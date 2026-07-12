"""Deterministic remediation guidance: what to do about a SQL review
result, and how to translate that result into shared.trust_scoring's
input contract.

Both functions here are pure and deterministic -- no Streamlit, no LLM,
no BigQuery access. explanations.py may narrate the *playbooks* and the
*trust score* this module produces, but never invents or overrides them.
"""

from __future__ import annotations

from apps.metric_governance.models import SqlReviewFinding, SqlReviewResult

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
