"""Tests for tools/phase6e_ops/safety.py's DatasetTargetGuard -- the
generic, module-agnostic backstop for Phase 6E correction 2: "no write
target may silently fall back to loupe_platform," "add a query-target
assertion or guarded persistence adapter that refuses any generated DML
referencing a dataset other than the exact validated target."
"""

from __future__ import annotations

import pytest

from shared.config import UnexpectedDatasetTargetError
from tools.phase6e_ops.safety import DatasetTargetGuard
from tests.shared.conftest import FakeBigQueryClient, FakeDataset


def test_guard_forwards_a_query_that_targets_the_allowed_dataset(fake_client):
    guard = DatasetTargetGuard(fake_client, allowed_dataset="loupe_platform_test")
    fake_client.next_rows = [{"n": 1}]

    job = guard.query("SELECT * FROM `loupe_platform_test.incidents`")

    assert job.result() == [{"n": 1}]
    assert len(fake_client.queries) == 1


def test_guard_refuses_a_query_that_targets_a_different_dataset(fake_client):
    guard = DatasetTargetGuard(fake_client, allowed_dataset="loupe_platform_test")

    with pytest.raises(UnexpectedDatasetTargetError):
        guard.query("SELECT * FROM `loupe_platform.incidents`")

    # Refused BEFORE ever reaching the wrapped client -- never a partial
    # or silent execution against the wrong dataset.
    assert fake_client.queries == []


def test_guard_refuses_even_when_the_forbidden_dataset_is_production(fake_client):
    guard = DatasetTargetGuard(fake_client, allowed_dataset="loupe_platform_test")

    with pytest.raises(UnexpectedDatasetTargetError) as excinfo:
        guard.query(
            "UPDATE `loupe_platform.write_locks` SET last_touched_at = CURRENT_TIMESTAMP() "
            "WHERE lock_domain = 'incidents'"
        )
    assert "loupe_platform" in str(excinfo.value)
    assert fake_client.queries == []


def test_guard_delegates_unrelated_attributes_to_the_wrapped_client(fake_client):
    fake_client.datasets["proj.loupe_platform_test"] = FakeDataset(location="US")
    guard = DatasetTargetGuard(fake_client, allowed_dataset="loupe_platform_test")

    # get_dataset() is a metadata read, not a SQL DML/DDL path -- the
    # guard delegates it unguarded via __getattr__.
    result = guard.get_dataset("proj.loupe_platform_test")
    assert result.location == "US"


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()
