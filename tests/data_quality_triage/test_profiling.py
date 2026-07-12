"""Tests for apps/data_quality_triage/profiling.py.

Uses the same FakeBigQueryClient/FakeTable test doubles as
tests/shared/test_data_service.py (imported directly, not via a shared
conftest fixture, since tests/data_quality_triage/ has its own package).
profiling.py never constructs a bigquery.Client or calls one of its methods
directly -- it only calls shared.data_service.list_tables() and
shared.data_service.get_table_metadata(), so these fakes (which already
back the shared.data_service tests) are sufficient here too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.data_quality_triage.profiling import (
    QUALIFIED_DATASET,
    TableProfile,
    _freshness_minutes,
    _pick_nullable_candidates,
    _pick_primary_candidate,
    _pick_temporal_candidates,
    build_table_profile,
    build_table_profiles,
)
from tests.shared.conftest import FakeBigQueryClient, FakeTable


# ---------------------------------------------------------------------------
# Column-name heuristics (pure functions, ported from bigquery_source.py)
# ---------------------------------------------------------------------------


def test_pick_primary_candidate_prefers_priority_list_order():
    columns = ["user_id", "order_id", "id"]
    assert _pick_primary_candidate(columns) == "order_id"


def test_pick_primary_candidate_falls_back_to_any_id_suffixed_column():
    # "shipment_id" is not in the exact priority list, but ends in "_id",
    # so it's used as the fallback candidate.
    columns = ["status", "shipment_id"]
    assert _pick_primary_candidate(columns) == "shipment_id"


def test_pick_primary_candidate_returns_none_when_no_id_like_column_exists():
    assert _pick_primary_candidate(["status", "name", "description"]) is None


def test_pick_nullable_candidates_excludes_id_like_columns_and_caps_at_three():
    columns = ["id", "status", "name", "description", "notes", "category"]
    assert _pick_nullable_candidates(columns) == ["status", "name", "description"]


def test_pick_temporal_candidates_matches_known_names_and_caps_at_three():
    columns = ["id", "created_at", "updated_at", "shipped_at", "delivered_at", "status"]
    assert _pick_temporal_candidates(columns) == ["created_at", "updated_at", "shipped_at"]


def test_pick_temporal_candidates_returns_empty_list_when_no_match():
    assert _pick_temporal_candidates(["id", "status"]) == []


# ---------------------------------------------------------------------------
# _freshness_minutes
# ---------------------------------------------------------------------------


def test_freshness_minutes_returns_none_when_modified_at_is_none():
    assert _freshness_minutes(None) is None


def test_freshness_minutes_computes_elapsed_minutes():
    modified = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 11, 10, 30, 0, tzinfo=timezone.utc)
    assert _freshness_minutes(modified.isoformat(), now=now) == 30.0


def test_freshness_minutes_treats_naive_iso_strings_as_utc():
    modified = datetime(2026, 7, 11, 10, 0, 0)  # naive
    now = datetime(2026, 7, 11, 11, 0, 0, tzinfo=timezone.utc)
    assert _freshness_minutes(modified.isoformat(), now=now) == 60.0


def test_freshness_minutes_never_returns_negative():
    modified = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 11, 11, 0, 0, tzinfo=timezone.utc)  # modified is "in the future"
    assert _freshness_minutes(modified.isoformat(), now=now) == 0.0


# ---------------------------------------------------------------------------
# build_table_profile / build_table_profiles (routed through
# shared.data_service, never touching a bigquery.Client directly)
# ---------------------------------------------------------------------------


_ORDER_ITEMS_COLUMN_TYPES = {
    "id": "INTEGER",
    "order_id": "INTEGER",
    "user_id": "INTEGER",
    "status": "STRING",
    "created_at": "TIMESTAMP",
}


def _client_with_one_table() -> FakeBigQueryClient:
    client = FakeBigQueryClient()
    client.table_ids_by_dataset[QUALIFIED_DATASET] = ["order_items"]
    client.tables[f"{QUALIFIED_DATASET}.order_items"] = FakeTable(
        table_id="order_items",
        num_rows=181_594,
        modified=datetime(2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc),
        columns=["id", "order_id", "user_id", "status", "created_at"],
        column_types=_ORDER_ITEMS_COLUMN_TYPES,
    )
    return client


def test_build_table_profile_derives_all_fields_from_metadata():
    client = _client_with_one_table()
    now = datetime(2026, 7, 11, 9, 15, 0, tzinfo=timezone.utc)

    profile = build_table_profile(client, QUALIFIED_DATASET, "order_items", now=now)

    assert profile == TableProfile(
        table_id="order_items",
        row_count=181_594,
        last_modified=datetime(2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc).isoformat(),
        freshness_minutes=15.0,
        primary_candidate="order_id",
        nullable_candidates=["status", "created_at"],
        temporal_candidates=["created_at"],
        column_types=_ORDER_ITEMS_COLUMN_TYPES,
    )


def test_build_table_profile_carries_column_types_through_from_metadata():
    client = _client_with_one_table()
    profile = build_table_profile(client, QUALIFIED_DATASET, "order_items")
    assert profile.column_types == _ORDER_ITEMS_COLUMN_TYPES


def test_build_table_profiles_covers_every_table_in_the_dataset():
    client = _client_with_one_table()
    client.table_ids_by_dataset[QUALIFIED_DATASET].append("dim_customers")
    client.tables[f"{QUALIFIED_DATASET}.dim_customers"] = FakeTable(
        table_id="dim_customers", num_rows=100_000, modified=None, columns=["id", "email"]
    )

    profiles = build_table_profiles(client, QUALIFIED_DATASET)

    assert [p.table_id for p in profiles] == ["order_items", "dim_customers"]
    assert profiles[1].freshness_minutes is None
    assert profiles[1].last_modified is None


def test_build_table_profiles_returns_empty_list_for_empty_dataset():
    client = FakeBigQueryClient()
    assert build_table_profiles(client, QUALIFIED_DATASET) == []
