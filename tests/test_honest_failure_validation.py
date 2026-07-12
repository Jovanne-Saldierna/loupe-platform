"""Phase 6E: honest-failure validation.

Confirms all three apps -- and the persistence-layer functions they're
built on -- remain honest (never fabricate sample data, never report a
fake healthy/certified/persisted state) under four failure modes:
persistence unavailable, catalog reads fail, incident reads fail, and a
write fails.

Most of this ground is already covered by tests/test_cross_app_workflow.py
(persistence-unavailable across all three apps) and by
tests/data_quality_triage/test_persistence.py /
tests/metric_governance/test_persistence.py (individual read/write
failure honesty for each module). This file closes the specific gaps
Phase 6E's explicit "a write fails" requirement calls out that were not
yet covered by a focused test: a write that partially succeeds (incident
persisted, its accompanying audit event write fails), a governed
certification write failure, and a schema-baseline promotion write
failure -- in every case, the caller must see a real, honest outcome
(an error recorded per-item, or a real exception propagating), never a
silently-fabricated success.
"""

from __future__ import annotations

import pytest

from apps.data_quality_triage.checks import build_audit_event_for_incident
from apps.data_quality_triage.models import TableFinding
from apps.data_quality_triage.persistence import persist_confirmed_incidents, promote_schema_baseline_now
from apps.metric_governance.persistence import certify_definition
from shared.models import Incident
from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient
from tests.shared.conftest import FakeBigQueryClient, fake_client


def _incident() -> Incident:
    return Incident(
        incident_id="ds.tbl.check.2026-07-12T00:00:00Z",
        created_at="2026-07-12T00:00:00Z",
        dataset="ds",
        table_id="tbl",
        check_type="check",
        severity="high",
        status="open",
    )


def _build_event(incident: Incident):
    finding = TableFinding(
        table_id=incident.table_id,
        check_name=incident.check_type,
        status="fail",
        severity="high",
        observed_value=incident.observed_value,
        threshold=incident.expected_value,
        summary="test finding",
        likely_root_cause="test",
    )
    return build_audit_event_for_incident(
        incident,
        finding,
        event_id=f"incident_created.{incident.incident_id}",
        timestamp="2026-07-12T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# 1. A write fails partway: the incident's own INSERT commits, but its
#    accompanying audit-event write fails. This must be reported as an
#    honest, partially-succeeded outcome -- never a silent full success,
#    and never a rollback that pretends the incident was never persisted
#    (create_incident() and write_event_idempotent() are two separate
#    execute_transaction() calls in persist_confirmed_incidents(), not
#    one atomic unit, so this partial-success shape is expected and must
#    be surfaced honestly, not hidden).
# ---------------------------------------------------------------------------


def test_incident_write_succeeds_but_audit_write_fails_is_reported_honestly_not_as_full_success():
    incident = _incident()
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [
                {
                    "incident_id": incident.incident_id,
                    "dataset": "ds",
                    "table_id": "tbl",
                    "check_type": "check",
                    "severity": "high",
                    "status": "open",
                    "row_version": 1,
                }
            ],
        ]
    )
    # The queue is exhausted after the incident insert; force every
    # subsequent call (the audit-event write) to fail outright, simulating
    # "the incident committed, but the follow-up audit write failed."
    real_query = client.query
    calls = {"n": 0}

    def _flaky_query(sql, job_config=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("audit_events write failed")
        return real_query(sql, job_config=job_config)

    client.query = _flaky_query  # type: ignore[method-assign]

    outcomes = persist_confirmed_incidents(client, [incident], actor="test-actor", build_audit_event=_build_event)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    # Honest: the incident itself really did persist...
    assert outcome.persisted is True
    # ...but this is NOT reported as an unqualified success -- the audit
    # failure is visible in the error field, never silently dropped.
    assert outcome.error is not None
    assert "audit event failed" in outcome.error


# ---------------------------------------------------------------------------
# 2. A governed certification write fails: certify_definition() must let
#    the real exception propagate -- never catch it and report a fake
#    MetricCertificationResult, which would be indistinguishable from an
#    actual certification having happened.
# ---------------------------------------------------------------------------


def test_certify_definition_write_failure_propagates_never_fabricates_a_certification_result(fake_client):
    fake_client.query_exception = RuntimeError("metric_versions write failed")

    with pytest.raises(RuntimeError):
        certify_definition(
            fake_client,
            name="revenue",
            new_version="v2-certified",
            expected_current_version="v1-extracted",
            description="Total booked revenue.",
            formula="SUM(order_items.sale_price)",
            measurement_grain="order_item",
            freshness_expectation="undeclared",
            approved_source_tables=["order_items", "orders", "products"],
            created_by="reviewer-bot",
            reviewer="reviewer-bot",
            validation_evidence="cross-checked",
            reviewed_at="2026-07-12T00:00:00Z",
            change_reason="first certification",
            event_id="evt_cert_revenue_v2",
            require_separation_of_duties=False,
        )
    # Nothing was ever queried successfully -- the failure happened on
    # the very first (and only) attempt, so no partial write landed.
    assert fake_client.queries == []


# ---------------------------------------------------------------------------
# 3. A schema-baseline promotion write fails: promote_schema_baseline_now()
#    must let the real exception propagate -- never return a fabricated
#    SchemaBaselinePromotionResult that would make Triage's UI believe a
#    baseline was promoted when it was not.
# ---------------------------------------------------------------------------


def test_promote_schema_baseline_write_failure_propagates_never_fabricates_a_promotion_result(fake_client):
    fake_client.query_exception = RuntimeError("schema_baselines write failed")

    with pytest.raises(RuntimeError):
        promote_schema_baseline_now(
            fake_client,
            dataset="ds",
            table_id="tbl",
            columns={"id": "STRING"},
            source_snapshot_id="snap_1",
            promoted_by="triage-bot",
            event_id="evt_promo_1",
            event_timestamp="2026-07-12T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# 4. Incident reads fail (distinct from catalog reads / general
#    persistence-unavailable): Governance's evidence trail must degrade to
#    "no evidence available," never a fabricated healthy/empty-but-clean
#    result that would make a metric look more trustworthy than its
#    actual, unknown state.
# ---------------------------------------------------------------------------


def test_incident_read_failure_degrades_governance_evidence_honestly_not_as_clean(fake_client):
    from apps.metric_governance.persistence import source_health_for_definition
    from shared.metric_catalog import get_definition

    fake_client.query_exception = RuntimeError("incidents table unreachable")

    evidence = source_health_for_definition(fake_client, get_definition("revenue"))

    assert evidence.worst_health is None
    assert evidence.table_health == []
    assert evidence.active_incidents == []
