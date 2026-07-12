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


class LoupeAskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1_000)


class LoupeAskResponse(BaseModel):
    category: str
    answer: str
    source_health_status: Optional[str]
    source_health_warning: Optional[str]


class ErrorResponse(BaseModel):
    detail: str = Field(description="Safe user-facing error without raw infrastructure details")


class CatalogMetric(BaseModel):
    name: str
    version: str
    certification_status: str
    measurement_grain: str


class GovernanceCatalogResponse(BaseModel):
    metrics: list[CatalogMetric]


class GovernanceReviewRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)
    metric_name: str = Field(min_length=1, max_length=100)


class ReviewFinding(BaseModel):
    severity: str
    category: str
    message: str


class TrustFactor(BaseModel):
    name: str
    points: int
    reason: str


class ContractAlignment(BaseModel):
    contract: str
    expected: str
    observed: str
    status: str


class GovernanceReviewResponse(BaseModel):
    metric: CatalogMetric
    review_score: int
    summary: str
    findings: list[ReviewFinding]
    referenced_tables: list[str]
    recommended_next_steps: list[str]
    trust_score: int
    trust_band: str
    scoring_version: str
    trust_factors: list[TrustFactor]
    override_reason: Optional[str]
    source_health: str
    active_incident_ids: list[str]
    alignment: list[ContractAlignment]


class TriageTableHealth(BaseModel):
    table_id: str
    status: Literal["healthy", "degraded", "critical", "unknown"]
    freshness_minutes: Optional[float]
    active_incident_count: int


class TriageIncident(BaseModel):
    incident_id: str
    table_id: str
    check_type: str
    severity: str
    status: str
    created_at: str
    observed_value: Optional[float]
    expected_value: Optional[float]
    affected_metrics: list[str]
    owner: Optional[str]
    next_allowed_statuses: list[str]


class TriageWarehouseResponse(BaseModel):
    generated_at: str
    dataset: str
    monitored_tables: int
    healthy_tables: int
    degraded_tables: int
    critical_tables: int
    open_incidents: int
    freshness_minutes: Optional[float]
    tables: list[TriageTableHealth]
    incidents: list[TriageIncident]


class IncidentTransitionRequest(BaseModel):
    target_status: Literal["acknowledged", "investigating", "mitigated", "resolved", "open"]
    expected_current_status: str
    resolution_notes: Optional[str] = Field(default=None, max_length=2_000)


class IncidentTransitionResponse(BaseModel):
    incident_id: str
    status: str
    persisted: bool
    row_version: Optional[int]
