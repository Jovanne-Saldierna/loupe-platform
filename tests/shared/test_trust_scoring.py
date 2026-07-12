"""Tests for shared/trust_scoring.py -- the single deterministic,
versioned trust-scoring function.
"""

from __future__ import annotations

from shared.models import MetricDefinition, SourceHealth
from shared.trust_scoring import SCORING_VERSION, _band_for_score, compute_trust_score


def _certified_definition(**overrides) -> MetricDefinition:
    defaults = dict(
        name="return_rate",
        owner="analytics-team",
        description="d",
        formula="f",
        measurement_grain="g",
        freshness_expectation="daily",
        certification_status="certified",
        approved_source_tables=["order_items"],
        version="v1",
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


def _healthy_source(**overrides) -> SourceHealth:
    defaults = dict(dataset="thelook_ecommerce", table_id="order_items", status="healthy")
    defaults.update(overrides)
    return SourceHealth(**defaults)


# ---------------------------------------------------------------------------
# Version and factor shape
# ---------------------------------------------------------------------------


def test_result_always_reports_scoring_version():
    result = compute_trust_score(definition=None, source_health=None)
    assert result.scoring_version == SCORING_VERSION


def test_result_has_exactly_seven_itemized_factors():
    result = compute_trust_score(definition=_certified_definition(), source_health=_healthy_source())
    assert len(result.factors) == 7
    assert {f.name for f in result.factors} == {
        "metric_certification",
        "source_health",
        "definition_mismatches",
        "review_findings",
        "approved_table_coverage",
        "grain_declared",
        "freshness_declared",
    }


# ---------------------------------------------------------------------------
# Best / worst case and clamping
# ---------------------------------------------------------------------------


def test_perfect_inputs_score_100_and_high_trust():
    result = compute_trust_score(
        definition=_certified_definition(),
        source_health=_healthy_source(),
        definition_mismatch_count=0,
        high_severity_finding_count=0,
        medium_severity_finding_count=0,
        approved_table_coverage_ratio=1.0,
        has_declared_grain=True,
        has_freshness_expectation=True,
    )
    assert result.score == 100
    assert result.band == "high_trust"


def test_worst_case_inputs_clamp_to_zero_not_negative():
    result = compute_trust_score(
        definition=None,
        source_health=SourceHealth(dataset="d", table_id="t", status="critical", active_incident_ids=["i1"]),
        definition_mismatch_count=5,
        high_severity_finding_count=3,
        medium_severity_finding_count=0,
        approved_table_coverage_ratio=0.0,
        has_declared_grain=False,
        has_freshness_expectation=False,
    )
    assert result.score == 0
    assert result.band == "do_not_rely"


# ---------------------------------------------------------------------------
# Forced "do_not_rely" overrides -- must win regardless of arithmetic total
# ---------------------------------------------------------------------------


def test_critical_source_health_forces_do_not_rely_even_with_high_arithmetic_score():
    result = compute_trust_score(
        definition=_certified_definition(),
        source_health=SourceHealth(dataset="d", table_id="t", status="critical", active_incident_ids=["i1"]),
        definition_mismatch_count=0,
        high_severity_finding_count=0,
        medium_severity_finding_count=0,
        approved_table_coverage_ratio=1.0,
        has_declared_grain=True,
        has_freshness_expectation=True,
    )
    # Arithmetic total here is 30 - 20 + 15 + 15 + 5 + 3 + 2 = 50, which
    # would otherwise land in "review_required" -- the override must win.
    assert result.band == "do_not_rely"
    # The override must NEVER silently rewrite the arithmetic score to
    # match the forced band -- score stays the raw, honest total.
    assert result.score == 50
    # And the disagreement between score and band must be explained.
    assert result.override_reason is not None
    assert "source health is critical" in result.override_reason
    assert "50" in result.override_reason


def test_high_severity_finding_forces_do_not_rely_even_with_healthy_source():
    result = compute_trust_score(
        definition=_certified_definition(),
        source_health=_healthy_source(),
        definition_mismatch_count=0,
        high_severity_finding_count=1,
        medium_severity_finding_count=0,
        approved_table_coverage_ratio=1.0,
        has_declared_grain=True,
        has_freshness_expectation=True,
    )
    # Arithmetic total: 30 + 30 - 15 + 15 + 5 + 3 + 2 = 70, would otherwise
    # be "review_required" -- the override must still force do_not_rely.
    assert result.band == "do_not_rely"
    assert result.score == 70
    assert result.override_reason is not None
    assert "high-severity review finding" in result.override_reason
    assert "70" in result.override_reason


def test_override_reason_is_none_when_no_override_fires():
    result = compute_trust_score(definition=_certified_definition(), source_health=_healthy_source())
    assert result.override_reason is None


def test_override_reason_reports_both_causes_when_both_fire_simultaneously():
    result = compute_trust_score(
        definition=_certified_definition(),
        source_health=SourceHealth(dataset="d", table_id="t", status="critical", active_incident_ids=["i1"]),
        high_severity_finding_count=2,
    )
    assert result.band == "do_not_rely"
    assert result.override_reason is not None
    assert "source health is critical" in result.override_reason
    assert "2 high-severity review finding(s)" in result.override_reason


def test_degraded_non_critical_source_does_not_trigger_the_override():
    result = compute_trust_score(
        definition=_certified_definition(),
        source_health=SourceHealth(dataset="d", table_id="t", status="degraded", active_incident_ids=["i1"]),
    )
    assert result.band != "do_not_rely" or result.score < 50  # only via arithmetic, not override
    # Explicitly: no high-severity findings and no critical source, so the
    # override must not have fired.
    assert not (result.score >= 50 and result.band == "do_not_rely")


def test_missing_source_health_scores_zero_not_healthy():
    # A caller that has no live source-health data (source_health=None) --
    # e.g. Metric Governance's SQL Review page, which has no incident
    # persistence wired up yet -- must never be silently treated the same
    # as a checked-and-healthy table. None is an explicit "unknown" signal
    # and must score the same 0 points as _SOURCE_UNKNOWN_POINTS, never
    # the +30 a genuinely healthy SourceHealth would earn.
    result = compute_trust_score(definition=_certified_definition(), source_health=None)
    source_health_factor = next(f for f in result.factors if f.name == "source_health")
    assert source_health_factor.points == 0
    assert "No source-health data available" in source_health_factor.reason


def test_missing_source_health_scores_the_same_as_no_definition_case():
    # Sanity check that the "unknown" score (0) is well below what a
    # healthy source would earn, so this can never be mistaken for an
    # implicit healthy assumption.
    unknown_result = compute_trust_score(definition=_certified_definition(), source_health=None)
    healthy_result = compute_trust_score(definition=_certified_definition(), source_health=_healthy_source())
    unknown_factor = next(f for f in unknown_result.factors if f.name == "source_health")
    healthy_factor = next(f for f in healthy_result.factors if f.name == "source_health")
    assert unknown_factor.points < healthy_factor.points
    assert unknown_factor.points == 0


# ---------------------------------------------------------------------------
# Band thresholds (boundary precision)
# ---------------------------------------------------------------------------


def test_band_threshold_boundaries():
    assert _band_for_score(85) == "high_trust"
    assert _band_for_score(84) == "review_required"
    assert _band_for_score(50) == "review_required"
    assert _band_for_score(49) == "do_not_rely"
    assert _band_for_score(0) == "do_not_rely"
    assert _band_for_score(100) == "high_trust"


# ---------------------------------------------------------------------------
# Individual factor sanity checks
# ---------------------------------------------------------------------------


def test_missing_definition_scores_zero_certification_points():
    result = compute_trust_score(definition=None, source_health=_healthy_source())
    cert_factor = next(f for f in result.factors if f.name == "metric_certification")
    assert cert_factor.points == 0


def test_pending_validation_definition_scores_fewer_points_than_certified():
    pending_result = compute_trust_score(
        definition=_certified_definition(certification_status="pending_validation"),
        source_health=_healthy_source(),
    )
    certified_result = compute_trust_score(
        definition=_certified_definition(certification_status="certified"),
        source_health=_healthy_source(),
    )
    pending_factor = next(f for f in pending_result.factors if f.name == "metric_certification")
    certified_factor = next(f for f in certified_result.factors if f.name == "metric_certification")
    assert pending_factor.points < certified_factor.points


def test_missing_grain_and_freshness_reduce_score_relative_to_declared():
    with_both = compute_trust_score(
        definition=_certified_definition(), source_health=_healthy_source(),
        has_declared_grain=True, has_freshness_expectation=True,
    )
    with_neither = compute_trust_score(
        definition=_certified_definition(), source_health=_healthy_source(),
        has_declared_grain=False, has_freshness_expectation=False,
    )
    assert with_neither.score < with_both.score
