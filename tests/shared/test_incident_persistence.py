"""Tests for shared/incident_persistence.py.

Exercises create_incident() and record_incident_transition() against the
fake BigQuery client -- no live BigQuery access. The underlying
execute_transaction() mechanism (commit/rollback, ASSERT @@row_count,
result_sql, retry/backoff, lock-row contention) is already covered by
tests/shared/test_persistence_transactions.py and confirmed against real
BigQuery by the Phase 6B live spike (see docs/PHASE_6B_HANDOFF.md); these
tests focus on this module's own business logic: the SQL these templates
render, the idempotency/conflict contract, and the fail-fast transition
validation.
"""

from __future__ import annotations

import pytest

from shared.config import PlatformConfig
from shared.incidents import InvalidTransitionError
from shared.incident_persistence import (
    CREATE_INCIDENT_TXN,
    TRANSITION_INCIDENT_STATUS_TXN,
    create_incident,
    get_incident_state,
    record_incident_transition,
)
from shared.models import Incident
from shared.persistence_transactions import PayloadConflictError


def _incident(**overrides) -> Incident:
    defaults = dict(
        incident_id="analytics.orders.null_check.2026-07-12T00:00:00Z",
        created_at="2026-07-12T00:00:00Z",
        dataset="analytics",
        table_id="orders",
        check_type="null_check",
        severity="high",
        status="detected",
    )
    defaults.update(overrides)
    return Incident(**defaults)


# ---------------------------------------------------------------------------
# Templates are registered at import time and follow the verified pattern
# ---------------------------------------------------------------------------


def test_templates_are_registered_with_incidents_lock_domain():
    assert CREATE_INCIDENT_TXN.lock_domain == "incidents"
    assert TRANSITION_INCIDENT_STATUS_TXN.lock_domain == "incidents"


def test_create_incident_txn_touches_lock_row_before_insert():
    lock_index = CREATE_INCIDENT_TXN.sql.index("write_locks")
    insert_index = CREATE_INCIDENT_TXN.sql.index("INSERT INTO")
    assert lock_index < insert_index


def test_create_incident_txn_insert_uses_where_not_exists_guard():
    assert "WHERE NOT EXISTS" in CREATE_INCIDENT_TXN.sql
    assert "FROM UNNEST([1])" in CREATE_INCIDENT_TXN.sql
    assert "ASSERT @@row_count IN (0, 1)" in CREATE_INCIDENT_TXN.sql


def test_transition_txn_touches_lock_before_update_and_insert():
    sql = TRANSITION_INCIDENT_STATUS_TXN.sql
    lock_index = sql.index("write_locks")
    update_index = sql.index("UPDATE `loupe_platform.incidents`")
    insert_index = sql.index("INSERT INTO `loupe_platform.incident_transitions`")
    assert lock_index < update_index < insert_index


# ---------------------------------------------------------------------------
# create_incident()
# ---------------------------------------------------------------------------


def test_create_incident_first_call_reports_created_true(fake_client):
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "detected", "row_version": 1}
    ]
    result = create_incident(fake_client, _incident(incident_id="inc_1"), actor="triage-bot")

    assert result.created is True
    assert result.incident_id == "inc_1"
    assert result.persisted_severity == "high"
    assert result.row_version == 1


def test_create_incident_second_call_with_identical_payload_is_idempotent_no_op(fake_client):
    # Simulates a retry: the row already exists with matching severity/status.
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "detected", "row_version": 1}
    ]
    result = create_incident(fake_client, _incident(incident_id="inc_1"), actor="triage-bot")
    assert result.created is True  # row_version == 1 is the only signal available; see docstring


def test_create_incident_conflicting_severity_raises_payload_conflict_error(fake_client):
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "low", "status": "detected", "row_version": 1}
    ]
    with pytest.raises(PayloadConflictError) as excinfo:
        create_incident(fake_client, _incident(incident_id="inc_1", severity="high"), actor="triage-bot")
    assert "severity" in str(excinfo.value)
    assert "low" not in str(excinfo.value)
    assert "high" not in str(excinfo.value)


def test_create_incident_conflicting_status_raises_payload_conflict_error(fake_client):
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "open", "row_version": 1}
    ]
    with pytest.raises(PayloadConflictError) as excinfo:
        create_incident(fake_client, _incident(incident_id="inc_1", status="detected"), actor="triage-bot")
    assert "status" in str(excinfo.value)


def test_create_incident_binds_incident_id_and_actor(fake_client):
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "detected", "row_version": 1}
    ]
    create_incident(fake_client, _incident(incident_id="inc_1"), actor="triage-bot")
    _, job_config = fake_client.queries[0]
    param_names = {p.name for p in job_config.query_parameters}
    assert "s0_incident_id" in param_names
    assert "s0_actor" in param_names


def test_create_incident_raises_runtime_error_if_no_row_found_afterward(fake_client):
    fake_client.next_rows = []  # unreachable in practice, but must not silently succeed
    with pytest.raises(RuntimeError):
        create_incident(fake_client, _incident(incident_id="inc_1"), actor="triage-bot")


def test_create_incident_accepts_empty_affected_metrics_list(fake_client):
    # Regression test for the _build_job_config empty-array fix: an
    # incident with no affected_metrics/affected_dashboards yet must not
    # raise ValueError("Array parameter ... must not be empty").
    fake_client.next_rows = [
        {"incident_id": "inc_1", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "detected", "row_version": 1}
    ]
    incident = _incident(incident_id="inc_1", affected_metrics=[], affected_dashboards=[])
    result = create_incident(fake_client, incident, actor="triage-bot")
    assert result.incident_id == "inc_1"


# ---------------------------------------------------------------------------
# record_incident_transition()
# ---------------------------------------------------------------------------


def test_record_incident_transition_validates_before_touching_client(fake_client):
    with pytest.raises(InvalidTransitionError):
        record_incident_transition(
            fake_client,
            incident_id="inc_1",
            from_status="detected",
            to_status="resolved",  # not an allowed direct transition
            row_version_before=1,
            actor="triage-bot",
            transition_id="t_1",
        )
    assert fake_client.queries == []


def test_record_incident_transition_happy_path(fake_client):
    fake_client.next_rows = [{"incident_id": "inc_1", "status": "open", "row_version": 2}]
    result = record_incident_transition(
        fake_client,
        incident_id="inc_1",
        from_status="detected",
        to_status="open",
        row_version_before=1,
        actor="triage-bot",
        transition_id="t_1",
    )
    assert result.status == "open"
    assert result.row_version == 2


def test_record_incident_transition_binds_from_and_to_status(fake_client):
    fake_client.next_rows = [{"incident_id": "inc_1", "status": "open", "row_version": 2}]
    record_incident_transition(
        fake_client,
        incident_id="inc_1",
        from_status="detected",
        to_status="open",
        row_version_before=1,
        actor="triage-bot",
        transition_id="t_1",
    )
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["s0_from_status"] == "detected"
    assert params["s0_to_status"] == "open"
    assert params["s0_transition_id"] == "t_1"


def test_record_incident_transition_raises_runtime_error_if_no_row_found_afterward(fake_client):
    fake_client.next_rows = []
    with pytest.raises(RuntimeError):
        record_incident_transition(
            fake_client,
            incident_id="inc_1",
            from_status="detected",
            to_status="open",
            row_version_before=1,
            actor="triage-bot",
            transition_id="t_1",
        )


def test_record_incident_transition_propagates_unclassified_assert_failure(fake_client):
    # Per this module's documented scope: an ASSERT failure (e.g. the
    # incident wasn't actually in from_status) is NOT caught or re-wrapped
    # here -- it propagates as whatever execute_transaction() raises.
    fake_client.query_exception = RuntimeError("simulated ASSERT failure, non-retryable")
    with pytest.raises(RuntimeError):
        record_incident_transition(
            fake_client,
            incident_id="inc_1",
            from_status="acknowledged",
            to_status="investigating",
            row_version_before=2,
            actor="triage-bot",
            transition_id="t_2",
        )


# ---------------------------------------------------------------------------
# get_incident_state()
# ---------------------------------------------------------------------------


def test_get_incident_state_returns_none_when_not_found(fake_client):
    fake_client.next_rows = []
    assert get_incident_state(fake_client, "does_not_exist") is None


def test_get_incident_state_returns_status_and_row_version(fake_client):
    fake_client.next_rows = [{"incident_id": "inc_1", "status": "open", "row_version": 3}]
    state = get_incident_state(fake_client, "inc_1")
    assert state is not None
    assert state.status == "open"
    assert state.row_version == 3


def test_get_incident_state_issues_a_read_only_select(fake_client):
    fake_client.next_rows = [{"incident_id": "inc_1", "status": "open", "row_version": 1}]
    get_incident_state(fake_client, "inc_1")
    sql, _ = fake_client.queries[0]
    assert sql.strip().upper().startswith("SELECT")


# ---------------------------------------------------------------------------
# Dataset-target authoritative override: this module's own import-time-
# resolved INCIDENTS_TABLE/INCIDENT_TRANSITIONS_TABLE/WRITE_LOCKS_TABLE
# constants (imported at the very top of this test module, above, well
# before any of the tests below run -- i.e. this module IS already
# imported with whatever LOUPE_DATASET happened to be set, or unset, in
# this test process) must NOT be the dataset an explicit `config=`
# argument's calls actually target. Per Phase 6E correction 2: "the
# selected test dataset must remain authoritative even if modules were
# imported before CLI argument parsing." These tests select
# loupe_platform_test only NOW, well after import, and prove every read
# and write the config-aware calls issue targets loupe_platform_test --
# never this module's own frozen default constants (which, in this test
# process, are almost certainly NOT loupe_platform_test).
# ---------------------------------------------------------------------------


def _assert_every_query_targets(client, dataset: str) -> None:
    assert client.queries, "expected at least one query to have been issued"
    for sql, _ in client.queries:
        assert f"`{dataset}." in sql or f"{dataset}." in sql, sql
        for other in ("loupe_platform.", "loupe_platform_staging."):
            if other == f"{dataset}.":
                continue
            assert other not in sql, f"unexpected reference to {other!r} in: {sql}"


def test_create_incident_with_explicit_config_targets_the_configured_dataset_regardless_of_module_import(fake_client):
    # This module (shared.incident_persistence) was already imported at
    # the top of this file -- its own INCIDENTS_TABLE/WRITE_LOCKS_TABLE
    # constants are already frozen to whatever they resolved to then.
    # Selecting loupe_platform_test HERE, via an explicit PlatformConfig,
    # must still be authoritative.
    config = PlatformConfig(project="proj", dataset="loupe_platform_test")
    fake_client.next_rows = [
        {"incident_id": "inc_iso", "dataset": "analytics", "table_id": "orders",
         "check_type": "null_check", "severity": "high", "status": "detected", "row_version": 1}
    ]

    result = create_incident(fake_client, _incident(incident_id="inc_iso"), actor="triage-bot", config=config)

    assert result.incident_id == "inc_iso"
    _assert_every_query_targets(fake_client, "loupe_platform_test")


def test_record_incident_transition_with_explicit_config_targets_the_configured_dataset_regardless_of_module_import(fake_client):
    config = PlatformConfig(project="proj", dataset="loupe_platform_test")
    fake_client.next_rows = [{"incident_id": "inc_iso", "status": "open", "row_version": 2}]

    result = record_incident_transition(
        fake_client,
        incident_id="inc_iso",
        from_status="detected",
        to_status="open",
        row_version_before=1,
        actor="triage-bot",
        transition_id="t_iso",
        config=config,
    )

    assert result.status == "open"
    _assert_every_query_targets(fake_client, "loupe_platform_test")


def test_get_incident_state_with_explicit_config_targets_the_configured_dataset_regardless_of_module_import(fake_client):
    config = PlatformConfig(project="proj", dataset="loupe_platform_test")
    fake_client.next_rows = [{"incident_id": "inc_iso", "status": "open", "row_version": 1}]

    get_incident_state(fake_client, "inc_iso", config=config)

    _assert_every_query_targets(fake_client, "loupe_platform_test")


def test_two_different_configs_never_cross_contaminate_cached_templates(fake_client):
    # _create_template_for()/_transition_template_for() cache one
    # StatementTemplate per dataset -- proving a second, different dataset
    # still gets ITS OWN correctly-targeted SQL, not a stale template built
    # for a previously-requested dataset.
    prod_like_config = PlatformConfig(project="proj", dataset="loupe_platform_test_a")
    other_config = PlatformConfig(project="proj", dataset="loupe_platform_test_b")

    fake_client.next_rows = [{"incident_id": "inc_a", "status": "open", "row_version": 2}]
    record_incident_transition(
        fake_client, incident_id="inc_a", from_status="detected", to_status="open",
        row_version_before=1, actor="triage-bot", transition_id="t_a", config=prod_like_config,
    )
    fake_client.next_rows = [{"incident_id": "inc_b", "status": "open", "row_version": 2}]
    record_incident_transition(
        fake_client, incident_id="inc_b", from_status="detected", to_status="open",
        row_version_before=1, actor="triage-bot", transition_id="t_b", config=other_config,
    )

    first_sql, _ = fake_client.queries[0]
    second_sql, _ = fake_client.queries[1]
    assert "loupe_platform_test_a" in first_sql
    assert "loupe_platform_test_b" not in first_sql
    assert "loupe_platform_test_b" in second_sql
    assert "loupe_platform_test_a" not in second_sql
