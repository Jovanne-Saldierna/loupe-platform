"""Metric Governance's own presentation-layer models.

These are deliberately NOT the same thing as shared.models.MetricDefinition.
That shared model is the cross-app certified-catalog contract (consumed by
Loupe, Triage, and this app) -- see shared/metric_catalog.py. Everything in
*this* file is Governance-specific output shaped for its own SQL-review and
definition-diff views, and is only ever constructed and consumed inside this
app, so per docs/architecture.md it belongs here, not in shared/.

An earlier draft of this app had its own local MetricDefinition (with
certified_sql/common_misuse fields, keyed by metric_id) sourced from
hardcoded ARR sample data. That model has been removed: Catalog and
Definition Diff now read real, pending_validation metric definitions from
shared.metric_catalog, per the approved Phase 3 migration decision. Any ARR
example data that remains lives only in tests/, explicitly labeled
fictional, and is never loaded by the running app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Review-finding severity is intentionally its own, 4-level vocabulary
# (low/medium/high/critical) distinct from shared.models.Severity's 3-level
# incident vocabulary (high/medium/low) -- these describe different things
# (a SQL lint finding vs. a data-quality incident) and happen to share no
# guaranteed 1:1 mapping, so conflating them would be a false cognate.
ReviewSeverity = Literal["low", "medium", "high", "critical"]


@dataclass(frozen=True)
class DefinitionDiff:
    """A structured, deterministic comparison between two metric
    definitions -- the output of definition_diff.py's compare_definitions(),
    never hand-authored and never LLM-generated. explanations.py may narrate
    *why* a diff matters, but the diff's matches/differences/recommended_use
    themselves come only from comparing the two MetricDefinition objects'
    actual fields.
    """

    left_name: str
    right_name: str
    matches: list[str]
    differences: list[str]
    recommended_use: str


@dataclass(frozen=True)
class SqlReviewFinding:
    severity: ReviewSeverity
    category: str
    message: str


@dataclass(frozen=True)
class SqlReviewResult:
    score: int
    summary: str
    findings: list[SqlReviewFinding]
    referenced_tables: list[str]
    recommended_next_steps: list[str]


# Definition-change risk status for one category (see remediation.py's
# derive_change_risk()): "aligned" means the deterministic review/metadata
# found no gap for that category, "risk" means a real gap was found, and
# "unknown" means there wasn't enough evidence to judge either way (e.g. no
# tables were referenced at all, or source health couldn't be resolved) --
# "unknown" is never silently upgraded to "aligned" or "risk".
ChangeRiskStatus = Literal["aligned", "risk", "unknown"]


@dataclass(frozen=True)
class ChangeRiskCategory:
    """One deterministic definition-change-risk category, derived only
    from an already-computed SqlReviewResult plus the governed
    MetricDefinition's own fields (see remediation.py's
    derive_change_risk()) -- never a fabricated or LLM-authored diff."""

    category: str
    status: ChangeRiskStatus
    detail: str


# A recommendation's urgency: "info" is a passive status note (e.g.
# "approve"), "required" means action should happen before the definition
# is broadly relied on, and "blocking" means it should not be used for
# executive reporting until resolved.
RecommendationPriority = Literal["info", "required", "blocking"]


@dataclass(frozen=True)
class GovernanceRecommendationItem:
    """One deterministic governance recommendation, derived only from
    already-computed review/trust/change-risk fields (see remediation.py's
    derive_governance_recommendations()) -- never invented by, or only
    surfaced through, the Ask Loupe helper."""

    action: str
    rationale: str
    priority: RecommendationPriority


@dataclass(frozen=True)
class CompletenessCheck:
    """One governance-completeness rule check for a metric (Steward
    Summary's "Consistency Checks" requirement) -- see remediation.py's
    derive_governance_completeness(). `passed` and `detail` are both
    derived directly from the governed MetricDefinition's own fields plus
    already-resolved source health/incident evidence; nothing here is a
    subjective judgment or an AI narration."""

    label: str
    passed: bool
    detail: str
