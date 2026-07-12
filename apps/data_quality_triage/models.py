"""Data Quality Triage's own presentation/domain models.

Analogous to apps/metric_governance/models.py: these are NOT the same thing
as shared.models.Incident. shared.models.Incident is the cross-app contract
(the facts: severity, status, observed_value/expected_value, sql,
affected_metrics/affected_dashboards) that Loupe and Governance read.
Everything in this file is Triage-specific narrative detail -- richer
severity granularity, root-cause guesses, and affected-asset lists -- that
has no field on shared.models.Incident and is only ever constructed and
consumed inside this app, so per docs/architecture.md it belongs here, not
in shared/.

CheckSeverity intentionally keeps the original app's 4-level vocabulary
(low/medium/high/critical), distinct from shared.models.Severity's 3-level
vocabulary (high/medium/low) documented in docs/data-quality-triage.md's
severity baseline. The two are NOT the same axis: a TableFinding's
CheckSeverity is the full-resolution internal classification a deterministic
check produces; shared.models.Incident.severity is the constrained,
cross-app-contract severity the docs require. checks.py performs the one,
explicit, documented translation between them (critical -> high) at the
single point where a TableFinding is promoted into a shared.models.Incident
-- the richer detail is never silently discarded before that point, and the
collapse never happens anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

CheckSeverity = Literal["low", "medium", "high", "critical"]
_CHECK_SEVERITY_VALUES = {"low", "medium", "high", "critical"}

# "error" and "not_evaluated" were added in the Phase 4 correction pass
# alongside schema-drift and query-exception detection:
#   - "error": the check's QUERY failed to execute (timeout, permission
#     denial, malformed SQL, etc.) -- the warehouse/query layer itself
#     broke, as distinct from "fail" (the query ran fine and found bad
#     data). See anomaly_engine.py's _query_exception_finding().
#   - "not_evaluated": the check has a real precondition it cannot
#     satisfy right now (e.g. schema_drift with no baseline snapshot
#     supplied yet) -- this is explicitly NOT "pass". A "pass" means the
#     check ran and found nothing wrong; "not_evaluated" means the check
#     could not honestly reach a verdict at all. Neither "error" nor
#     "not_evaluated" is a case where nothing needs surfacing, but only
#     "error" becomes an incident (see checks.build_incident_from_finding) --
#     "not_evaluated" has no confirmed problem to report, just a missing
#     precondition.
CheckStatus = Literal["pass", "warn", "fail", "error", "not_evaluated"]
_CHECK_STATUS_VALUES = {"pass", "warn", "fail", "error", "not_evaluated"}


def _require(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{field_name}={value!r} is not one of {sorted(allowed)}")


@dataclass(frozen=True)
class TableFinding:
    """One deterministic check result against one table -- the full-detail
    internal record produced by checks.py / anomaly_engine.py.

    Not every TableFinding is promoted into a shared.models.Incident: a
    "pass" status never is. table_id + check_name identify which Incident
    (if any) a given finding was promoted into.
    """

    table_id: str
    check_name: str
    status: CheckStatus
    severity: CheckSeverity
    observed_value: Optional[float]
    threshold: Optional[float]
    summary: str
    likely_root_cause: str
    affected_assets: list[str] = field(default_factory=list)
    sql: Optional[str] = None
    """The read-only SQL text (already run through shared.data_service.run_query())
    that produced observed_value, when this finding came from a live ratio
    query. None for metadata-only checks (empty table, stale freshness, no
    primary-key candidate), which use BigQuery's metadata API and have no
    SQL text at all. Threaded through to shared.models.Incident.sql_template
    (and hashed into Incident.query_hash) when a finding is promoted into
    an incident -- the original app never populated either field."""

    def __post_init__(self) -> None:
        _require(self.severity, _CHECK_SEVERITY_VALUES, "severity")
        _require(self.status, _CHECK_STATUS_VALUES, "status")


@dataclass(frozen=True)
class CheckDefinition:
    """One entry in the static Guardrails catalog -- what Triage checks for
    and why, independent of any specific table's current result.

    Replaces the original app's local MetricCheck; renamed to avoid
    confusion with shared.models.MetricDefinition, which is a different
    concept entirely (a certified business-metric formula, not a
    data-quality guardrail).
    """

    name: str
    description: str
    threshold: str
    severity: CheckSeverity

    def __post_init__(self) -> None:
        _require(self.severity, _CHECK_SEVERITY_VALUES, "severity")


@dataclass(frozen=True)
class SchemaSnapshot:
    """A point-in-time record of one table's column names and BigQuery
    types, used as the baseline for checks.check_schema_drift().

    Callers (ultimately main.py) supply this -- checks.py never invents,
    fetches, or persists one itself. There is no live baseline-storage
    mechanism yet (Phase 6: a real loupe_platform table to read/write
    snapshots from). Until then, main.py passes `schema_baselines=None`
    (or an empty dict), and check_schema_drift() returns an explicit
    not_evaluated finding rather than silently skipping the check or
    guessing that nothing changed -- see that function's docstring.
    """

    table_id: str
    captured_at: str
    columns: dict[str, str]  # column name -> BigQuery field_type


@dataclass(frozen=True)
class IncidentExplanation:
    """Claude-narrated explanation of an already-detected incident.

    Per the Phase 4 constraint ("Claude may explain an incident but cannot
    create, classify, or resolve one"), this model deliberately has no
    severity, status, or check_type field -- there is nothing here an LLM
    could use to reclassify or create an incident. It is pure narration
    grounded in an already-computed shared.models.Incident + TableFinding,
    produced by explanations.py, and rendered read-only by ui.py.
    `used_claude` records whether the narrative came from a live model call
    or the deterministic no-API-key fallback, matching the pattern
    established in apps/metric_governance/explanations.py.
    """

    incident_id: str
    narrative: str
    used_claude: bool
