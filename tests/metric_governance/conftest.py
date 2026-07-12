"""Pytest fixtures for apps/metric_governance/ tests.

Reuses the same FakeBigQueryClient/FakeTable/FakeDataset test doubles
defined in tests/shared/conftest.py (imported, not duplicated) -- pytest
fixtures declared in a sibling directory's conftest.py aren't
automatically visible here, so this file re-exposes the `fake_client`
fixture for tests under tests/metric_governance/, matching
tests/data_quality_triage/conftest.py's existing pattern.
"""

from __future__ import annotations

import pytest

from tests.shared.conftest import FakeBigQueryClient, FakeDataset, FakeTable, SequencedFakeBigQueryClient

__all__ = ["FakeBigQueryClient", "FakeDataset", "FakeTable", "SequencedFakeBigQueryClient", "fake_client"]


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()
