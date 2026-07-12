"""Cross-app data contracts shared by loupe_agent, metric_governance, and
data_quality_triage.

Only genuinely cross-app shapes belong here. If a model is only ever
constructed and consumed inside a single app, it belongs in that app's own
package instead (see docs/architecture.md).

Each dataclass validates its enum-like string fields in __post_init__ rather
than relying on static Literal hints alone -- these are core domain
contracts and should fail loudly on construction if given a value outside
the documented vocabulary, per docs/contracts.md and
docs/data-quality-triage.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Enum-like string vocabularies
# ---------------------------------------------------------------------------

Severity = Literal["high", "medium", "low"]
_SEVERITY_VALUES = {"high", "medium", "low"}

IncidentStatus = Literal[
    "detected", "open", "acknowledged", "investigating", "mitigated", "resolved"
]
_INCIDENT_STATUS_VALUES = {
    "detected",
    "open",
    "acknowledged",
    "investigating",
    "mitigated",
    "resolved",
}

CertificationStatus = Literal["certified", "proposed", "pending_validation"]
_CERTIFICATION_STATUS_VALUES = {"certified", "proposed", "pending_validation"}

SourceHealthStatus = Literal["healthy", "degraded", "critical"]
_SOURCE_HEALTH_STATUS_VALUES = {"healthy", "degraded", "critical"}

TrustBand = Literal["high_trust", "review_required", "do_not_rely"]
_TRUST_BAND_VALUES = {"high_trust", "review_required", "do_not_rely"}


def _require(value: str, allowed: set[str], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(
            f"{field_name}={value!r} is not one of {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Incident (Reliability layer -> read by Loupe and Governance)
# ---------------------------------------------------------------------------


@dataclass
class Incident:
    """A deterministic data-quality incident, per docs/data-quality-triage.md.

    `status` follows the lifecycle documented there:
        detected -> open -> acknowledged -> investigating -> mitigated -> resolved
    See shared/incidents.py for the allowed-transition rules, the
    "detected" vs "open" semantics, and the active-status classification
    used for source-health propagation.
    """

    incident_id: str
    created_at: str
    dataset: str
    table_id: str
    check_type: str
    severity: Severity
    status: IncidentStatus
    observed_value: Optional[float] = None
    expected_value: Optional[float] = None
    sql_template: Optional[str] = None
    """A parameterized, read-only SQL template (identifiers only -- table
    and column names resolved from BigQuery metadata, never a bound
    literal value, credential, or raw exception message) that produced
    this incident's observed_value. Renamed from the original `sql` field
    (Phase 6, amendment 9) specifically to make it structurally clear this
    must never hold bound values or free-text error output -- callers
    that need to correlate repeated occurrences of the same check without
    storing the full text again should populate `query_hash` instead."""
    query_hash: Optional[str] = None
    """A deterministic hash (e.g. SHA-256) of `sql_template`'s executed
    query shape, for correlating recurring incidents against the same
    check without re-storing or re-comparing the full SQL text."""
    affected_metrics: list[str] = field(default_factory=list)
    affected_dashboards: list[str] = field(default_factory=list)
    playbook: Optional[str] = None
    owner: Optional[str] = None
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_notes: Optional[str] = None
    rule_version: Optional[str] = None
    recurrence_of_incident_id: Optional[str] = None
    """Set when this incident represents a new, independent occurrence of
    a condition previously tracked under a different (already-resolved)
    incident_id -- as opposed to reopening that same incident. See
    shared/incidents.py's "Reopen vs. new linked incident" section for
    the rule that decides which case applies."""

    def __post_init__(self) -> None:
        _require(self.severity, _SEVERITY_VALUES, "severity")
        _require(self.status, _INCIDENT_STATUS_VALUES, "status")


# ---------------------------------------------------------------------------
# MetricDefinition (Definition layer -> read by Loupe and Triage)
# ---------------------------------------------------------------------------


@dataclass
class MetricDefinition:
    """A certified (or proposed) metric definition, per docs/contracts.md.

    A metric is certified only when name, formula, measurement_grain,
    freshness expectation, source tables, and version are recorded and
    reviewed. Newly-extracted definitions must start as "proposed" or
    "pending_validation" -- never silently marked "certified". See
    shared/metric_catalog.py (Phase 2/5) for the promotion workflow.

    measurement_grain vs. reporting grain (Phase 5 correction; see
    docs/contracts.md's "Measurement grain vs. reporting grain" section):

    `measurement_grain` answers one question only -- "what is the atomic
    business entity this metric is defined over?" (e.g. "order_item",
    "order", "user", "session/event"). It is a property of the METRIC
    ITSELF and never changes based on how a particular query happens to
    group or filter the data.

    The dimensional/temporal shape a specific query returns (one row per
    day, one row per category, one aggregate row for a window, etc.) is
    that query's REPORTING grain, not this field. Reporting grain is
    declared per query -- see each function's docstring in
    apps/loupe_agent/metrics.py and its regression coverage in
    tests/loupe_agent/test_query_contracts.py -- never in this dataclass.
    A metric with one fixed measurement_grain can correctly back many
    different reporting grains simultaneously (a monthly trend, a
    per-category leaderboard, a single whole-window KPI, ...); that is
    normal, valid reuse of one definition, not a "grain mismatch."

    This field was previously named `grain` and, in the original Phase 5
    migration, was populated with reporting-grain-shaped text ("one row
    per day, optionally sliced by category and/or state") that did not
    match what the query functions actually returned. That was the bug:
    conflating the two concepts under one ambiguous field let a query's
    output shape silently drift out of sync with a docstring nobody
    updated. Renaming the field forces every caller to say which concept
    it means.
    """

    name: str
    owner: str
    description: str
    formula: str
    measurement_grain: str
    freshness_expectation: str
    certification_status: CertificationStatus
    approved_source_tables: list[str]
    version: str
    required_filters: list[str] = field(default_factory=list)
    downstream_dashboards: list[str] = field(default_factory=list)
    last_reviewed_at: Optional[str] = None

    def __post_init__(self) -> None:
        _require(
            self.certification_status,
            _CERTIFICATION_STATUS_VALUES,
            "certification_status",
        )


# ---------------------------------------------------------------------------
# MetricVersion (Phase 6 persistence: immutable version history) and the
# metric_catalog pointer row it is resolved through.
# ---------------------------------------------------------------------------
#
# Per Phase 6's approved amendment: MetricDefinition (above) represents the
# RESOLVED current business definition -- what a caller gets back after
# following metric_catalog's current_version pointer into the matching
# MetricVersion row. It is deliberately kept as the simple, flat shape
# every existing caller (Loupe, Triage, Governance's UI) already consumes,
# rather than being forced to grow reviewer/evidence/hash bookkeeping that
# has nothing to do with "what does this metric currently mean."
#
# MetricVersion is the separate, append-only historical record: one row
# per governed version, carrying everything about HOW and WHY that version
# came to exist, in addition to what it means. This is a genuinely
# different responsibility from MetricDefinition, so it gets its own
# dataclass rather than being bolted onto MetricDefinition's optional
# fields -- per the instruction to keep focused persistence-row models
# rather than forcing every table schema to match one shared dataclass
# field-for-field.


@dataclass
class MetricVersion:
    """One immutable, governed version of a metric's definition, plus the
    review/certification metadata for that specific version.

    `content_hash` is computed over exactly the fields that affect metric
    MEANING (see shared/metric_hashing.py's canonical field list) --
    never over reviewer, timestamps, or certification_status themselves,
    so two versions with byte-identical semantic content are always
    distinguishable from a version that changed only its approval state.

    `created_by` (who authored/proposed this version's content) and
    `reviewer` (who certified/reviewed it, if anyone yet has) are
    deliberately distinct fields and must never be conflated: a version
    can exist with `created_by` set and `reviewer=None` (proposed, not
    yet reviewed), and a later certification of that SAME content creates
    a NEW MetricVersion row (see change_reason) with `reviewer` set and
    `content_hash` unchanged from the version it supersedes.
    """

    name: str
    version: str
    description: str
    formula: str
    measurement_grain: str
    freshness_expectation: str
    certification_status: CertificationStatus
    approved_source_tables: list[str]
    content_hash: str
    created_by: str
    created_at: str
    change_reason: str
    required_filters: list[str] = field(default_factory=list)
    downstream_dashboards: list[str] = field(default_factory=list)
    prior_version: Optional[str] = None
    validation_evidence: Optional[str] = None
    review_notes: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None

    def __post_init__(self) -> None:
        _require(
            self.certification_status,
            _CERTIFICATION_STATUS_VALUES,
            "certification_status",
        )


@dataclass
class MetricCatalogPointer:
    """The metric_catalog table's current-state row: a pointer at the
    currently-active MetricVersion, plus the small amount of state that
    genuinely belongs to "the metric as a whole" rather than to any one
    version (owner, current certification_status, last_reviewed_at).

    This is NOT a duplicate of MetricDefinition -- MetricDefinition is
    the resolved, read-facing shape (pointer + version content already
    joined together) that Loupe/Triage/Governance's existing call sites
    consume. MetricCatalogPointer is the thinner, current-state-only
    persistence row that metric_catalog.py resolves into a
    MetricDefinition before handing it to a caller.
    """

    name: str
    current_version: str
    owner: str
    certification_status: CertificationStatus
    updated_at: str
    last_reviewed_at: Optional[str] = None

    def __post_init__(self) -> None:
        _require(
            self.certification_status,
            _CERTIFICATION_STATUS_VALUES,
            "certification_status",
        )


# ---------------------------------------------------------------------------
# AuditEvent (written by Governance and Triage, per docs/*.md audit sections)
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    """A single audit-log entry.

    Must contain enough structured context to reproduce the decision
    without storing secrets (docs/metric-governance.md).
    """

    event_id: str
    timestamp: str
    actor: str
    event_type: str
    subject: str
    outcome: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SourceHealth (derived read model over Incidents; consumed by Loupe + Governance)
# ---------------------------------------------------------------------------


@dataclass
class SourceHealth:
    """The current health of one warehouse table, derived from open incidents.

    This is the shape that satisfies docs/data-quality-triage.md's
    requirement that "the resulting source status must be available to
    both Loupe responses and governance SQL reviews."
    """

    dataset: str
    table_id: str
    status: SourceHealthStatus
    active_incident_ids: list[str] = field(default_factory=list)
    last_checked_at: Optional[str] = None

    def __post_init__(self) -> None:
        _require(self.status, _SOURCE_HEALTH_STATUS_VALUES, "status")


# ---------------------------------------------------------------------------
# Trust scoring result (produced by shared/trust_scoring.py in Phase 2;
# consumed by Loupe and Governance UIs)
# ---------------------------------------------------------------------------


@dataclass
class TrustScoreFactor:
    """One itemized contribution to a trust score, per docs/contracts.md:
    'The UI must expose the factors that changed the score.'
    """

    name: str
    points: int
    reason: str


@dataclass
class TrustScoreResult:
    """The full, explainable output of the deterministic trust-scoring
    function. An LLM may summarize this in prose but cannot alter it.

    `score` is always the raw arithmetic total (clamped 0-100) from the
    itemized `factors` -- it is never silently changed to match a forced
    band. When a forced override changes `band` without the arithmetic
    score itself justifying it (e.g. a critical source incident forces
    "do_not_rely" even at a middling score), `override_reason` explains
    why, so a UI or reviewer can see both numbers and understand they
    disagree on purpose.
    """

    score: int
    band: TrustBand
    scoring_version: str
    factors: list[TrustScoreFactor] = field(default_factory=list)
    override_reason: Optional[str] = None

    def __post_init__(self) -> None:
        _require(self.band, _TRUST_BAND_VALUES, "band")
        if not (0 <= self.score <= 100):
            raise ValueError(f"score={self.score!r} must be between 0 and 100")
