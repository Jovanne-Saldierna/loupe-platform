"""Tests for shared/schema_hashing.py."""

from __future__ import annotations

from shared.schema_hashing import compute_schema_hash


def test_identical_columns_hash_identically():
    a = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    b = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    assert a == b


def test_column_order_does_not_affect_the_hash():
    a = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    b = compute_schema_hash({"name": "STRING", "id": "INTEGER"})
    assert a == b


def test_a_type_change_changes_the_hash():
    a = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    b = compute_schema_hash({"id": "STRING", "name": "STRING"})
    assert a != b


def test_an_added_column_changes_the_hash():
    a = compute_schema_hash({"id": "INTEGER"})
    b = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    assert a != b


def test_repeated_identical_observations_would_not_require_a_new_row():
    # Documents the intended usage: a caller (Phase 6B/6D) computes this
    # hash on every profiling run and only inserts a new schema_snapshots
    # row when it differs from the most recently persisted hash (or none
    # exists yet, or a cadence threshold requires a fresh observation) --
    # never on every rerun, per amendment 9.
    observation_1 = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    observation_2 = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    observation_3 = compute_schema_hash({"id": "INTEGER", "name": "STRING"})
    assert observation_1 == observation_2 == observation_3
