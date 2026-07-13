"""Tests for apps/metric_governance/remediation.py."""

from __future__ import annotations

from apps.metric_governance.models import ChangeRiskCategory, SqlReviewFinding, SqlReviewResult
from apps.metric_governance.remediation import (
    derive_change_risk,
    derive_governance_recommendations,
    suggested_playbooks_for_review,
    trust_score_inputs_from_review,
)
from shared.models import MetricDefinition


def _result(**overrides) -> SqlReviewResult:
    defaults = dict(
        score=90,
        summary="Query looks strong.",
        findings=[],
        referenced_tables=["order_items"],
        recommended_next_steps=[],
    )
    defaults.update(overrides)
    return SqlReviewResult(**defaults)


# ---------------------------------------------------------------------------
# suggested_playbooks_for_review
# ---------------------------------------------------------------------------


def test_low_score_routes_through_a_steward_first():
    playbooks = suggested_playbooks_for_review(50, [])
    assert playbooks[0] == "Route the query through a steward or analytics lead before publishing."


def test_high_score_does_not_route_through_a_steward():
    playbooks = suggested_playbooks_for_review(95, [])
    assert "Route the query through a steward or analytics lead before publishing." not in playbooks


def test_approved_tables_finding_adds_a_resolve_gap_playbook():
    finding = SqlReviewFinding("high", "Approved Tables", "msg")
    playbooks = suggested_playbooks_for_review(90, [finding])
    assert any("Resolve table approval gaps" in p for p in playbooks)


def test_projection_finding_adds_a_select_star_playbook():
    finding = SqlReviewFinding("medium", "Projection", "msg")
    playbooks = suggested_playbooks_for_review(90, [finding])
    assert any("SELECT *" in p for p in playbooks)


def test_playbooks_are_capped_at_four():
    findings = [SqlReviewFinding("high", "Approved Tables", "m"), SqlReviewFinding("medium", "Projection", "m")]
    playbooks = suggested_playbooks_for_review(50, findings)
    assert len(playbooks) == 4


# ---------------------------------------------------------------------------
# trust_score_inputs_from_review
# ---------------------------------------------------------------------------


def test_clean_result_yields_full_coverage_and_no_mismatches():
    result = _result(referenced_tables=["order_items"], findings=[])
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["definition_mismatch_count"] == 0
    assert inputs["approved_table_coverage_ratio"] == 1.0
    assert inputs["has_declared_grain"] is True
    assert inputs["has_freshness_expectation"] is True
    assert inputs["high_severity_finding_count"] == 0
    assert inputs["medium_severity_finding_count"] == 0


def test_unapproved_referenced_table_counts_as_a_mismatch_and_lowers_coverage():
    result = _result(referenced_tables=["order_items", "unapproved_table"])
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["definition_mismatch_count"] == 1
    assert inputs["approved_table_coverage_ratio"] == 0.5


def test_empty_referenced_tables_defaults_coverage_to_full():
    result = _result(referenced_tables=[])
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["approved_table_coverage_ratio"] == 1.0
    assert inputs["definition_mismatch_count"] == 0


def test_critical_and_high_findings_both_count_as_high_severity():
    findings = [
        SqlReviewFinding("critical", "Join Logic", "m"),
        SqlReviewFinding("high", "Approved Tables", "m"),
    ]
    result = _result(findings=findings)
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["high_severity_finding_count"] == 2


def test_medium_findings_counted_separately_from_high():
    findings = [SqlReviewFinding("medium", "Filters", "m")]
    result = _result(findings=findings)
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["medium_severity_finding_count"] == 1
    assert inputs["high_severity_finding_count"] == 0


def test_grain_finding_means_grain_is_not_declared():
    findings = [SqlReviewFinding("medium", "Grain", "Aggregate logic should make the grain explicit.")]
    result = _result(findings=findings)
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["has_declared_grain"] is False


def test_filters_finding_means_freshness_expectation_is_not_declared():
    findings = [SqlReviewFinding("medium", "Filters", "Add business filters and freshness filters where required.")]
    result = _result(findings=findings)
    inputs = trust_score_inputs_from_review(result, approved_tables=["order_items"])
    assert inputs["has_freshness_expectation"] is False


# ---------------------------------------------------------------------------
# derive_change_risk
# ---------------------------------------------------------------------------


def _definition(**overrides) -> MetricDefinition:
    defaults = dict(
        name="revenue", owner="Analytics", description="Revenue", formula="SUM(sale_price)",
        measurement_grain="order_item", freshness_expectation="daily", certification_status="certified",
        approved_source_tables=["order_items"], version="v1-certified",
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


def _categories_by_name(categories: list[ChangeRiskCategory]) -> dict[str, ChangeRiskCategory]:
    return {c.category: c for c in categories}


def test_change_risk_returns_five_fixed_categories_in_order():
    result = _result(referenced_tables=["order_items"], findings=[])
    categories = derive_change_risk(result, _definition(), "healthy")
    assert [c.category for c in categories] == [
        "Calculation drift",
        "Source table mismatch",
        "Grain mismatch",
        "Filter/status mismatch",
        "Freshness/SLA mismatch",
    ]


def test_clean_review_is_aligned_on_every_category():
    result = _result(referenced_tables=["order_items"], findings=[])
    categories = derive_change_risk(result, _definition(), "healthy")
    assert all(c.status == "aligned" for c in categories)


def test_projection_finding_flags_calculation_drift_as_risk():
    finding = SqlReviewFinding("medium", "Projection", "Avoid SELECT * in governed metric SQL.")
    result = _result(findings=[finding])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "healthy"))
    assert by_name["Calculation drift"].status == "risk"
    assert "SELECT *" in by_name["Calculation drift"].detail


def test_unapproved_table_flags_source_table_mismatch_as_risk():
    result = _result(referenced_tables=["order_items", "unapproved_table"])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "healthy"))
    assert by_name["Source table mismatch"].status == "risk"
    assert "unapproved_table" in by_name["Source table mismatch"].detail


def test_no_referenced_tables_is_unknown_not_risk():
    result = _result(referenced_tables=[])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "healthy"))
    assert by_name["Source table mismatch"].status == "unknown"


def test_grain_finding_flags_grain_mismatch_as_risk():
    finding = SqlReviewFinding("medium", "Grain", "Aggregate logic should make the grain explicit.")
    result = _result(findings=[finding])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "healthy"))
    assert by_name["Grain mismatch"].status == "risk"


def test_uncertified_definition_flags_filter_status_mismatch_as_risk():
    result = _result(findings=[])
    by_name = _categories_by_name(derive_change_risk(result, _definition(certification_status="proposed"), "healthy"))
    assert by_name["Filter/status mismatch"].status == "risk"
    assert "proposed" in by_name["Filter/status mismatch"].detail


def test_degraded_source_status_flags_freshness_sla_as_risk():
    result = _result(findings=[])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "degraded"))
    assert by_name["Freshness/SLA mismatch"].status == "risk"


def test_unresolvable_source_status_is_unknown_not_risk():
    result = _result(findings=[])
    by_name = _categories_by_name(derive_change_risk(result, _definition(), "unknown"))
    assert by_name["Freshness/SLA mismatch"].status == "unknown"


# ---------------------------------------------------------------------------
# derive_governance_recommendations
# ---------------------------------------------------------------------------


def _clean_change_risk() -> list[ChangeRiskCategory]:
    result = _result(referenced_tables=["order_items"], findings=[])
    return derive_change_risk(result, _definition(), "healthy")


def test_high_trust_and_clean_review_recommends_approve():
    recs = derive_governance_recommendations(
        trust_band="high_trust", trust_score=95, review_score=95, findings=[],
        change_risk=_clean_change_risk(), definition=_definition(), source_status="healthy",
        active_incident_ids=[],
    )
    actions = [r.action for r in recs]
    assert "Approve" in actions
    assert "Block for executive reporting" not in actions
    assert "Needs review" not in actions


def test_do_not_rely_band_blocks_for_executive_reporting():
    findings = [SqlReviewFinding("critical", "Join Logic", "Join clauses should include explicit ON conditions.")]
    recs = derive_governance_recommendations(
        trust_band="do_not_rely", trust_score=20, review_score=40, findings=findings,
        change_risk=_clean_change_risk(), definition=_definition(), source_status="healthy",
        active_incident_ids=[],
    )
    blocking = next(r for r in recs if r.action == "Block for executive reporting")
    assert blocking.priority == "blocking"
    assert "Join Logic" in blocking.rationale


def test_review_required_band_recommends_needs_review():
    recs = derive_governance_recommendations(
        trust_band="review_required", trust_score=65, review_score=70, findings=[],
        change_risk=_clean_change_risk(), definition=_definition(), source_status="healthy",
        active_incident_ids=[],
    )
    assert any(r.action == "Needs review" and r.priority == "required" for r in recs)


def test_active_incidents_recommend_resolving_source_incident_as_blocking():
    recs = derive_governance_recommendations(
        trust_band="high_trust", trust_score=90, review_score=90, findings=[],
        change_risk=_clean_change_risk(), definition=_definition(), source_status="healthy",
        active_incident_ids=["inc-42"],
    )
    rec = next(r for r in recs if r.action == "Resolve source incident")
    assert rec.priority == "blocking"
    assert "inc-42" in rec.rationale


def test_uncertified_definition_recommends_updating_documentation():
    recs = derive_governance_recommendations(
        trust_band="high_trust", trust_score=90, review_score=90, findings=[],
        change_risk=_clean_change_risk(), definition=_definition(certification_status="proposed"),
        source_status="healthy", active_incident_ids=[],
    )
    assert any(r.action == "Update documentation" for r in recs)


def test_missing_owner_recommends_assigning_owner():
    recs = derive_governance_recommendations(
        trust_band="high_trust", trust_score=90, review_score=90, findings=[],
        change_risk=_clean_change_risk(), definition=_definition(owner=""), source_status="healthy",
        active_incident_ids=[],
    )
    assert any(r.action == "Assign owner" for r in recs)


def test_calculation_and_grain_risk_together_recommend_deprecation():
    result = _result(findings=[
        SqlReviewFinding("medium", "Projection", "Avoid SELECT * in governed metric SQL."),
        SqlReviewFinding("medium", "Grain", "Aggregate logic should make the grain explicit."),
    ])
    change_risk = derive_change_risk(result, _definition(), "healthy")
    recs = derive_governance_recommendations(
        trust_band="review_required", trust_score=60, review_score=60, findings=result.findings,
        change_risk=change_risk, definition=_definition(), source_status="healthy", active_incident_ids=[],
    )
    assert any(r.action == "Deprecate old definition" for r in recs)


def test_duplicate_action_labels_are_not_repeated():
    # An uncertified definition triggers "Update documentation" both from
    # its certification_status directly AND from change_risk's
    # Filter/status mismatch category (which also flags uncertified
    # status as risk) -- a genuine double-trigger, so this proves add()'s
    # dedupe actually collapses two independent code paths, not just an
    # absence of overlap.
    definition = _definition(certification_status="proposed", owner="")
    result = _result(findings=[])
    change_risk = derive_change_risk(result, definition, "healthy")
    recs = derive_governance_recommendations(
        trust_band="high_trust", trust_score=90, review_score=90, findings=[],
        change_risk=change_risk, definition=definition, source_status="healthy", active_incident_ids=[],
    )
    actions = [r.action for r in recs]
    assert actions.count("Update documentation") == 1
    assert len(actions) == len(set(actions))
