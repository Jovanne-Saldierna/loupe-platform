"""Tests for shared/persistence_bootstrap.py -- Phase 6D's read-only
startup resolution of persistence availability.

Proves the explicit cutover rule: no bootstrap/migrate/seed/certify call
of any kind happens here, only reads (config validation + schema version
check), and "constants" mode never even constructs a client.
"""

from __future__ import annotations

from shared.persistence_bootstrap import resolve_persistence
from tests.shared.conftest import FakeDataset


def test_constants_mode_never_constructs_a_client_or_touches_bigquery(fake_client):
    calls = []

    def _factory(project, location):
        calls.append((project, location))
        return fake_client

    result = resolve_persistence({"LOUPE_PERSISTENCE_MODE": "constants"}, client_factory=_factory)

    assert result.mode == "constants"
    assert result.available is False
    assert result.client is None
    assert calls == []
    assert fake_client.queries == []


def test_persisted_mode_with_no_project_configured_is_honestly_unavailable():
    result = resolve_persistence({"LOUPE_PERSISTENCE_MODE": "persisted"})

    assert result.mode == "persisted"
    assert result.available is False
    assert result.client is None
    assert result.safe_error is not None


def test_persisted_mode_available_when_dataset_and_schema_version_check_out(fake_client):
    fake_client.datasets["proj.loupe_platform"] = FakeDataset(location="US")
    fake_client.next_rows = [{"version": 1}]

    result = resolve_persistence(
        {"LOUPE_PERSISTENCE_MODE": "persisted", "GOOGLE_CLOUD_PROJECT": "proj"},
        client=fake_client,
    )

    assert result.mode == "persisted"
    assert result.available is True
    assert result.client is fake_client
    assert result.safe_error is None


def test_persisted_mode_unavailable_when_dataset_metadata_read_fails(fake_client):
    fake_client.get_dataset_exception = RuntimeError("permission denied")

    result = resolve_persistence(
        {"LOUPE_PERSISTENCE_MODE": "persisted", "GOOGLE_CLOUD_PROJECT": "proj"},
        client=fake_client,
    )

    assert result.available is False
    assert result.client is None
    assert "permission denied" not in (result.safe_error or "")  # never leaks raw exception text


def test_persisted_mode_unavailable_when_schema_version_is_behind(fake_client):
    fake_client.datasets["proj.loupe_platform"] = FakeDataset(location="US")
    fake_client.next_rows = []  # no applied migrations

    result = resolve_persistence(
        {"LOUPE_PERSISTENCE_MODE": "persisted", "GOOGLE_CLOUD_PROJECT": "proj"},
        client=fake_client,
    )

    assert result.available is False
    assert result.client is None
    assert result.safe_error is not None


def test_resolve_persistence_never_issues_a_write_or_transaction_script(fake_client):
    fake_client.datasets["proj.loupe_platform"] = FakeDataset(location="US")
    fake_client.next_rows = [{"version": 1}]

    resolve_persistence({"LOUPE_PERSISTENCE_MODE": "persisted", "GOOGLE_CLOUD_PROJECT": "proj"}, client=fake_client)

    for sql, _ in fake_client.queries:
        assert "BEGIN TRANSACTION" not in sql
        assert "CREATE TABLE" not in sql.upper()
        assert "INSERT" not in sql.upper()
        assert "MERGE" not in sql.upper()
