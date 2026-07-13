from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

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
    raw_data: Optional[Any] = Field(
        default=None,
        description="Structured evidence behind `answer`, when the matched intent produced any "
        "(category/state metrics, comparison rows, channel-mix months, scenario baseline, "
        "returns-leakage rows). Never fabricated -- passed through unchanged from "
        "apps.loupe_agent.chat.run_agent()'s own raw_data.",
    )


class CategoryBreakdown(BaseModel):
    category: str
    revenue: float
    margin: float
    items: int
    return_rate_pct: float


class LoupeCategoriesResponse(BaseModel):
    start_date: date
    end_date: date
    categories: list[CategoryBreakdown]


class StateBreakdown(BaseModel):
    state: str
    state_abbrev: str
    revenue: float
    margin: float
    items: int


class LoupeStatesResponse(BaseModel):
    start_date: date
    end_date: date
    states: list[StateBreakdown]


class ChannelMonth(BaseModel):
    month: str
    paid: int
    unpaid: int
    total: int
    paid_share_pct: float


class LoupeChannelMixResponse(BaseModel):
    start_date: date
    end_date: date
    months: list[ChannelMonth]


class ReturnsLeakageRow(BaseModel):
    category: str
    returned_items: int
    total_items: int
    return_rate_pct: float
    margin_lost_to_returns: float


class LoupeReturnsLeakageResponse(BaseModel):
    categories: list[ReturnsLeakageRow]


class LoupeBenchmarkResponse(BaseModel):
    avg_margin_pct: float
    avg_return_rate_pct: float


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


class GovernanceHelperRequest(BaseModel):
    """Everything the Governance SQL Review screen already has on hand
    after a review has run -- sent back to the helper endpoint verbatim so
    the AI narration has no path to invent a score, finding, or incident
    that the deterministic review didn't already produce. `question` is
    the only free-text field; every other field mirrors
    GovernanceReviewResponse (see api/services/governance_helper.py for
    how this is flattened into a grounding summary)."""

    question: str = Field(min_length=3, max_length=1_000)
    metric: CatalogMetric
    sql: str = Field(min_length=1, max_length=50_000)
    review_score: int
    summary: str
    findings: list[ReviewFinding]
    trust_score: int
    trust_band: str
    trust_factors: list[TrustFactor]
    recommended_next_steps: list[str]
    referenced_tables: list[str]
    source_health: str
    active_incident_ids: list[str]
    override_reason: Optional[str] = None


class GovernanceHelperResponse(BaseModel):
    answer: str


class TriageTableHealth(BaseModel):
    table_id: str
    status: Literal["healthy", "degraded", "critical", "unknown"]
    freshness_minutes: Optional[float]
    active_incident_count: int


class AuditTrailEntry(BaseModel):
    """One deterministic, grounded step in Triage's audit trail -- see
    api/services/triage_audit.py. `source` names the deterministic
    function/module or the narration model that produced this step, never
    a guess; `timestamp` is omitted (None) when no real timestamp is
    available, never backfilled with the current time to look complete."""

    step: str
    description: str
    timestamp: Optional[str] = None
    source: Optional[str] = None


class LineageMetric(BaseModel):
    """One governed metric that reads from a source table, plus the
    downstream dashboards/reports that metric already declares in the
    metric catalog (shared.models.MetricDefinition.downstream_dashboards).
    Empty list when the catalog has no downstream assets on file for this
    metric yet -- never invented."""

    name: str
    downstream_dashboards: list[str] = Field(default_factory=list)


class TriageLineage(BaseModel):
    """One source table's lineage chain: table -> governed metrics that
    declare it as an approved_source_table -> each metric's own
    downstream_dashboards, exactly as the persisted metric catalog
    records them. See api/services/triage_warehouse.py's
    _governed_tables_and_metric_map()."""

    table_id: str
    governed_metrics: list[LineageMetric]


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
    # Reverse of GovernanceReviewResponse.active_incident_ids: which governed
    # catalog metrics (by name) declare this incident's table_id in their
    # approved_source_tables. Derived conservatively from the same persisted
    # metric catalog Governance already reads -- never fabricated. Empty when
    # the catalog is unavailable or no governed metric references the table.
    governed_metric_names: list[str]
    # Deduplicated union of downstream_dashboards across every governed
    # metric in governed_metric_names -- the same catalog data as `lineage`
    # on TriageWarehouseResponse, pre-flattened onto the incident for
    # convenience (grounding the AI playbook and the incident detail panel
    # without a second lookup). Empty when no governed metric on this table
    # has any downstream assets on file.
    downstream_assets: list[str] = Field(default_factory=list)
    # Deterministic facts about how this incident record came to exist --
    # metadata load, check evaluation, incident generation -- never
    # AI-narrated. See api/services/triage_audit.py.
    audit_trail: list[AuditTrailEntry] = Field(default_factory=list)


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
    # Source table -> governed metrics -> downstream assets, for every
    # governed table Triage currently monitors (not just tables with active
    # incidents) -- the general lineage/observability view. Empty list when
    # the catalog is unavailable.
    lineage: list[TriageLineage] = Field(default_factory=list)


class TriageHelperRequest(BaseModel):
    """Everything the Triage Source Health screen already has on hand for
    the currently selected incident -- sent back to the helper endpoint
    verbatim so the AI narration cannot invent a root cause, severity, or
    affected-metric list beyond what the deterministic incident record
    already contains. `question` is the only free-text field; every other
    field mirrors TriageIncident plus the source table's
    active_incident_count."""

    question: str = Field(min_length=3, max_length=1_000)
    incident_id: str
    table_id: str
    check_type: str
    severity: str
    status: str
    created_at: str
    observed_value: Optional[float] = None
    expected_value: Optional[float] = None
    affected_metrics: list[str] = Field(default_factory=list)
    governed_metric_names: list[str] = Field(default_factory=list)
    active_incident_count: Optional[int] = None
    owner: Optional[str] = None


class TriageHelperResponse(BaseModel):
    answer: str
    # Which narration model actually produced `answer`, or None when no
    # Anthropic API key is configured and the deterministic "not
    # configured" fallback text was returned instead -- see
    # api/services/triage_helper.py. Surfaced so the frontend audit trail
    # can record what actually generated an answer, never assumed.
    model: Optional[str] = None


class SqlCheck(BaseModel):
    title: str
    # Plain-language description of what this specific query validates --
    # deterministic, from apps/data_quality_triage/sql_checks.py, never
    # AI-narrated. Lets the frontend render each check as a labeled step in
    # an investigation workflow instead of an unexplained code block.
    purpose: str
    sql: str


class TriagePlaybookRequest(BaseModel):
    """Everything the Triage Source Health screen already has on hand for
    the currently selected incident -- the same shape as
    TriageHelperRequest minus the free-text question, plus the lineage
    facts (downstream_assets, source_health) already rendered elsewhere on
    the page. Sent back to the playbook endpoint verbatim so the generated
    playbook cannot invent a root cause, affected asset, or governed
    metric beyond what the deterministic incident record and catalog
    lineage already contain."""

    incident_id: str
    table_id: str
    check_type: str
    severity: str
    status: str
    created_at: str
    observed_value: Optional[float] = None
    expected_value: Optional[float] = None
    affected_metrics: list[str] = Field(default_factory=list)
    governed_metric_names: list[str] = Field(default_factory=list)
    downstream_assets: list[str] = Field(default_factory=list)
    active_incident_count: Optional[int] = None
    source_health: Optional[str] = None
    owner: Optional[str] = None


class TriagePlaybookResponse(BaseModel):
    """An AI-generated triage playbook. `likely_root_cause`,
    `impact_summary`, and `next_action` are narration, grounded only in
    the incident context sent to the endpoint -- they explain, they do
    not decide whether the data is broken (that already happened
    deterministically before this incident existed). `debugging_steps`,
    `sql_checks`, and `owner_recommendation` are computed deterministically
    from check_type/table_id/owner, independent of the AI call -- see
    api/services/triage_playbook.py for the exact split."""

    likely_root_cause: str
    impact_summary: str
    affected_downstream_assets: list[str]
    affected_governed_metrics: list[str]
    debugging_steps: list[str]
    sql_checks: list[SqlCheck]
    owner_recommendation: str
    next_action: str
    # Which narration model actually produced likely_root_cause/
    # impact_summary/next_action, or None when no Anthropic API key is
    # configured (in which case those three fields hold the deterministic
    # "unknown, not enough information" fallback text, never a fabricated
    # guess). Same convention as TriageHelperResponse.model.
    model: Optional[str] = None


class IncidentTransitionRequest(BaseModel):
    target_status: Literal["acknowledged", "investigating", "mitigated", "resolved", "open"]
    expected_current_status: str
    resolution_notes: Optional[str] = Field(default=None, max_length=2_000)


class IncidentTransitionResponse(BaseModel):
    incident_id: str
    status: str
    persisted: bool
    row_version: Optional[int]
