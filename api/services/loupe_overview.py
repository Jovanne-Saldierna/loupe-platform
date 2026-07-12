from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from api.models import (
    LoupeOverviewResponse,
    MetricContext,
    MetricValue,
    SourceHealthSummary,
    SourceTableHealth,
    TrendPoint,
)
from apps.loupe_agent.metrics import get_dashboard_kpis, get_revenue_trend
from apps.loupe_agent.source_health import health_for
from shared.config import load_persistence_mode
from shared.metric_catalog import get_definition
from shared.metric_catalog_persistence import resolve_current_definition


def _change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


def _as_float(value: Any) -> float:
    return float(value or 0)


def _definition(client: Any):
    if load_persistence_mode() == "persisted":
        resolved = resolve_current_definition(client, "revenue")
        if not resolved.ok or resolved.definition is None:
            return None
        return resolved.definition
    return get_definition("revenue")


def _insight(current: dict, previous: dict) -> str:
    revenue_change = _change(_as_float(current["revenue"]), _as_float(previous["revenue"]))
    returns_change = _change(_as_float(current["return_rate_pct"]), _as_float(previous["return_rate_pct"]))
    if revenue_change is None:
        return "The selected period has live performance data, but the prior period is empty, so a growth comparison is unavailable."
    direction = "increased" if revenue_change >= 0 else "decreased"
    return_direction = "improved" if returns_change is not None and returns_change <= 0 else "increased"
    return f"Revenue {direction} {abs(revenue_change):.1f}% versus the prior period while return pressure {return_direction}."


def build_loupe_overview(client: Any, start_date: date, end_date: date) -> LoupeOverviewResponse:
    period_days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)

    current = get_dashboard_kpis(client, start_date, end_date)
    previous = get_dashboard_kpis(client, previous_start, previous_end)
    trend_rows = get_revenue_trend(client, start_date, end_date)
    health = health_for(client, "dashboard_kpis")
    definition = _definition(client)

    revenue = _as_float(current["revenue"])
    margin = _as_float(current["margin"])
    previous_revenue = _as_float(previous["revenue"])
    previous_margin = _as_float(previous["margin"])
    margin_pct = (margin / revenue * 100) if revenue else 0
    previous_margin_pct = (previous_margin / previous_revenue * 100) if previous_revenue else 0

    context = MetricContext(
        name="revenue",
        version=definition.version if definition else None,
        certification_status=definition.certification_status if definition else "unavailable",
        measurement_grain=definition.measurement_grain if definition else "unavailable",
        reporting_grain="one row per month",
    )

    return LoupeOverviewResponse(
        start_date=start_date,
        end_date=end_date,
        revenue=MetricValue(value=revenue, change_pct=_change(revenue, previous_revenue)),
        gross_margin_pct=MetricValue(value=round(margin_pct, 1), change_pct=round(margin_pct - previous_margin_pct, 1)),
        order_items=MetricValue(
            value=float(current["total_items"]),
            change_pct=_change(float(current["total_items"]), float(previous["total_items"])),
        ),
        return_rate_pct=MetricValue(
            value=_as_float(current["return_rate_pct"]),
            change_pct=round(_as_float(current["return_rate_pct"]) - _as_float(previous["return_rate_pct"]), 1),
        ),
        trend=[
            TrendPoint(
                period=str(row["month"]),
                revenue=_as_float(row["revenue"]),
                margin=_as_float(row["margin"]),
                items=int(row["items"] or 0),
            )
            for row in trend_rows
        ],
        insight=_insight(current, previous),
        source_health=SourceHealthSummary(
            status=health["status"],
            warning=health["warning"],
            tables=[SourceTableHealth(**row) for row in health["tables"]],
        ),
        metric_context=context,
    )
