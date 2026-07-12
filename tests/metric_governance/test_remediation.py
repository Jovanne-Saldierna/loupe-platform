"""Tests for apps/metric_governance/remediation.py."""

from __future__ import annotations

from apps.metric_governance.models import SqlReviewFinding, SqlReviewResult
from apps.metric_governance.remediation import (
    suggested_playbooks_for_review,
    trust_score_inputs_from_review,
)


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
