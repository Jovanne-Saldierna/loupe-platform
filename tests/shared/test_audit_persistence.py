"""Tests for shared/audit_persistence.py."""

from __future__ import annotations

import json

import pytest

from shared.audit import build_event
from shared.audit_persistence import (
    WRITE_AUDIT_EVENT_TXN,
    write_event_idempotent,
)
from shared.models import AuditEvent
from shared.persistence_transactions import PayloadConflictError


def _event(**overrides) -> AuditEvent:
    defaults = dict(
        event_id="evt_1",
        timestamp="2026-07-12T00:00:00Z",
        actor="governance-bot",
        event_type="sql_review_submitted",
        subject="review_id:abc123",
        outcome="completed",
        context={"table_count": 3},
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


def test_write_audit_event_txn_is_guarded_by_audit_events_lock_domain():
    assert WRITE_AUDIT_EVENT_TXN.lock_domain == "audit_events"
    lock_index = WRITE_AUDIT_EVENT_TXN.sql.index("write_locks")
    insert_index = WRITE_AUDIT_EVENT_TXN.sql.index("INSERT INTO")
    assert lock_index < insert_index
    assert "WHERE NOT EXISTS" in WRITE_AUDIT_EVENT_TXN.sql


def test_write_event_idempotent_happy_path(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_1", "event_type": "sql_review_submitted", "subject": "review_id:abc123", "outcome": "completed"}
    ]
    result = write_event_idempotent(fake_client, _event())
    assert result.event_id == "evt_1"
    assert result.persisted_outcome == "completed"


def test_write_event_idempotent_rejects_secrets_before_touching_client(fake_client):
    event = _event(context={"api_key": "sk-super-secret"})
    with pytest.raises(ValueError):
        write_event_idempotent(fake_client, event)
    assert fake_client.queries == []


def test_write_event_idempotent_conflicting_outcome_raises_payload_conflict_error(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_1", "event_type": "sql_review_submitted", "subject": "review_id:abc123", "outcome": "rejected"}
    ]
    with pytest.raises(PayloadConflictError) as excinfo:
        write_event_idempotent(fake_client, _event(outcome="completed"))
    assert "outcome" in str(excinfo.value)
    assert "rejected" not in str(excinfo.value)
    assert "completed" not in str(excinfo.value)


def test_write_event_idempotent_serializes_context_as_canonical_json(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_1", "event_type": "sql_review_submitted", "subject": "review_id:abc123", "outcome": "completed"}
    ]
    write_event_idempotent(fake_client, _event(context={"b": 2, "a": 1}))
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert json.loads(params["s0_context_json"]) == {"a": 1, "b": 2}
    # sort_keys=True -- the same context always serializes identically
    # regardless of dict construction order, matching
    # shared.metric_hashing's canonicalization discipline.
    assert params["s0_context_json"] == '{"a": 1, "b": 2}'


def test_write_event_idempotent_defaults_lock_actor_to_event_actor(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_1", "event_type": "sql_review_submitted", "subject": "review_id:abc123", "outcome": "completed"}
    ]
    write_event_idempotent(fake_client, _event(actor="governance-bot"))
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["s0_actor"] == "governance-bot"


def test_write_event_idempotent_accepts_explicit_actor_override(fake_client):
    fake_client.next_rows = [
        {"event_id": "evt_1", "event_type": "sql_review_submitted", "subject": "review_id:abc123", "outcome": "completed"}
    ]
    write_event_idempotent(fake_client, _event(actor="governance-bot"), actor="override-actor")
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["s0_actor"] == "override-actor"


def test_write_event_idempotent_raises_runtime_error_if_no_row_found_afterward(fake_client):
    fake_client.next_rows = []
    with pytest.raises(RuntimeError):
        write_event_idempotent(fake_client, _event())


def test_write_event_idempotent_works_with_build_event_output(fake_client):
    # Confirms this module composes cleanly with the existing
    # shared.audit.build_event() constructor, not just hand-built
    # AuditEvent instances.
    event = build_event(
        event_id="evt_2",
        timestamp="2026-07-12T00:00:00Z",
        actor="triage-bot",
        event_type="incident_created",
        subject="inc_1",
        outcome="completed",
    )
    fake_client.next_rows = [
        {"event_id": "evt_2", "event_type": "incident_created", "subject": "inc_1", "outcome": "completed"}
    ]
    result = write_event_idempotent(fake_client, event)
    assert result.event_id == "evt_2"
