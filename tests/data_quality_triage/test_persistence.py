"""Tests for apps/data_quality_triage/persistence.py (Phase 6D)."""

from __future__ import annotations

from apps.data_quality_triage.checks import build_audit_event_for_incident
from apps.data_quality_triage.models import TableFinding
from apps.data_quality_triage.persistence import (
    IncidentPersistOutcome,
    persist_confirmed_incidents,
    promote_schema_baseline_now,
    read_schema_baseline,
)
from shared.config import PlatformConfig
from shared.models import Incident
from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient


def _incident(incident_id: str = "ds.tbl.check.2026-07-12T00:00:00Z") -> Incident:
    return Incident(
        incident_id=incident_id,
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


def test_persist_confirmed_incidents_persists_and_audits_each_one():
    incident = _incident()
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [{"incident_id": incident.incident_id, "dataset": "ds", "table_id": "tbl", "check_type": "check", "severity": "high", "status": "open", "row_version": 1}],
            [{"event_id": f"incident_created.{incident.incident_id}", "event_type": "incident_created", "subject": incident.incident_id, "outcome": "incident_created"}],
        ]
    )

    outcomes = persist_confirmed_incidents(client, [incident], actor="test-actor", build_audit_event=_build_event)

    assert outcomes == [IncidentPersistOutcome(incident_id=incident.incident_id, persisted=True, created=True, error=None)]


def test_persist_confirmed_incidents_never_substitutes_sample_data_on_failure():
    incident = _incident()
    client = SequencedFakeBigQueryClient(rows_per_call=[])
    client.query_exception = RuntimeError("no loupe_platform.incidents table")

    outcomes = persist_confirmed_incidents(client, [incident], actor="test-actor", build_audit_event=_build_event)

    assert len(outcomes) == 1
    assert outcomes[0].persisted is False
    assert outcomes[0].error is not None
    # The failure is reported honestly per-incident -- nothing here ever
    # returns a fabricated "success" or a substitute incident.


def test_persist_confirmed_incidents_one_bad_incident_does_not_block_the_rest():
    good = _incident("ds.tbl.check_a.2026-07-12T00:00:00Z")
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [{"incident_id": good.incident_id, "dataset": "ds", "table_id": "tbl", "check_type": "check_a", "severity": "high", "status": "open", "row_version": 1}],
            [{"event_id": f"incident_created.{good.incident_id}", "event_type": "incident_created", "subject": good.incident_id, "outcome": "incident_created"}],
        ]
    )
    bad = _incident("ds.tbl.check_b.2026-07-12T00:00:00Z")
    # Only two calls queued -- once exhausted, a third call would reuse
    # the second queued shape (event row), which lacks incident fields
    # and will raise a KeyError inside create_incident()'s conflict check,
    # simulating "this second incident's persistence attempt failed."
    outcomes = persist_confirmed_incidents(client, [good, bad], actor="test-actor", build_audit_event=_build_event)

    assert len(outcomes) == 2
    assert outcomes[0].persisted is True
    assert outcomes[1].persisted is False


def test_read_schema_baseline_returns_none_when_persistence_unavailable(fake_client):
    fake_client.query_exception = RuntimeError("no schema_baselines table")
    result = read_schema_baseline(fake_client, dataset="ds", table_id="tbl")
    assert result is None


def test_read_schema_baseline_adapts_persisted_row_into_app_local_snapshot(fake_client):
    fake_client.next_rows = [
        {
            "dataset": "ds",
            "table_id": "tbl",
            "columns": [{"name": "id", "field_type": "STRING"}],
            "source_snapshot_id": "snap_1",
            "promoted_at": "2026-07-12T00:00:00Z",
            "promoted_by": "triage-bot",
        }
    ]
    result = read_schema_baseline(fake_client, dataset="ds", table_id="tbl")
    assert result is not None
    assert result.table_id == "tbl"
    assert result.columns == {"id": "STRING"}
    assert result.captured_at == "2026-07-12T00:00:00Z"


def test_promote_schema_baseline_now_is_a_thin_wrapper(fake_client):
    fake_client.next_rows = [
        {"dataset": "ds", "table_id": "tbl", "source_snapshot_id": "snap_1", "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "triage-bot"}
    ]
    result = promote_schema_baseline_now(
        fake_client,
        dataset="ds",
        table_id="tbl",
        columns={"id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="triage-bot",
        event_id="evt_promo_1",
        event_timestamp="2026-07-12T00:00:00Z",
    )
    assert result.dataset == "ds"
    assert result.table_id == "tbl"


def test_app_persistence_passes_explicit_dataset_to_incident_audit_and_baseline_paths():
    config = PlatformConfig(project="test-project", dataset="loupe_platform_test")
    incident = _incident()
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [{"incident_id": incident.incident_id, "dataset": "ds", "table_id": "tbl", "check_type": "check", "severity": "high", "status": "open", "row_version": 1}],
            [{"event_id": f"incident_created.{incident.incident_id}", "event_type": "incident_created", "subject": incident.incident_id, "outcome": "incident_created"}],
            [{"dataset": "ds", "table_id": "tbl", "source_snapshot_id": "snap_1", "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "tester"}],
        ]
    )

    persist_confirmed_incidents(
        client,
        [incident],
        actor="tester",
        build_audit_event=_build_event,
        config=config,
    )
    promote_schema_baseline_now(
        client,
        dataset="ds",
        table_id="tbl",
        columns={"id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="tester",
        event_id="evt_baseline_1",
        event_timestamp="2026-07-12T00:00:00Z",
        config=config,
    )

    combined_sql = "\n".join(sql for sql, _ in client.queries)
    assert "`loupe_platform_test.incidents`" in combined_sql
    assert "`loupe_platform_test.audit_events`" in combined_sql
    assert "`loupe_platform_test.schema_baselines`" in combined_sql
    assert "`loupe_platform_test.write_locks`" in combined_sql
    assert "`loupe_platform.incidents`" not in combined_sql
