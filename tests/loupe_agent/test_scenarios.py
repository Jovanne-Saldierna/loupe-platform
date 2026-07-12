"""Tests for apps/loupe_agent/scenarios.py."""

from __future__ import annotations

import pytest

from apps.loupe_agent import scenarios
from tests.shared.conftest import FakeBigQueryClient


def test_lever_rules_has_the_three_original_levers():
    assert set(scenarios.LEVER_RULES.keys()) == {
        "return_rate_improvement",
        "channel_mix_shift",
        "category_price_position",
    }


def test_get_lever_baseline_raises_for_unknown_lever():
    client = FakeBigQueryClient()
    with pytest.raises(scenarios.UnknownLeverError):
        scenarios.get_lever_baseline(client, "not_a_real_lever")


def test_get_lever_baseline_requires_a_category_for_return_rate_improvement():
    client = FakeBigQueryClient()
    with pytest.raises(ValueError, match="requires a category"):
        scenarios.get_lever_baseline(client, "return_rate_improvement")


def test_get_lever_baseline_return_rate_improvement_uses_category_metrics():
    client = FakeBigQueryClient()
    client.next_rows = [
        {"category": "Swim", "revenue": 1000.0, "margin": 400.0, "total_items": 100, "returned_items": 25, "return_rate_pct": 25.0}
    ]
    baseline = scenarios.get_lever_baseline(client, "return_rate_improvement", category="Swim")
    assert baseline["lever"] == "return_rate_improvement"
    assert baseline["category"] == "Swim"
    assert baseline["return_rate_pct"] == 25.0


def test_get_lever_baseline_return_rate_improvement_raises_when_category_has_no_data():
    client = FakeBigQueryClient()
    client.next_rows = []
    with pytest.raises(ValueError, match="No data found"):
        scenarios.get_lever_baseline(client, "return_rate_improvement", category="Ghost")


def test_get_lever_baseline_channel_mix_shift_needs_no_category():
    client = FakeBigQueryClient()
    client.next_rows = []
    baseline = scenarios.get_lever_baseline(client, "channel_mix_shift")
    assert baseline["lever"] == "channel_mix_shift"
    assert baseline["months"] == []


def test_get_lever_baseline_category_price_position_includes_company_benchmark(monkeypatch):
    # This branch issues two distinct queries (price-position, then
    # company-wide benchmark) -- each already exercised in isolation by
    # test_metrics.py against a real FakeBigQueryClient. Here the two
    # metrics.py calls are monkeypatched so this test asserts only on
    # scenarios.py's own orchestration (both results attached to one
    # baseline dict), not on metrics.py's SQL/row-shape internals.
    client = FakeBigQueryClient()
    monkeypatch.setattr(
        scenarios,
        "get_lever_price_position",
        lambda c, category: {"category": category, "avg_sale_price": 40.0, "avg_cost": 18.0, "margin_pct": 55.0, "margin_metric": {}},
    )
    monkeypatch.setattr(
        scenarios,
        "get_company_benchmark",
        lambda c: {"avg_margin_pct": 38.0, "avg_return_rate_pct": 9.0},
    )
    baseline = scenarios.get_lever_baseline(client, "category_price_position", category="Jeans")
    assert baseline["lever"] == "category_price_position"
    assert baseline["company_benchmark"] == {"avg_margin_pct": 38.0, "avg_return_rate_pct": 9.0}
    assert baseline["avg_sale_price"] == 40.0


def test_get_lever_baseline_category_price_position_requires_a_category():
    client = FakeBigQueryClient()
    with pytest.raises(ValueError, match="requires a category"):
        scenarios.get_lever_baseline(client, "category_price_position")
