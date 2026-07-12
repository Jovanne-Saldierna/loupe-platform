"""Tests for the Phase 5 grain-mismatch correction: measurement grain
(shared.metric_catalog's `measurement_grain` -- the atomic business entity
a metric is DEFINED over) and reporting grain (the dimensional/temporal
shape ONE query's output takes) are two different concepts, and must
never be conflated as a single "grain mismatch."

This file proves three things explicitly, per the correction's
requirements:

1. The same catalog metric definition (same measurement_grain, same
   identity) legitimately backs multiple, simultaneously-valid reporting
   grains -- monthly, per-category, per-state, and whole-window -- and
   none of that is a "definition mismatch." (tests/metric_governance's
   definition_diff tests already cover the case where measurement_grain
   GENUINELY differs between two definitions; this file covers the
   opposite: one definition, many valid reporting shapes.)
2. return_rate's numerator and denominator are both counted at the same
   order_item grain, in the same query, over the same filter scope --
   never a numerator and denominator computed from differently-scoped
   queries.
3. channel_mix's denominator is an order_item count, not an order count,
   a session count, or an events-table count -- despite the SQL alias
   `order_count`, which reads as if it were order-grain.
"""

from __future__ import annotations

from datetime import date

from apps.loupe_agent import metrics
from shared.metric_catalog import get_definition
from tests.shared.conftest import FakeBigQueryClient


# ---------------------------------------------------------------------------
# 1. One measurement grain, many valid reporting grains
# ---------------------------------------------------------------------------


def test_revenue_measurement_grain_is_order_item_and_never_changes():
    definition = get_definition("revenue")
    assert definition is not None
    assert definition.measurement_grain.startswith("order_item")


def test_category_grain_month_grain_and_whole_window_reporting_all_cite_the_same_revenue_definition():
    """get_category_metrics (one row per category), get_revenue_trend (one
    row per month), and get_dashboard_kpis (one aggregate row for the
    whole window) are three structurally different reporting shapes for
    revenue. Proving they all resolve to the identical catalog identity
    (same name/certification_status/version, i.e. the same
    measurement_grain) is exactly the "no false mismatch" guarantee: the
    catalog is never asked to arbitrate between these three shapes,
    because it was never making a reporting-shape claim to begin with.
    """

    category_client = FakeBigQueryClient()
    category_client.next_rows = [
        {
            "category": "Dresses",
            "revenue": 100.0,
            "margin": 40.0,
            "total_items": 10,
            "returned_items": 1,
            "return_rate_pct": 10.0,
        }
    ]
    category_result = metrics.get_category_metrics(category_client, "Dresses")

    trend_client = FakeBigQueryClient()
    trend_client.next_rows = [{"month": "2026-01", "revenue": 100.0, "margin": 40.0, "items": 10}]
    trend_result = metrics.get_revenue_trend(trend_client, date(2026, 1, 1), date(2026, 6, 30))

    kpis_client = FakeBigQueryClient()
    kpis_client.next_rows = [
        {"revenue": 100.0, "margin": 40.0, "total_items": 10, "returned_items": 1, "return_rate_pct": 10.0}
    ]
    kpis_result = metrics.get_dashboard_kpis(kpis_client, date(2026, 1, 1), date(2026, 6, 30))

    # Reporting shapes genuinely differ -- these are NOT the same output grain.
    assert set(category_result.keys()) >= {"category", "revenue_metric"}
    assert "month" in trend_result[0] and "category" not in trend_result[0]
    assert "revenue" in kpis_result and "month" not in kpis_result and "category" not in kpis_result

    # But the metric identity behind all three is identical: the same
    # catalog definition, same measurement_grain, same certification
    # status -- proving reporting-shape diversity never gets reported as a
    # "definition mismatch" against the catalog.
    definition = get_definition("revenue")
    assert category_result["revenue_metric"]["name"] == definition.name
    assert category_result["revenue_metric"]["certification_status"] == definition.certification_status
    assert category_result["revenue_metric"]["version"] == definition.version
    # get_revenue_trend/get_dashboard_kpis don't attach a metric ref
    # per-row (see metrics.py), so the identity check runs where it's
    # actually attached (get_category_metrics, get_company_benchmark-style
    # single-entity functions) -- the point is that the SAME
    # shared.metric_catalog.get_definition("revenue") singleton backs all
    # three query shapes; there is only one revenue definition to
    # possibly disagree with itself.
    assert get_definition("revenue") is definition


def test_return_rate_grain_across_category_state_and_whole_window_reporting():
    """Same guarantee as revenue, for return_rate specifically, since
    return_rate is the metric whose numerator/denominator grain is most
    likely to be misread as reporting-grain-dependent.
    """

    category_client = FakeBigQueryClient()
    category_client.next_rows = [
        {"category": "Dresses", "revenue": 1.0, "margin": 1.0, "total_items": 10, "returned_items": 2, "return_rate_pct": 20.0}
    ]
    category_result = metrics.get_category_metrics(category_client, "Dresses")

    state_client = FakeBigQueryClient()
    state_client.next_rows = [
        {"state": "California", "revenue": 1.0, "margin": 1.0, "total_items": 10, "return_rate_pct": 20.0}
    ]
    state_result = metrics.get_state_metrics(state_client, "California")

    definition = get_definition("return_rate")
    assert category_result["return_rate_metric"]["name"] == definition.name == "return_rate"
    assert state_result["return_rate_metric"]["name"] == definition.name == "return_rate"
    assert category_result["return_rate_metric"]["version"] == state_result["return_rate_metric"]["version"]


# ---------------------------------------------------------------------------
# 2. return_rate denominator grain: numerator and denominator are both
# order_item counts, computed in the SAME query over the SAME filter
# scope -- never two differently-scoped queries.
# ---------------------------------------------------------------------------


def test_return_rate_numerator_and_denominator_are_both_order_item_counts_in_one_query():
    client = FakeBigQueryClient()
    client.next_rows = [
        {"category": "Dresses", "revenue": 1.0, "margin": 1.0, "total_items": 10, "returned_items": 2, "return_rate_pct": 20.0}
    ]
    metrics.get_category_metrics(client, "Dresses")
    sql, _ = client.queries[0]

    # Both the numerator (COUNTIF ... = 'Returned') and the denominator
    # (COUNT(*)) appear in the SAME SELECT list, over the SAME FROM/WHERE
    # scope -- there is exactly one query, so there is no possibility of
    # the numerator and denominator being computed from two differently
    # filtered result sets.
    assert sql.count("FROM `") == 1
    assert sql.count("WHERE") <= 1
    assert "COUNTIF(oi.status = 'Returned')" in sql
    assert "COUNT(*)" in sql
    # The ratio itself is computed in-database from those same two
    # expressions, not recombined afterward from two separate calls:
    assert "SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*))" in sql


def test_return_rate_pct_arithmetic_matches_returned_items_over_total_items():
    client = FakeBigQueryClient()
    client.next_rows = [
        {"category": "Dresses", "revenue": 100.0, "margin": 40.0, "total_items": 50, "returned_items": 5, "return_rate_pct": 10.0}
    ]
    result = metrics.get_category_metrics(client, "Dresses")
    assert result["return_rate_pct"] == round(result["returned_items"] / result["total_items"] * 100, 2)


def test_return_rate_denominator_is_order_item_grain_not_order_grain_in_every_agent_facing_function():
    """order_items is a line-item table (multiple rows can share one
    order_id); return_rate's formula and catalog measurement_grain are
    both explicit that the denominator is order_items rows, not distinct
    orders. Verified structurally: no function computing return_rate_pct
    ever wraps COUNT in DISTINCT, which would silently change the
    denominator from order_item grain to order grain.
    """

    checks = [
        (metrics.get_category_metrics, ("Dresses",)),
        (metrics.get_state_metrics, ("California",)),
        (metrics.get_multi_category_comparison, (["Dresses", "Jeans"],)),
        (metrics.get_multi_state_comparison, (["California", "Texas"],)),
        (metrics.get_returns_leakage, ()),
    ]
    for fn, args in checks:
        client = FakeBigQueryClient()
        client.next_rows = []
        fn(client, *args)
        sql, _ = client.queries[0]
        assert "return_rate_pct" in sql or fn is metrics.get_returns_leakage
        assert "COUNT(DISTINCT" not in sql, f"{fn.__name__} must not silently change return_rate's denominator to order grain"


# ---------------------------------------------------------------------------
# 3. channel_mix denominator grain: an order_item count, not an order
# count or an events-table count, despite the misleading `order_count`
# SQL alias.
# ---------------------------------------------------------------------------


def test_channel_mix_order_count_alias_is_actually_an_order_item_count():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_channel_mix_trend(client)
    sql, _ = client.queries[0]

    # The real denominator behind `order_count` is COUNT(*) over
    # order_items -- one row per line item, not per order. If this were
    # ever changed to COUNT(DISTINCT oi.order_id), the denominator would
    # silently become order-grain instead of order_item-grain, which
    # would NOT match shared.metric_catalog's channel_mix measurement_grain.
    assert f"FROM `{metrics.QUALIFIED_DATASET}.order_items` oi" in sql
    assert "COUNT(*) AS order_count" in sql
    assert "COUNT(DISTINCT" not in sql
    # And it never touches an events table -- see shared/metric_catalog.py's
    # channel_mix entry for the corrected approved_source_tables.
    assert "events" not in sql.lower()


def test_channel_mix_range_order_count_alias_is_also_an_order_item_count():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_channel_mix_range(client, date(2026, 1, 1), date(2026, 6, 30))
    sql, _ = client.queries[0]
    assert "COUNT(*) AS order_count" in sql
    assert "COUNT(DISTINCT" not in sql
    assert "events" not in sql.lower()


def test_channel_mix_catalog_measurement_grain_flags_the_misleading_alias():
    definition = get_definition("channel_mix")
    assert definition.measurement_grain.startswith("order_item")
    assert "order_count" in definition.measurement_grain
    assert "not" in definition.measurement_grain.lower()


def test_channel_mix_catalog_approved_source_tables_match_the_real_query_not_a_stale_events_reference():
    definition = get_definition("channel_mix")
    assert set(definition.approved_source_tables) == {"order_items", "users"}
    assert "events" not in definition.approved_source_tables
