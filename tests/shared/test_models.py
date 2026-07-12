"""Tests for shared/models.py -- the cross-app data contracts.

These tests exist to catch two categories of bug: a dataclass accepting a
value outside its documented vocabulary, and a required field being made
optional (or vice versa) by accident during future refactors.
"""

from __future__ import annotations

import pytest

from shared.models import (
    AuditEvent,
    Incident,
    MetricCatalogPointer,
    MetricDefinition,
    MetricVersion,
    SourceHealth,
    TrustScoreFactor,
    TrustScoreResult,
)


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------


def _make_incident(**overrides):
    defaults = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="null_spike",
        severity="high",
        status="detected",
    )
    defaults.update(overrides)
    return Incident(**defaults)


def test_incident_constructs_with_required_fields_only():
    incident = _make_incident()
    assert incident.incident_id == "inc_1"
    assert incident.affected_metrics == []
    assert incident.affected_dashboards == []
    assert incident.owner is None
    assert incident.sql_template is None
    assert incident.query_hash is None


def test_incident_sql_template_and_query_hash_are_independent_optional_fields():
    incident = _make_incident(sql_template="SELECT COUNT(*) FROM `orders`", query_hash="abc123")
    assert incident.sql_template == "SELECT COUNT(*) FROM `orders`"
    assert incident.query_hash == "abc123"


def test_incident_rejects_invalid_severity():
    with pytest.raises(ValueError):
        _make_incident(severity="catastrophic")


def test_incident_rejects_invalid_status():
    with pytest.raises(ValueError):
        _make_incident(status="closed")


def test_incident_accepts_all_documented_statuses():
    for status in (
        "detected",
        "open",
        "acknowledged",
        "investigating",
        "mitigated",
        "resolved",
    ):
        assert _make_incident(status=status).status == status


def test_incident_optional_fields_default_independently():
    a = _make_incident()
    a.affected_metrics.append("revenue")
    b = _make_incident()
    assert b.affected_metrics == [], "default_factory list must not be shared between instances"


def test_incident_recurrence_link_defaults_to_none():
    incident = _make_incident()
    assert incident.recurrence_of_incident_id is None


def test_incident_can_be_linked_to_a_prior_resolved_incident():
    new_occurrence = _make_incident(
        incident_id="inc_2",
        status="detected",
        recurrence_of_incident_id="inc_1",
    )
    assert new_occurrence.recurrence_of_incident_id == "inc_1"


# ---------------------------------------------------------------------------
# MetricDefinition
# ---------------------------------------------------------------------------


def _make_definition(**overrides):
    defaults = dict(
        name="return_rate",
        owner="analytics-team",
        description="Returned order items over eligible fulfilled order items.",
        formula="SUM(returned_items) / SUM(fulfilled_items)",
        measurement_grain="one row per day per category",
        freshness_expectation="daily by 06:00 UTC",
        certification_status="pending_validation",
        approved_source_tables=["order_items"],
        version="v1",
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


def test_metric_definition_defaults_to_uncertified_state_ok():
    definition = _make_definition(certification_status="proposed")
    assert definition.certification_status == "proposed"


def test_metric_definition_rejects_invalid_certification_status():
    with pytest.raises(ValueError):
        _make_definition(certification_status="approved")


def test_metric_definition_accepts_certified():
    definition = _make_definition(certification_status="certified")
    assert definition.certification_status == "certified"


# ---------------------------------------------------------------------------
# MetricVersion (Phase 6 amendment 4: separate history-record model,
# distinct from MetricDefinition's resolved current-state shape)
# ---------------------------------------------------------------------------


def _make_metric_version(**overrides):
    defaults = dict(
        name="revenue",
        version="v1-extracted",
        description="Total booked revenue.",
        formula="SUM(order_items.sale_price)",
        measurement_grain="order_item",
        freshness_expectation="undeclared",
        certification_status="pending_validation",
        approved_source_tables=["order_items"],
        content_hash="a" * 64,
        created_by="loupe-agent-team",
        created_at="2026-07-11T00:00:00Z",
        change_reason="initial seed",
    )
    defaults.update(overrides)
    return MetricVersion(**defaults)


def test_metric_version_constructs_with_required_fields_only():
    version = _make_metric_version()
    assert version.reviewer is None
    assert version.reviewed_at is None
    assert version.prior_version is None
    assert version.validation_evidence is None


def test_metric_version_rejects_invalid_certification_status():
    with pytest.raises(ValueError):
        _make_metric_version(certification_status="approved")


def test_metric_version_created_by_and_reviewer_are_independent_fields():
    # Per amendment 5/8: created_by (who authored this version's content)
    # and reviewer (who certified it, if anyone yet has) must never be
    # conflated -- a version can exist with one set and not the other.
    version = _make_metric_version(created_by="analyst-a", reviewer=None)
    assert version.created_by == "analyst-a"
    assert version.reviewer is None

    certified = _make_metric_version(
        created_by="analyst-a",
        reviewer="reviewer-b",
        reviewed_at="2026-07-12T00:00:00Z",
        change_reason="certification review",
        prior_version="v1-extracted",
    )
    assert certified.created_by == "analyst-a"
    assert certified.reviewer == "reviewer-b"
    assert certified.created_by != certified.reviewer


def test_metric_version_certification_without_content_change_keeps_same_hash():
    # Approved decision (amendment 5): certifying a version without
    # changing its content creates a NEW MetricVersion row with the SAME
    # content_hash and prior_version populated.
    original = _make_metric_version(content_hash="b" * 64)
    certification = _make_metric_version(
        content_hash="b" * 64,
        prior_version=original.version,
        reviewer="reviewer-b",
        reviewed_at="2026-07-12T00:00:00Z",
        change_reason="certification review",
        validation_evidence="Reviewed against docs/contracts.md certification bar.",
    )
    assert certification.content_hash == original.content_hash
    assert certification.prior_version == original.version


# ---------------------------------------------------------------------------
# MetricCatalogPointer (Phase 6 amendment 4: current-state pointer row)
# ---------------------------------------------------------------------------


def test_metric_catalog_pointer_constructs_with_required_fields_only():
    pointer = MetricCatalogPointer(
        name="revenue",
        current_version="v1-extracted",
        owner="loupe-agent-team",
        certification_status="pending_validation",
        updated_at="2026-07-11T00:00:00Z",
    )
    assert pointer.last_reviewed_at is None


def test_metric_catalog_pointer_rejects_invalid_certification_status():
    with pytest.raises(ValueError):
        MetricCatalogPointer(
            name="revenue",
            current_version="v1",
            owner="team",
            certification_status="approved",
            updated_at="t",
        )


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


def test_audit_event_constructs_and_defaults_context_to_empty_dict():
    event = AuditEvent(
        event_id="evt_1",
        timestamp="2026-07-11T00:00:00Z",
        actor="jovanne",
        event_type="sql_review_submitted",
        subject="review_id:abc123",
        outcome="completed",
    )
    assert event.context == {}


def test_audit_event_context_not_shared_between_instances():
    a = AuditEvent(
        event_id="evt_1",
        timestamp="t",
        actor="a",
        event_type="t",
        subject="s",
        outcome="o",
    )
    a.context["key"] = "value"
    b = AuditEvent(
        event_id="evt_2",
        timestamp="t",
        actor="a",
        event_type="t",
        subject="s",
        outcome="o",
    )
    assert b.context == {}


# ---------------------------------------------------------------------------
# SourceHealth
# ---------------------------------------------------------------------------


def test_source_health_rejects_invalid_status():
    with pytest.raises(ValueError):
        SourceHealth(dataset="thelook_ecommerce", table_id="orders", status="broken")


def test_source_health_accepts_documented_statuses():
    for status in ("healthy", "degraded", "critical"):
        health = SourceHealth(dataset="thelook_ecommerce", table_id="orders", status=status)
        assert health.status == status
        assert health.active_incident_ids == []


# ---------------------------------------------------------------------------
# TrustScoreResult
# ---------------------------------------------------------------------------


def test_trust_score_result_rejects_invalid_band():
    with pytest.raises(ValueError):
        TrustScoreResult(score=90, band="totally_fine", scoring_version="v1")


def test_trust_score_result_rejects_out_of_range_score():
    with pytest.raises(ValueError):
        TrustScoreResult(score=150, band="high_trust", scoring_version="v1")
    with pytest.raises(ValueError):
        TrustScoreResult(score=-1, band="high_trust", scoring_version="v1")


def test_trust_score_result_holds_itemized_factors():
    result = TrustScoreResult(
        score=72,
        band="review_required",
        scoring_version="v1",
        factors=[
            TrustScoreFactor(name="definition_certified", points=20, reason="Metric is certified."),
            TrustScoreFactor(name="active_incident", points=-15, reason="Source has an open incident."),
        ],
    )
    assert len(result.factors) == 2
    assert result.factors[1].points == -15
