"""Deterministic remediation guidance: what to do about a SQL review
result, and how to translate that result into shared.trust_scoring's
input contract.

Both functions here are pure and deterministic -- no Streamlit, no LLM,
no BigQuery access. explanations.py may narrate the *playbooks* and the
*trust score* this module produces, but never invents or overrides them.
"""

from __future__ import annotations

from apps.metric_governance.models import (
    ChangeRiskCategory,
    GovernanceRecommendationItem,
    SqlReviewFinding,
    SqlReviewResult,
)
from shared.models import MetricDefinition

# Severities that are serious enough to count as "high_severity" for
# shared.trust_scoring.compute_trust_score's override, per that module's
# contract: any high-severity finding forces "do_not_rely" regardless of
# the arithmetic score. Governance's own ReviewSeverity has a 4th level
# ("critical") beyond shared.models.Severity's 3 -- both "critical" and
# "high" review findings are serious enough to count here.
_HIGH_SEVERITY_REVIEW_LEVELS = {"critical", "high"}
_MEDIUM_SEVERITY_REVIEW_LEVELS = {"medium"}


def suggested_playbooks_for_review(score: int, findings: list[SqlReviewFinding]) -> list[str]:
    """Deterministic next-action suggestions for a SQL review result.

    Migrated unchanged from the original app's src/triage.py -- this was
    already a pure, rule-based function with no LLM involvement, despite
    living in a file named triage.py alongside genuinely LLM-backed
    functions. Splitting it out here is what makes that distinction
    explicit going forward.
    """

    playbooks = [
        "Compare the SQL grain to the certified metric grain before reuse.",
        "Validate the referenced tables against the approved catalog and owner.",
        "Check whether any downstream dashboard depends on a conflicting definition.",
    ]
    if score < 80:
        playbooks.insert(0, "Route the query through a steward or analytics lead before publishing.")
    if any(finding.category == "Approved Tables" for finding in findings):
        playbooks.append("Resolve table approval gaps before the query is used in leadership reporting.")
    if any(finding.category == "Projection" for finding in findings):
        playbooks.append("Replace SELECT * with an explicit projection from the certified model.")
    return playbooks[:4]


def trust_score_inputs_from_review(result: SqlReviewResult, approved_tables: list[str]) -> dict:
    """Translate a SqlReviewResult into the keyword arguments
    shared.trust_scoring.compute_trust_score() expects for
    definition_mismatch_count, high/medium_severity_finding_count,
    approved_table_coverage_ratio, has_declared_grain, and
    has_freshness_expectation.

    Callers still supply `definition` and `source_health` themselves
    (from shared.metric_catalog and shared.data_service respectively) --
    this function only covers the inputs derivable from the review result
    itself. Every mapping below is a documented, deterministic rule, not
    a guess:

    - definition_mismatch_count: how many referenced tables are not in
      the approved catalog. Recomputed directly from
      result.referenced_tables vs. approved_tables (not by counting
      findings), so it stays correct even if review.py's finding
      messages change wording later.
    - high_severity_finding_count / medium_severity_finding_count: a
      straight count of this result's findings by severity, with
      Governance's "critical" review findings counted as high-severity
      for trust-scoring purposes (see _HIGH_SEVERITY_REVIEW_LEVELS).
    - approved_table_coverage_ratio: the fraction of referenced tables
      that ARE approved. Defined as 1.0 when no tables were referenced
      at all -- that case is already flagged separately by review.py's
      "Lineage" finding, so it should not also silently zero out
      coverage here.
    - has_declared_grain: True unless review.py raised its "Grain"
      finding (aggregate logic without an explicit GROUP BY/DISTINCT).
    - has_freshness_expectation: True unless review.py raised its
      "Filters" finding (missing WHERE-clause business/freshness
      filters).
    """

    unapproved_count = sum(1 for table in result.referenced_tables if table not in approved_tables)

    high_count = sum(1 for f in result.findings if f.severity in _HIGH_SEVERITY_REVIEW_LEVELS)
    medium_count = sum(1 for f in result.findings if f.severity in _MEDIUM_SEVERITY_REVIEW_LEVELS)

    if result.referenced_tables:
        approved_count = len(result.referenced_tables) - unapproved_count
        coverage_ratio = approved_count / len(result.referenced_tables)
    else:
        coverage_ratio = 1.0

    has_declared_grain = not any(f.category == "Grain" for f in result.findings)
    has_freshness_expectation = not any(f.category == "Filters" for f in result.findings)

    return {
        "definition_mismatch_count": unapproved_count,
        "high_severity_finding_count": high_count,
        "medium_severity_finding_count": medium_count,
        "approved_table_coverage_ratio": coverage_ratio,
        "has_declared_grain": has_declared_grain,
        "has_freshness_expectation": has_freshness_expectation,
    }


# ---------------------------------------------------------------------------
# Definition-change risk -- five fixed categories, always returned in the
# same order, each derived deterministically from fields review_sql() and
# the governed MetricDefinition already produced. Per the Definition Diff
# product requirement, this is explicitly NOT a formal diff against a
# second proposed MetricDefinition (the SQL review only ever receives raw
# SQL, never a full proposed definition) -- it is a categorized read of the
# SAME deterministic review findings and metadata already computed
# elsewhere in this module and in review.py, relabeled for the "what kind
# of drift is this" question. Nothing here is invented: a category with no
# supporting finding/metadata gap is reported "aligned", and a category
# with insufficient evidence (no tables referenced, no source health
# available) is reported "unknown" rather than guessed either way.
# ---------------------------------------------------------------------------

_CALCULATION_DRIFT_CATEGORIES = {"Projection", "Join Logic", "Syntax"}


def derive_change_risk(
    review: SqlReviewResult,
    definition: MetricDefinition,
    source_status: str,
) -> list[ChangeRiskCategory]:
    """Categorize `review`'s findings (plus `definition` metadata and
    `source_status`) into the five fixed Definition Diff risk categories.
    `source_status` is whatever shared.data_service.SourceHealth.status
    already resolved to ("healthy"/"degraded"/"critical") or "unknown" when
    no evidence was available -- the same value already shown as Governance's
    "Source health" field."""

    finding_categories = {f.category for f in review.findings}
    approved = set(definition.approved_source_tables)
    approved_observed = [t for t in review.referenced_tables if t in approved]
    unapproved_observed = [t for t in review.referenced_tables if t not in approved]

    categories: list[ChangeRiskCategory] = []

    # Calculation drift -- SELECT *, missing JOIN conditions, or unparseable
    # SQL are the only signals review_sql() raises about the query's actual
    # calculation logic (it never parses formula semantics beyond that).
    drift_findings = [f for f in review.findings if f.category in _CALCULATION_DRIFT_CATEGORIES]
    if drift_findings:
        detail = " ".join(f"{f.category}: {f.message}" for f in drift_findings)
        categories.append(ChangeRiskCategory("Calculation drift", "risk", detail))
    else:
        categories.append(ChangeRiskCategory(
            "Calculation drift", "aligned",
            "No calculation-logic findings (SELECT *, missing join conditions, or unparseable SQL) were raised by the deterministic SQL review.",
        ))

    # Source table mismatch -- same signal as the "Approved source tables"
    # contract-alignment row, categorized here for the diff view.
    if not review.referenced_tables:
        categories.append(ChangeRiskCategory(
            "Source table mismatch", "unknown", "No table references were detected in the submitted SQL.",
        ))
    elif unapproved_observed:
        categories.append(ChangeRiskCategory(
            "Source table mismatch", "risk",
            f"References {', '.join(unapproved_observed)}, which "
            f"{'is' if len(unapproved_observed) == 1 else 'are'} not in {definition.name}'s "
            f"approved source tables ({', '.join(definition.approved_source_tables) or 'none on file'}).",
        ))
    else:
        categories.append(ChangeRiskCategory(
            "Source table mismatch", "aligned",
            f"All referenced tables ({', '.join(approved_observed)}) are in {definition.name}'s approved source tables.",
        ))

    # Grain mismatch -- review.py's "Grain" finding fires when aggregate
    # logic doesn't make its grouping explicit.
    grain_finding = next((f for f in review.findings if f.category == "Grain"), None)
    if grain_finding is not None:
        categories.append(ChangeRiskCategory(
            "Grain mismatch", "risk",
            f"{grain_finding.message} Certified measurement grain is \"{definition.measurement_grain}\".",
        ))
    else:
        categories.append(ChangeRiskCategory(
            "Grain mismatch", "aligned",
            f"No grain findings raised; certified measurement grain is \"{definition.measurement_grain}\".",
        ))

    # Filter/status mismatch -- review.py's "Filters" finding (missing WHERE
    # clause), plus whether the definition itself is certified yet.
    filters_finding = next((f for f in review.findings if f.category == "Filters"), None)
    status_risk = definition.certification_status != "certified"
    if filters_finding is not None or status_risk:
        parts = []
        if filters_finding is not None:
            parts.append(filters_finding.message)
        if status_risk:
            parts.append(f"Metric certification status is \"{definition.certification_status}\", not certified.")
        categories.append(ChangeRiskCategory("Filter/status mismatch", "risk", " ".join(parts)))
    else:
        categories.append(ChangeRiskCategory(
            "Filter/status mismatch", "aligned",
            f"Filters look declared and certification status is \"{definition.certification_status}\".",
        ))

    # Freshness/SLA mismatch -- the worst resolved source health for this
    # metric's approved tables against its declared freshness expectation.
    if source_status == "healthy":
        categories.append(ChangeRiskCategory(
            "Freshness/SLA mismatch", "aligned",
            f"Source health is healthy against a freshness expectation of \"{definition.freshness_expectation}\".",
        ))
    elif source_status in ("degraded", "critical"):
        categories.append(ChangeRiskCategory(
            "Freshness/SLA mismatch", "risk",
            f"Source health for {definition.name}'s approved tables is \"{source_status}\" against a "
            f"freshness expectation of \"{definition.freshness_expectation}\".",
        ))
    else:
        categories.append(ChangeRiskCategory(
            "Freshness/SLA mismatch", "unknown",
            "Source health could not be resolved, so freshness/SLA risk cannot be judged yet.",
        ))

    return categories


def derive_governance_recommendations(
    *,
    trust_band: str,
    trust_score: int,
    review_score: int,
    findings: list[SqlReviewFinding],
    change_risk: list[ChangeRiskCategory],
    definition: MetricDefinition,
    source_status: str,
    active_incident_ids: list[str],
) -> list[GovernanceRecommendationItem]:
    """Deterministic governance recommendations, derived only from the
    review/trust/change-risk fields already computed above and passed in by
    the caller (api/services/governance_review.py) -- no field here is
    re-derived independently or guessed; every rationale quotes the real
    input it's based on. Ask Loupe may narrate *why* these matter, but this
    function -- not the model -- decides which recommendations exist."""

    recs: list[GovernanceRecommendationItem] = []

    def add(action: str, rationale: str, priority: str) -> None:
        # Keep at most one recommendation per action label so two
        # independent triggers for the same action (e.g. "Update
        # documentation" from both certification status and change-risk)
        # don't render as duplicate cards -- the first, most specific
        # rationale wins.
        if any(r.action == action for r in recs):
            return
        recs.append(GovernanceRecommendationItem(action=action, rationale=rationale, priority=priority))

    high_findings = [f for f in findings if f.severity in ("high", "critical")]

    if trust_band == "do_not_rely" or high_findings:
        if high_findings:
            reason = "; ".join(f"[{f.severity}] {f.category}: {f.message}" for f in high_findings)
        else:
            reason = f"Trust score is {trust_score} (band={trust_band})."
        add("Block for executive reporting", f"This query is not safe for executive reporting yet: {reason}", "blocking")
    elif trust_band == "review_required" or review_score < 80:
        add(
            "Needs review",
            f"Trust score is {trust_score} (band={trust_band}); review score is {review_score}/100 -- "
            "route through a steward or analytics lead before relying on this for reporting.",
            "required",
        )
    else:
        add(
            "Approve",
            f"Trust score is {trust_score} (band={trust_band}) and the deterministic review found no "
            "high-severity findings.",
            "info",
        )

    if active_incident_ids:
        add(
            "Resolve source incident",
            "Active data-quality incident(s) on this metric's source tables: " + ", ".join(active_incident_ids) + ".",
            "blocking",
        )
    elif source_status in ("degraded", "critical"):
        add(
            "Resolve source incident",
            f"Source health for {definition.name}'s approved tables is currently \"{source_status}\".",
            "required",
        )

    if definition.certification_status != "certified":
        add(
            "Update documentation",
            f"{definition.name} is currently \"{definition.certification_status}\", not a certified "
            "definition -- documentation and certification should be completed before broad reliance.",
            "required",
        )

    if not definition.owner or not definition.owner.strip():
        add("Assign owner", f"{definition.name} has no owner on file in the catalog.", "required")

    calc_risk = any(c.category == "Calculation drift" and c.status == "risk" for c in change_risk)
    grain_risk = any(c.category == "Grain mismatch" and c.status == "risk" for c in change_risk)
    any_risk = [c.category for c in change_risk if c.status == "risk"]

    if calc_risk and grain_risk:
        add(
            "Deprecate old definition",
            "Both calculation logic and measurement grain diverge from the certified definition -- the "
            "certified definition may no longer reflect how this metric is actually computed and should "
            "be reviewed for deprecation or re-certification.",
            "required",
        )
    elif any_risk:
        add(
            "Update documentation",
            f"Definition-change risk detected in: {', '.join(any_risk)}. Confirm the certified definition "
            "still matches how this metric is actually computed.",
            "required",
        )

    return recs
