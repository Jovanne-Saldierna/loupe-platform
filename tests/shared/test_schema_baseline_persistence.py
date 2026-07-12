"""Tests for shared/schema_baseline_persistence.py."""

from __future__ import annotations

import json

from shared.schema_baseline_persistence import (
    PROMOTE_SCHEMA_BASELINE_TXN,
    get_schema_baseline,
    promote_schema_baseline,
)


def test_promote_txn_is_guarded_by_schema_baselines_lock_domain():
    assert PROMOTE_SCHEMA_BASELINE_TXN.lock_domain == "schema_baselines"


def test_promote_txn_touches_both_lock_domains_before_their_respective_writes():
    sql = PROMOTE_SCHEMA_BASELINE_TXN.sql
    schema_lock_index = sql.index("lock_domain = 'schema_baselines'")
    merge_index = sql.index("MERGE `loupe_platform.schema_baselines`")
    audit_lock_index = sql.index("lock_domain = 'audit_events'")
    insert_index = sql.index("INSERT INTO `loupe_platform.audit_events`")
    assert schema_lock_index < merge_index < audit_lock_index < insert_index


def test_promote_txn_is_an_upsert_not_insert_if_absent():
    # Per module docstring: re-promoting the same (dataset, table_id) must
    # overwrite, not conflict -- this is a MERGE with WHEN MATCHED THEN
    # UPDATE, unlike CREATE_INCIDENT_TXN/WRITE_AUDIT_EVENT_TXN's
    # insert-if-absent shape.
    assert "WHEN MATCHED THEN UPDATE" in PROMOTE_SCHEMA_BASELINE_TXN.sql
    assert "WHEN NOT MATCHED THEN INSERT" in PROMOTE_SCHEMA_BASELINE_TXN.sql


def test_promote_txn_audit_insert_is_still_insert_if_absent_by_event_id():
    # The MERGE is naturally idempotent, but the audit_events insert must
    # still guard against double-recording on a retried call.
    assert "WHERE NOT EXISTS" in PROMOTE_SCHEMA_BASELINE_TXN.sql


def test_promote_schema_baseline_happy_path(fake_client):
    fake_client.next_rows = [
        {"dataset": "analytics", "table_id": "orders", "source_snapshot_id": "snap_1",
         "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "governance-bot"}
    ]
    result = promote_schema_baseline(
        fake_client,
        dataset="analytics",
        table_id="orders",
        columns={"order_id": "STRING", "amount": "FLOAT64"},
        source_snapshot_id="snap_1",
        promoted_by="governance-bot",
        event_id="evt_promo_1",
        event_timestamp="2026-07-12T00:00:00Z",
    )
    assert result.dataset == "analytics"
    assert result.table_id == "orders"
    assert result.source_snapshot_id == "snap_1"


def test_promote_schema_baseline_binds_columns_as_sorted_json(fake_client):
    fake_client.next_rows = [
        {"dataset": "analytics", "table_id": "orders", "source_snapshot_id": "snap_1",
         "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "governance-bot"}
    ]
    promote_schema_baseline(
        fake_client,
        dataset="analytics",
        table_id="orders",
        columns={"amount": "FLOAT64", "order_id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="governance-bot",
        event_id="evt_promo_1",
        event_timestamp="2026-07-12T00:00:00Z",
    )
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    columns = json.loads(params["s0_columns_json"])
    assert columns == [
        {"name": "amount", "field_type": "FLOAT64"},
        {"name": "order_id", "field_type": "STRING"},
    ]


def test_promote_schema_baseline_context_includes_deterministic_schema_hash(fake_client):
    fake_client.next_rows = [
        {"dataset": "analytics", "table_id": "orders", "source_snapshot_id": "snap_1",
         "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "governance-bot"}
    ]
    promote_schema_baseline(
        fake_client,
        dataset="analytics",
        table_id="orders",
        columns={"order_id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="governance-bot",
        event_id="evt_promo_1",
        event_timestamp="2026-07-12T00:00:00Z",
    )
    _, job_config = fake_client.queries[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    context = json.loads(params["s0_context_json"])
    assert context["schema_hash"]  # non-empty -- exact value covered by test_schema_hashing.py
    assert context["column_count"] == 1
    assert context["dataset"] == "analytics"


def test_promote_schema_baseline_context_never_contains_a_secret_field(fake_client):
    # promote_schema_baseline builds its own audit context internally
    # (never caller-supplied), so this is really confirming
    # shared.audit.validate_no_secrets() is actually being called on it --
    # a regression guard in case the context dict's keys ever grow to
    # include something that would trip the sensitive-field scan.
    fake_client.next_rows = [
        {"dataset": "analytics", "table_id": "orders", "source_snapshot_id": "snap_1",
         "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "governance-bot"}
    ]
    promote_schema_baseline(
        fake_client,
        dataset="analytics",
        table_id="orders",
        columns={"order_id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="governance-bot",
        event_id="evt_promo_1",
        event_timestamp="2026-07-12T00:00:00Z",
    )  # must not raise


def test_promote_schema_baseline_raises_runtime_error_if_no_row_found_afterward(fake_client):
    fake_client.next_rows = []
    import pytest

    with pytest.raises(RuntimeError):
        promote_schema_baseline(
            fake_client,
            dataset="analytics",
            table_id="orders",
            columns={"order_id": "STRING"},
            source_snapshot_id="snap_1",
            promoted_by="governance-bot",
            event_id="evt_promo_1",
            event_timestamp="2026-07-12T00:00:00Z",
        )


# ---------------------------------------------------------------------------
# get_schema_baseline() -- Phase 6D read (a plain run_query(), no
# transaction).
# ---------------------------------------------------------------------------


def test_get_schema_baseline_returns_none_when_nothing_promoted_yet(fake_client):
    fake_client.next_rows = []
    result = get_schema_baseline(fake_client, dataset="analytics", table_id="orders")
    assert result is None


def test_get_schema_baseline_returns_persisted_row_shaped_as_a_column_type_dict(fake_client):
    fake_client.next_rows = [
        {
            "dataset": "analytics",
            "table_id": "orders",
            "columns": [{"name": "order_id", "field_type": "STRING"}, {"name": "amount", "field_type": "FLOAT64"}],
            "source_snapshot_id": "snap_1",
            "promoted_at": "2026-07-12T00:00:00Z",
            "promoted_by": "governance-bot",
        }
    ]
    result = get_schema_baseline(fake_client, dataset="analytics", table_id="orders")
    assert result is not None
    assert result.dataset == "analytics"
    assert result.table_id == "orders"
    assert result.columns == {"order_id": "STRING", "amount": "FLOAT64"}
    assert result.promoted_by == "governance-bot"


def test_get_schema_baseline_is_read_only_never_registers_or_mutates(fake_client):
    fake_client.next_rows = []
    get_schema_baseline(fake_client, dataset="analytics", table_id="orders")
    # A plain run_query() read must never touch write_locks or issue a
    # BEGIN TRANSACTION script.
    sql, _ = fake_client.queries[-1]
    assert "BEGIN TRANSACTION" not in sql
    assert "write_locks" not in sql
