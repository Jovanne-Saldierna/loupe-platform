"""Tests for apps/loupe_agent/source_health.py.

Per the Phase 5 correction review: until Phase 6 persistence exists,
every table's health must be reported as an explicit "unknown", never
silently "healthy", and "unknown" must be treated as warning-worthy
(no trust benefit) exactly like "degraded"/"critical".
"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.loupe_agent import source_health
from tests.shared.conftest import FakeBigQueryClient


def _incident_row(**overrides) -> dict:
    row = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset=source_health.QUALIFIED_DATASET,
        table_id="order_items",
        check_type="null_ratio",
        severity="medium",
        status="open",
        observed_value=None,
        expected_value=None,
        sql_template=None,
        query_hash=None,
        affected_metrics=[],
        affected_dashboards=[],
        playbook=None,
        owner=None,
        acknowledged_at=None,
        resolved_at=None,
        resolution_notes=None,
        rule_version=None,
        recurrence_of_incident_id=None,
    )
    row.update(overrides)
    return row


def test_table_health_reports_unknown_when_the_incidents_table_does_not_exist():
    # No `loupe_platform.incidents` table exists yet (Phase 6 not started)
    # -- FakeBigQueryClient with an exception configured simulates that
    # real-world failure honestly instead of masking it.
    client = FakeBigQueryClient()
    client.query_exception = RuntimeError('Not found: Table loupe_platform.incidents')
    result = source_health.table_health(client, "order_items")
    assert result == {"table_id": "order_items", "status": "unknown", "known": False}


def test_table_health_reports_healthy_when_persistence_returns_zero_active_incidents():
    client = FakeBigQueryClient()
    client.next_rows = []  # no incident rows at all for this table
    result = source_health.table_health(client, "order_items")
    assert result == {"table_id": "order_items", "status": "healthy", "known": True}


def test_table_health_reports_critical_for_an_active_high_severity_incident():
    client = FakeBigQueryClient()
    client.next_rows = [_incident_row(severity="high", status="open")]
    result = source_health.table_health(client, "order_items")
    assert result == {"table_id": "order_items", "status": "critical", "known": True}


def test_table_health_reports_degraded_for_a_non_high_active_incident():
    client = FakeBigQueryClient()
    client.next_rows = [_incident_row(severity="low", status="acknowledged")]
    result = source_health.table_health(client, "order_items")
    assert result == {"table_id": "order_items", "status": "degraded", "known": True}


def test_get_source_health_covers_every_requested_table():
    client = FakeBigQueryClient()
    client.query_exception = RuntimeError("unreachable")
    rows = source_health.get_source_health(client, ("order_items", "products", "users"))
    assert [r["table_id"] for r in rows] == ["order_items", "products", "users"]
    assert all(r["status"] == "unknown" for r in rows)


def test_summarize_awards_no_trust_benefit_for_unknown_status():
    rows = [{"table_id": "order_items", "status": "unknown", "known": False}]
    summary = source_health.summarize(rows)
    assert summary["status"] == "unknown"
    assert summary["warning"] is not None
    assert "Phase 6" in summary["warning"]


def test_summarize_is_silent_only_when_every_table_is_known_and_healthy():
    rows = [
        {"table_id": "order_items", "status": "healthy", "known": True},
        {"table_id": "products", "status": "healthy", "known": True},
    ]
    summary = source_health.summarize(rows)
    assert summary["status"] == "healthy"
    assert summary["warning"] is None


def test_summarize_picks_the_worst_status_when_tables_disagree():
    rows = [
        {"table_id": "order_items", "status": "healthy", "known": True},
        {"table_id": "products", "status": "critical", "known": True},
        {"table_id": "users", "status": "unknown", "known": False},
    ]
    summary = source_health.summarize(rows)
    assert summary["status"] == "critical"
    assert "CRITICAL" in summary["warning"]


def test_summarize_degraded_status_produces_a_review_warning():
    rows = [{"table_id": "order_items", "status": "degraded", "known": True}]
    summary = source_health.summarize(rows)
    assert summary["status"] == "degraded"
    assert "DEGRADED" in summary["warning"]


def test_summarize_handles_an_empty_table_list_honestly():
    summary = source_health.summarize([])
    assert summary["status"] == "unknown"
    assert "No source tables were checked" in summary["warning"]


def test_health_for_uses_the_registered_table_dependencies(monkeypatch):
    client = FakeBigQueryClient()
    seen_tables = []

    def _fake_get_source_health(c, tables):
        seen_tables.extend(tables)
        return [{"table_id": t, "status": "healthy", "known": True} for t in tables]

    monkeypatch.setattr(source_health, "get_source_health", _fake_get_source_health)
    source_health.health_for(client, "state_metrics")
    assert seen_tables == list(source_health.TABLE_DEPENDENCIES["state_metrics"])


def test_health_for_raises_for_an_unregistered_dependency_key():
    client = FakeBigQueryClient()
    try:
        source_health.health_for(client, "not_a_real_dependency")
        assert False, "expected KeyError for an unregistered dependency key"
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Phase 5 grain-mismatch correction: confirm TABLE_DEPENDENCIES actually
# covers every source table a given query touches -- no under-inclusion,
# which is the failure mode that would let an unhealthy table go
# undetected in a response's source-health summary. Declaring MORE tables
# than one specific call variant happens to touch (e.g. dashboard_kpis
# conservatively lists `users` even on calls with no state filter, where
# the join is skipped) is fine -- that's a superset, never a gap.
# ---------------------------------------------------------------------------

import re
from datetime import date

from apps.loupe_agent import metrics


def _tables_referenced_in(sql: str) -> set[str]:
    """Extract bare table names from every backtick-qualified
    `{dataset}.table` reference in a SQL string."""

    return {
        match.rsplit(".", 1)[-1]
        for match in re.findall(rf"`{re.escape(metrics.QUALIFIED_DATASET)}\.(\w+)`", sql)
    }


def test_table_dependencies_cover_every_table_get_category_metrics_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_category_metrics(client, "Dresses")
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["category_metrics"])


def test_table_dependencies_cover_every_table_get_company_benchmark_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = [{"avg_margin_pct": 0.0, "avg_return_rate_pct": 0.0}]
    metrics.get_company_benchmark(client)
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["company_benchmark"])


def test_table_dependencies_cover_every_table_get_multi_category_comparison_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_multi_category_comparison(client, ["Dresses", "Jeans"])
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["multi_category_comparison"])


def test_table_dependencies_cover_every_table_get_state_metrics_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_state_metrics(client, "California")
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["state_metrics"])


def test_table_dependencies_cover_every_table_get_multi_state_comparison_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_multi_state_comparison(client, ["California", "Texas"])
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["multi_state_comparison"])


def test_table_dependencies_cover_every_table_get_returns_leakage_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_returns_leakage(client)
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["returns_leakage"])


def test_table_dependencies_cover_every_table_get_channel_mix_trend_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_channel_mix_trend(client)
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["channel_mix_trend"])


def test_table_dependencies_cover_every_table_get_lever_price_position_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_lever_price_position(client, "Dresses")
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["lever_price_position"])


def test_table_dependencies_cover_every_table_dashboard_functions_actually_query_with_no_state_filter():
    """Confirm coverage even for the no-state-filter call variant, where
    get_dashboard_kpis/get_revenue_trend/get_category_leaderboard_dashboard
    skip the users JOIN entirely -- the declared dependency set is a
    (safe, conservative) superset in that case, never a gap."""

    for name in ("get_dashboard_kpis", "get_revenue_trend", "get_category_leaderboard_dashboard"):
        client = FakeBigQueryClient()
        client.next_rows = []
        getattr(metrics, name)(client, date(2026, 1, 1), date(2026, 6, 30))
        sql, _ = client.queries[0]
        key = {
            "get_dashboard_kpis": "dashboard_kpis",
            "get_revenue_trend": "revenue_trend",
            "get_category_leaderboard_dashboard": "category_leaderboard",
        }[name]
        assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES[key]), name


def test_table_dependencies_cover_every_table_get_state_breakdown_dashboard_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_state_breakdown_dashboard(client, date(2026, 1, 1), date(2026, 6, 30))
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["state_breakdown"])


def test_table_dependencies_cover_every_table_get_channel_mix_range_actually_queries():
    client = FakeBigQueryClient()
    client.next_rows = []
    metrics.get_channel_mix_range(client, date(2026, 1, 1), date(2026, 6, 30))
    sql, _ = client.queries[0]
    assert _tables_referenced_in(sql) <= set(source_health.TABLE_DEPENDENCIES["channel_mix_range"])


def test_summarize_worst_status_determines_the_response_warning_end_to_end(monkeypatch):
    """End-to-end proof (not just unit-level) that health_for() -- the
    function every chat.py handler actually calls -- reduces every table
    it looked up to the single worst status, and that status alone
    determines the returned warning."""

    def _fake_get_source_health(c, tables):
        # Three tables, three different statuses -- critical must win,
        # regardless of table order.
        statuses = {"order_items": "healthy", "products": "degraded", "users": "critical"}
        return [{"table_id": t, "status": statuses[t], "known": True} for t in tables]

    monkeypatch.setattr(source_health, "get_source_health", _fake_get_source_health)
    client = FakeBigQueryClient()
    result = source_health.health_for(client, "state_metrics")  # order_items, products, users
    assert result["status"] == "critical"
    assert "CRITICAL" in result["warning"]
    assert "users" in result["warning"]
