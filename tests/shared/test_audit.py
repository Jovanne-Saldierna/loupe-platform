"""Tests for shared/audit.py."""

from __future__ import annotations

import pytest

from shared.audit import build_event, list_events_for_subject, write_event
from shared.models import AuditEvent


def test_build_event_defaults_context_to_empty_dict():
    event = build_event(
        event_id="evt_1",
        timestamp="2026-07-11T00:00:00Z",
        actor="jovanne",
        event_type="sql_review_submitted",
        subject="review_id:abc123",
        outcome="completed",
    )
    assert event.context == {}


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "api_key",
        "API_KEY",
        "ANTHROPIC_API_KEY",
        "anthropicApiKey",
        "secret",
        "SECRET",
        "auth_token",
        "token",
        "password",
        "gcp_credential",
        "credential",
        "private_key",
        "access_token",
        "AccessToken",
        "myAccessToken",
    ],
)
def test_build_event_rejects_genuinely_sensitive_context_keys(sensitive_key):
    with pytest.raises(ValueError):
        build_event(
            event_id="evt_1",
            timestamp="t",
            actor="a",
            event_type="t",
            subject="s",
            outcome="o",
            context={sensitive_key: "should never be here"},
        )


@pytest.mark.parametrize(
    "legitimate_key",
    [
        "primary_key",
        "metric_key",
        "key_findings",
        "keyboard_event",
        "foreign_key",
        "sort_key",
        "table_count",
        "finding_count",
    ],
)
def test_build_event_accepts_legitimate_business_keys_containing_key(legitimate_key):
    event = build_event(
        event_id="evt_1",
        timestamp="t",
        actor="a",
        event_type="sql_review_submitted",
        subject="s",
        outcome="o",
        context={legitimate_key: "value"},
    )
    assert legitimate_key in event.context


def test_build_event_allows_non_secret_context():
    event = build_event(
        event_id="evt_1",
        timestamp="t",
        actor="a",
        event_type="sql_review_submitted",
        subject="s",
        outcome="o",
        context={"table_count": 3, "finding_count": 1},
    )
    assert event.context == {"table_count": 3, "finding_count": 1}


def test_rejected_secret_error_message_never_echoes_the_value():
    with pytest.raises(ValueError) as excinfo:
        build_event(
            event_id="evt_1",
            timestamp="t",
            actor="a",
            event_type="t",
            subject="s",
            outcome="o",
            context={"api_key": "sk-super-secret-value-should-never-appear-12345"},
        )
    message = str(excinfo.value)
    assert "sk-super-secret-value-should-never-appear-12345" not in message
    assert "api_key" in message  # the field name IS reported, just not the value


def test_build_event_rejects_secrets_nested_inside_a_dict():
    with pytest.raises(ValueError) as excinfo:
        build_event(
            event_id="evt_1",
            timestamp="t",
            actor="a",
            event_type="t",
            subject="s",
            outcome="o",
            context={"request": {"headers": {"api_key": "sk-super-secret"}}},
        )
    message = str(excinfo.value)
    assert "sk-super-secret" not in message
    assert "api_key" in message


def test_build_event_rejects_secrets_nested_inside_a_list_of_dicts():
    with pytest.raises(ValueError) as excinfo:
        build_event(
            event_id="evt_1",
            timestamp="t",
            actor="a",
            event_type="t",
            subject="s",
            outcome="o",
            context={"attempts": [{"table_count": 1}, {"token": "leaked-token-value"}]},
        )
    message = str(excinfo.value)
    assert "leaked-token-value" not in message
    assert "token" in message


def test_build_event_accepts_nested_context_with_no_secrets():
    event = build_event(
        event_id="evt_1",
        timestamp="t",
        actor="a",
        event_type="t",
        subject="s",
        outcome="o",
        context={"request": {"headers": {"content_type": "application/json"}}, "attempts": [{"table_count": 1}]},
    )
    assert event.context["request"]["headers"]["content_type"] == "application/json"


def test_write_event_calls_insert_rows_json_with_correct_table(fake_client):
    event = build_event(
        event_id="evt_1", timestamp="t", actor="a", event_type="t", subject="s", outcome="o"
    )
    write_event(fake_client, event)
    table, rows = fake_client.inserted_rows[0]
    assert table == "loupe_platform.audit_events"
    assert rows[0]["event_id"] == "evt_1"


def test_write_event_raises_on_insert_errors(fake_client):
    fake_client.insert_errors = [{"index": 0, "errors": ["boom"]}]
    event = build_event(
        event_id="evt_1", timestamp="t", actor="a", event_type="t", subject="s", outcome="o"
    )
    with pytest.raises(RuntimeError):
        write_event(fake_client, event)


def test_write_event_rejects_secrets_even_if_event_was_hand_built(fake_client):
    # Bypasses build_event entirely to prove write_event has its own,
    # independent defense-in-depth check.
    event = AuditEvent(
        event_id="evt_1",
        timestamp="t",
        actor="a",
        event_type="t",
        subject="s",
        outcome="o",
        context={"api_key": "leaked"},
    )
    with pytest.raises(ValueError):
        write_event(fake_client, event)


def test_list_events_for_subject_maps_rows_and_binds_params(fake_client):
    fake_client.next_rows = [
        {
            "event_id": "evt_1",
            "timestamp": "2026-07-11T00:00:00Z",
            "actor": "jovanne",
            "event_type": "sql_review_submitted",
            "subject": "review_id:abc123",
            "outcome": "completed",
            "context": {},
        }
    ]
    events = list_events_for_subject(fake_client, "review_id:abc123", limit=10)
    assert len(events) == 1
    assert events[0].event_id == "evt_1"

    sql, job_config = fake_client.queries[0]
    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {"subject", "limit"}
