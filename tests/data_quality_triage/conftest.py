"""Pytest fixtures for apps/data_quality_triage/ tests.

Reuses the same FakeBigQueryClient/FakeTable test doubles defined in
tests/shared/conftest.py (imported, not duplicated) -- pytest fixtures
declared in a sibling directory's conftest.py aren't automatically visible
here, so this file re-exposes the `fake_client` fixture for tests under
tests/data_quality_triage/.
"""

from __future__ import annotations

import pytest

from tests.shared.conftest import FakeBigQueryClient, FakeTable, SequencedFakeBigQueryClient

__all__ = ["FakeBigQueryClient", "FakeTable", "SequencedFakeBigQueryClient", "fake_client"]


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()
