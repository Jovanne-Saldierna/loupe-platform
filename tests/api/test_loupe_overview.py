from datetime import date

from api.services import loupe_overview


def test_build_loupe_overview_uses_live_results_without_mock_fallback(monkeypatch):
    calls = []

    def fake_kpis(client, start, end, categories, states):
        calls.append((start, end))
        if len(calls) == 1:
            return {"revenue": 1200, "margin": 480, "total_items": 120, "returned_items": 6, "return_rate_pct": 5}
        return {"revenue": 1000, "margin": 350, "total_items": 100, "returned_items": 6, "return_rate_pct": 6}

    monkeypatch.setattr(loupe_overview, "get_dashboard_kpis", fake_kpis)
    monkeypatch.setattr(
        loupe_overview,
        "get_revenue_trend",
        lambda *args: [{"month": "2026-06", "revenue": 1200, "margin": 480, "items": 120}],
    )
    monkeypatch.setattr(
        loupe_overview,
        "health_for",
        lambda *args: {"status": "healthy", "warning": None, "tables": [{"table_id": "order_items", "status": "healthy", "known": True}]},
    )
    monkeypatch.setattr(loupe_overview, "_definition", lambda client: None)

    result = loupe_overview.build_loupe_overview(object(), date(2026, 6, 1), date(2026, 6, 30))

    assert result.revenue.value == 1200
    assert result.revenue.change_pct == 20.0
    assert result.gross_margin_pct.value == 40.0
    assert result.source_health.status == "healthy"
    assert result.metric_context.certification_status == "unavailable"
    assert len(calls) == 2


def test_insight_is_deterministic_and_grounded_in_period_values():
    text = loupe_overview._insight(
        {"revenue": 120, "return_rate_pct": 4},
        {"revenue": 100, "return_rate_pct": 5},
    )
    assert "20.0%" in text
    assert "improved" in text
