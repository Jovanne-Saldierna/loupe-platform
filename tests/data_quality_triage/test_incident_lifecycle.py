"""Tests for apps/data_quality_triage/incident_lifecycle.py.

Uses the shared FakeBigQueryClient (tests/shared/conftest.py) -- these
functions are thin wrappers over shared.data_service, so the same fake
that proves shared.data_service's contract also proves this module's.
"""

from __future__ import annotations

import pytest

from apps.data_quality_triage.incident_lifecycle import (
    LifecycleTransitionOutcome,
    LivePersistenceUnavailableError,
    acknowledge_incident,
    begin_investigation,
    fetch_incident,
    list_open_incidents,
    mark_mitigated,
    next_allowed_statuses,
    reopen_incident,
    resolve_incident,
    source_health_for,
)
from shared.config import PlatformConfig
from shared.data_service import ConcurrentModificationError, IncidentNotFoundError
from shared.incidents import InvalidTransitionError
from shared.persistence_transactions import ConcurrentModificationError as PersistedConcurrentModificationError


def _incident_row(**overrides) -> dict:
    row = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="null_ratio",
        severity="high",
        status="open",
        observed_value=None,
        expected_value=None,
        sql_template=None,
        query_hash=None,
        affected_metrics=[],
        affected_dashboards=[],
        playbook=None,
        owner=None,
        acknowledged_at=None,
        resolved_at=None,
        resolution_notes=None,
        rule_version=None,
        recurrence_of_incident_id=None,
    )
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_fetch_incident_returns_none_when_not_found(fake_client):
    fake_client.next_rows = []
    assert fetch_incident(fake_client, "missing") is None


def test_fetch_incident_returns_the_incident(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1")]
    incident = fetch_incident(fake_client, "inc_1")
    assert incident is not None
    assert incident.incident_id == "inc_1"


def test_list_open_incidents_delegates_to_shared_active_status_filtering(fake_client):
    fake_client.next_rows = [
        _incident_row(incident_id="inc_open", status="open"),
        _incident_row(incident_id="inc_resolved", status="resolved"),
    ]
    open_incidents = list_open_incidents(fake_client, "thelook_ecommerce", "order_items")
    assert {i.incident_id for i in open_incidents} == {"inc_open"}


def test_source_health_for_is_healthy_with_no_active_incidents(fake_client):
    fake_client.next_rows = []
    health = source_health_for(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "healthy"


def test_next_allowed_statuses_matches_shared_allowed_transitions():
    assert next_allowed_statuses("detected") == ["open"]
    assert next_allowed_statuses("investigating") == sorted({"mitigated", "resolved"})
    assert next_allowed_statuses("resolved") == ["open"]


def test_next_allowed_statuses_never_touches_a_client():
    # No client argument at all -- this is a pure lookup, unlike every
    # other function in this module.
    assert next_allowed_statuses("open") == ["acknowledged"]


# ---------------------------------------------------------------------------
# Transitions -- each wrapper delegates correctly
# ---------------------------------------------------------------------------


def test_acknowledge_incident_transitions_open_to_acknowledged(fake_client):
    fake_client.next_rows = [_incident_row(status="open")]
    updated = acknowledge_incident(fake_client, "inc_1")
    assert updated.status == "acknowledged"


def test_begin_investigation_transitions_acknowledged_to_investigating(fake_client):
    fake_client.next_rows = [_incident_row(status="acknowledged")]
    updated = begin_investigation(fake_client, "inc_1")
    assert updated.status == "investigating"


def test_mark_mitigated_transitions_investigating_to_mitigated(fake_client):
    fake_client.next_rows = [_incident_row(status="investigating")]
    updated = mark_mitigated(fake_client, "inc_1")
    assert updated.status == "mitigated"


def test_resolve_incident_transitions_and_sets_resolution_notes(fake_client):
    fake_client.next_rows = [_incident_row(status="mitigated")]
    updated = resolve_incident(fake_client, "inc_1", resolution_notes="Backfill completed.")
    assert updated.status == "resolved"
    assert updated.resolution_notes == "Backfill completed."


def test_reopen_incident_transitions_resolved_to_open(fake_client):
    fake_client.next_rows = [_incident_row(status="resolved")]
    updated = reopen_incident(fake_client, "inc_1")
    assert updated.status == "open"


# ---------------------------------------------------------------------------
# Known application errors propagate unchanged, never wrapped
# ---------------------------------------------------------------------------


def test_invalid_transition_error_propagates_unchanged(fake_client):
    fake_client.next_rows = [_incident_row(status="detected")]
    with pytest.raises(InvalidTransitionError):
        acknowledge_incident(fake_client, "inc_1")


def test_incident_not_found_error_propagates_unchanged(fake_client):
    fake_client.next_rows = []
    with pytest.raises(IncidentNotFoundError):
        acknowledge_incident(fake_client, "does_not_exist")


def test_concurrent_modification_error_propagates_unchanged(fake_client):
    fake_client.next_rows = [_incident_row(status="acknowledged")]
    with pytest.raises(ConcurrentModificationError):
        begin_investigation(fake_client, "inc_1", expected_current_status="open")


# ---------------------------------------------------------------------------
# Unknown/unexpected errors are wrapped as LivePersistenceUnavailableError
# -- the "not connected yet" contract
# ---------------------------------------------------------------------------


def test_unexpected_query_failure_is_wrapped_as_persistence_unavailable(fake_client):
    fake_client.query_exception = RuntimeError("Table loupe_platform.incidents not found")
    with pytest.raises(LivePersistenceUnavailableError) as excinfo:
        fetch_incident(fake_client, "inc_1")
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_unexpected_query_failure_is_wrapped_for_list_open_incidents(fake_client):
    fake_client.query_exception = RuntimeError("boom")
    with pytest.raises(LivePersistenceUnavailableError):
        list_open_incidents(fake_client, "thelook_ecommerce", "order_items")


def test_unexpected_query_failure_is_wrapped_for_source_health(fake_client):
    fake_client.query_exception = RuntimeError("boom")
    with pytest.raises(LivePersistenceUnavailableError):
        source_health_for(fake_client, "thelook_ecommerce", "order_items")


def test_unexpected_query_failure_is_wrapped_for_a_transition(fake_client):
    fake_client.query_exception = RuntimeError("boom")
    with pytest.raises(LivePersistenceUnavailableError):
        acknowledge_incident(fake_client, "inc_1")


# ---------------------------------------------------------------------------
# Persisted mode: the UI-facing functions actually call
# shared.incident_persistence.record_incident_transition() when
# mode="persisted" -- not shared.data_service.apply_incident_transition(),
# and not a second, ui.py-local implementation.
# ---------------------------------------------------------------------------


def _state_row(**overrides) -> dict:
    row = dict(incident_id="inc_1", status="open", row_version=1)
    row.update(overrides)
    return row


def test_acknowledge_incident_persisted_mode_returns_a_persisted_outcome():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [_state_row(status="open", row_version=1)],  # get_incident_state read
            [{"incident_id": "inc_1", "status": "acknowledged", "row_version": 2}],  # transition result
        ]
    )

    outcome = acknowledge_incident(
        client, "inc_1", expected_current_status="open", mode="persisted", actor="triage-bot"
    )

    assert isinstance(outcome, LifecycleTransitionOutcome)
    assert outcome.status == "acknowledged"
    assert outcome.persisted is True
    assert outcome.session_only is False
    assert outcome.row_version == 2


def test_persisted_mode_transition_actually_issues_an_update_against_the_incidents_table():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [_state_row(status="open", row_version=1)],
            [{"incident_id": "inc_1", "status": "acknowledged", "row_version": 2}],
        ]
    )

    acknowledge_incident(client, "inc_1", expected_current_status="open", mode="persisted", actor="triage-bot")

    # First call is get_incident_state's SELECT; second is the
    # TRANSITION_INCIDENT_STATUS_TXN script -- proving the persisted path
    # actually reaches shared.incident_persistence.record_incident_transition(),
    # not merely returning a fabricated "persisted" outcome.
    assert len(client.queries) == 2
    select_sql, _ = client.queries[0]
    assert select_sql.strip().upper().startswith("SELECT")
    transition_sql, _ = client.queries[1]
    assert "UPDATE" in transition_sql.upper()
    assert "INSERT INTO" in transition_sql.upper()


def test_resolve_incident_persisted_mode_binds_resolution_notes():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [_state_row(status="mitigated", row_version=3)],
            [{"incident_id": "inc_1", "status": "resolved", "row_version": 4}],
        ]
    )

    outcome = resolve_incident(
        client,
        "inc_1",
        resolution_notes="Backfill completed.",
        expected_current_status="mitigated",
        mode="persisted",
        actor="triage-bot",
    )

    assert outcome.status == "resolved"
    assert outcome.persisted is True
    assert outcome.resolution_notes == "Backfill completed."
    _, job_config = client.queries[1]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["s0_resolution_notes"] == "Backfill completed."


def test_persisted_mode_requires_expected_current_status():
    with pytest.raises(ValueError):
        acknowledge_incident(object(), "inc_1", mode="persisted", actor="triage-bot")


def test_persisted_mode_raises_concurrent_modification_when_status_drifted():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(rows_per_call=[[_state_row(status="acknowledged", row_version=2)]])

    with pytest.raises((ConcurrentModificationError, PersistedConcurrentModificationError)):
        acknowledge_incident(
            client, "inc_1", expected_current_status="open", mode="persisted", actor="triage-bot"
        )
    # The concurrency check happens BEFORE any transition write is
    # attempted -- only the state-read query was issued.
    assert len(client.queries) == 1


def test_persisted_mode_raises_incident_not_found_when_never_persisted():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(rows_per_call=[[]])

    with pytest.raises(IncidentNotFoundError):
        acknowledge_incident(
            client, "never_persisted", expected_current_status="open", mode="persisted", actor="triage-bot"
        )


def test_persisted_mode_raises_invalid_transition_before_any_write():
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(rows_per_call=[[_state_row(status="detected", row_version=1)]])

    with pytest.raises(InvalidTransitionError):
        acknowledge_incident(
            client, "inc_1", expected_current_status="detected", mode="persisted", actor="triage-bot"
        )
    # Only the state-read query was issued -- validate_transition() fails
    # fast, before spending the row_version just read on a doomed write.
    assert len(client.queries) == 1


def test_persisted_mode_honors_an_explicit_config_over_module_defaults():
    """Proves record_incident_transition() (via the persisted lifecycle
    path) is authoritative on an explicitly-passed PlatformConfig,
    independent of this module's own import-time-resolved table
    constants -- the same guarantee
    tests/shared/test_incident_persistence.py's dataset-isolation tests
    prove directly against shared.incident_persistence."""

    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    config = PlatformConfig(project="proj", dataset="loupe_platform_test")
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [_state_row(status="open", row_version=1)],
            [{"incident_id": "inc_1", "status": "acknowledged", "row_version": 2}],
        ]
    )

    acknowledge_incident(
        client, "inc_1", expected_current_status="open", mode="persisted", actor="triage-bot", config=config
    )

    for sql, _ in client.queries:
        assert "loupe_platform_test" in sql
        assert "`loupe_platform.incidents`" not in sql
        assert "`loupe_platform.write_locks`" not in sql
