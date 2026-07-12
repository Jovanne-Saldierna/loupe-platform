from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MetricValue(BaseModel):
    value: float
    change_pct: Optional[float] = None


class TrendPoint(BaseModel):
    period: str
    revenue: float
    margin: float
    items: int


class SourceTableHealth(BaseModel):
    table_id: str
    status: str
    known: bool


class SourceHealthSummary(BaseModel):
    status: Literal["healthy", "degraded", "critical", "unknown"]
    warning: Optional[str]
    tables: list[SourceTableHealth]


class MetricContext(BaseModel):
    name: str
    version: Optional[str]
    certification_status: str
    measurement_grain: str
    reporting_grain: str


class LoupeOverviewResponse(BaseModel):
    start_date: date
    end_date: date
    revenue: MetricValue
    gross_margin_pct: MetricValue
    order_items: MetricValue
    return_rate_pct: MetricValue
    trend: list[TrendPoint]
    insight: str
    source_health: SourceHealthSummary
    metric_context: MetricContext
    data_source: Literal["BigQuery live"] = "BigQuery live"


class ErrorResponse(BaseModel):
    detail: str = Field(description="Safe user-facing error without raw infrastructure details")
