"""Tests for apps/loupe_agent/metrics.py.

Historical inputs/outputs are derived from
ecommerce-analytics-agent/main.py's original query functions (read-only
reference; commit 0cef813 for the dashboard functions -- see
metrics.py's module docstring) and app.py's rendering expectations
(app.py:349-408 for single/comparison shapes, app.py:465-527 for the
dashboard shapes).
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.loupe_agent import metrics
from tests.shared.conftest import FakeBigQueryClient


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()


def test_get_category_metrics_returns_none_for_no_data(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    assert metrics.get_category_metrics(fake_client, "Dresses") is None


def test_get_category_metrics_returns_structured_data_with_metric_refs(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [
        {
            "category": "Dresses",
            "revenue": 100_000.0,
            "margin": 40_000.0,
            "total_items": 500,
            "returned_items": 50,
            "return_rate_pct": 10.0,
        }
    ]
    result = metrics.get_category_metrics(fake_client, "Dresses")
    assert result["category"] == "Dresses"
    assert result["revenue"] == 100_000.0
    assert result["revenue_metric"]["name"] == "revenue"
    assert result["revenue_metric"]["certification_status"] == "pending_validation"
    assert result["margin_metric"]["name"] == "margin"
    assert result["return_rate_metric"]["name"] == "return_rate"

    # Confirms the query used a named parameter, never string interpolation.
    sql, job_config = fake_client.queries[0]
    assert "@category" in sql
    assert "Dresses" not in sql


def test_get_company_benchmark(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [{"avg_margin_pct": 38.5, "avg_return_rate_pct": 9.2}]
    result = metrics.get_company_benchmark(fake_client)
    assert result == {"avg_margin_pct": 38.5, "avg_return_rate_pct": 9.2}


def test_get_multi_category_comparison_passes_array_parameter(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [
        {"category": "Swim", "revenue": 1.0, "margin": 1.0, "total_items": 1, "return_rate_pct": 5.0},
    ]
    result = metrics.get_multi_category_comparison(fake_client, ["Swim", "Jeans"])
    assert result[0]["category"] == "Swim"
    sql, job_config = fake_client.queries[0]
    assert "@categories" in sql


def test_get_state_metrics_returns_none_for_no_data(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    assert metrics.get_state_metrics(fake_client, "Nowhere") is None


def test_get_returns_leakage_preserves_ranking_by_absolute_dollars(fake_client: FakeBigQueryClient):
    # margin_leakage per shared.metric_catalog: ranked by absolute dollars
    # lost, not by return-rate percentage -- a small category with a high
    # rate should not automatically outrank a large one with a moderate
    # rate. This test only proves the query is issued and rows pass
    # through unchanged; ORDER BY is verified by SQL inspection below.
    fake_client.next_rows = [
        {"category": "Dresses", "returned_items": 10, "total_items": 100, "return_rate_pct": 10.0, "margin_lost_to_returns": 5000.0},
        {"category": "Socks", "returned_items": 40, "total_items": 100, "return_rate_pct": 40.0, "margin_lost_to_returns": 300.0},
    ]
    result = metrics.get_returns_leakage(fake_client)
    assert result[0]["category"] == "Dresses"  # higher absolute dollars first
    sql, _ = fake_client.queries[0]
    assert "ORDER BY margin_lost_to_returns DESC" in sql


def test_get_channel_mix_trend_classifies_paid_vs_unpaid_and_computes_share(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [
        {"month": "2026-01", "traffic_source": "Facebook", "order_count": 30},
        {"month": "2026-01", "traffic_source": "Search", "order_count": 70},
        {"month": "2026-02", "traffic_source": "Email", "order_count": 10},
        {"month": "2026-02", "traffic_source": "Organic", "order_count": 90},
    ]
    result = metrics.get_channel_mix_trend(fake_client)
    months = {m["month"]: m for m in result["months"]}
    assert months["2026-01"]["paid"] == 30
    assert months["2026-01"]["unpaid"] == 70
    assert months["2026-01"]["paid_share_pct"] == 30.0
    assert months["2026-02"]["paid_share_pct"] == 10.0
    assert result["channel_mix_metric"]["name"] == "channel_mix"


def test_get_channel_mix_trend_handles_zero_total_without_dividing_by_zero(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    result = metrics.get_channel_mix_trend(fake_client)
    assert result["months"] == []


def test_get_lever_price_position_returns_none_for_no_data(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    assert metrics.get_lever_price_position(fake_client, "Ghost Category") is None


def test_get_lever_price_position_returns_structured_data(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [{"category": "Jeans", "avg_sale_price": 45.0, "avg_cost": 20.0, "margin_pct": 55.5}]
    result = metrics.get_lever_price_position(fake_client, "Jeans")
    assert result["avg_sale_price"] == 45.0
    assert result["margin_metric"]["name"] == "margin"


# ---------------------------------------------------------------------------
# Dashboard queries (recovered from commit 0cef813 -- see module docstring)
# ---------------------------------------------------------------------------


def test_get_dashboard_kpis_defaults_missing_values_to_zero(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    result = metrics.get_dashboard_kpis(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    assert result == {"revenue": 0, "margin": 0, "total_items": 0, "returned_items": 0, "return_rate_pct": 0}


def test_get_dashboard_kpis_binds_date_range_as_named_parameters(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [
        {"revenue": 500.0, "margin": 200.0, "total_items": 10, "returned_items": 1, "return_rate_pct": 10.0}
    ]
    result = metrics.get_dashboard_kpis(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    assert result["revenue"] == 500.0
    sql, job_config = fake_client.queries[0]
    assert "@start_date" in sql and "@end_date" in sql
    assert "2026-01-01" not in sql  # bound as a parameter, never interpolated


def test_get_dashboard_kpis_joins_users_only_when_state_filter_present(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    metrics.get_dashboard_kpis(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    sql_without_states, _ = fake_client.queries[0]
    assert f"JOIN `{metrics.QUALIFIED_DATASET}.users`" not in sql_without_states

    fake_client.queries.clear()
    metrics.get_dashboard_kpis(fake_client, date(2026, 1, 1), date(2026, 6, 30), states=["California"])
    sql_with_states, _ = fake_client.queries[0]
    assert f"JOIN `{metrics.QUALIFIED_DATASET}.users`" in sql_with_states


def test_get_revenue_trend_returns_rows_as_is(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [{"month": "2026-01", "revenue": 100.0, "margin": 40.0, "items": 5}]
    result = metrics.get_revenue_trend(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    assert result == fake_client.next_rows


def test_get_category_leaderboard_dashboard_orders_by_revenue(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [{"category": "Jeans", "revenue": 900.0, "margin": 300.0, "items": 20, "return_rate_pct": 5.0}]
    result = metrics.get_category_leaderboard_dashboard(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    assert result[0]["category"] == "Jeans"
    sql, _ = fake_client.queries[0]
    assert "ORDER BY revenue DESC" in sql


def test_get_state_breakdown_dashboard_attaches_state_abbrev(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [{"state": "California", "revenue": 1000.0, "margin": 400.0, "items": 30}]
    result = metrics.get_state_breakdown_dashboard(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    assert result[0]["state_abbrev"] == "CA"


def test_get_state_breakdown_dashboard_always_joins_users(fake_client: FakeBigQueryClient):
    fake_client.next_rows = []
    metrics.get_state_breakdown_dashboard(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    sql, _ = fake_client.queries[0]
    assert f"JOIN `{metrics.QUALIFIED_DATASET}.users`" in sql


def test_get_channel_mix_range_classifies_paid_vs_unpaid(fake_client: FakeBigQueryClient):
    fake_client.next_rows = [
        {"month": "2026-01", "traffic_source": "Display", "order_count": 5},
        {"month": "2026-01", "traffic_source": "Organic", "order_count": 15},
    ]
    result = metrics.get_channel_mix_range(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    assert result == [{"month": "2026-01", "paid": 5, "unpaid": 15, "total": 20}]


def test_get_channel_mix_range_always_joins_users_even_without_a_state_filter(fake_client: FakeBigQueryClient):
    # The original join_clause ternary was a no-op (both branches produced
    # the same join) -- simplified per explicit Phase 5 direction. This
    # test proves the join is present regardless of the states argument.
    fake_client.next_rows = []
    metrics.get_channel_mix_range(fake_client, date(2026, 1, 1), date(2026, 1, 31))
    sql, _ = fake_client.queries[0]
    assert f"JOIN `{metrics.QUALIFIED_DATASET}.users`" in sql


def test_state_abbrev_covers_all_50_states_plus_dc():
    assert len(metrics.STATE_ABBREV) == 51
    assert metrics.STATE_ABBREV["California"] == "CA"


def test_all_categories_matches_the_original_list_length():
    assert len(metrics.ALL_CATEGORIES) == 26
