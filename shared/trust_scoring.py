"""The single, deterministic, versioned trust-scoring function, per
docs/contracts.md.

This replaces two prior, inconsistent formulas that lived in Metric
Governance's src/triage.py (a dead, unused tiered-deduction function) and
inline in src/ui.py (`score - findings*3`). Neither incorporated source
incident status, definition mismatches, approved-table coverage, grain,
or freshness -- all of which docs/contracts.md requires:

    "Inputs include: metric certification status, source-table anomaly
    status, definition mismatch count, high-severity SQL findings,
    approved-table coverage, missing grain, missing freshness expectations.
    The UI must expose the factors that changed the score. An LLM may
    summarize the score but cannot alter it."

compute_trust_score() is a pure function: no BigQuery access, no LLM
call, no I/O of any kind. Callers (Governance's review.py, Loupe's
services.py) are responsible for fetching the MetricDefinition and
SourceHealth first (via shared/metric_catalog.py and
shared/data_service.py) and passing already-known values in. This keeps
the scoring formula itself trivially unit-testable and keeps "how do we
fetch the inputs" a separate concern from "how do we score them," per
the migration's cohesion principle.
"""

from __future__ import annotations

from shared.models import MetricDefinition, SourceHealth, TrustBand, TrustScoreFactor, TrustScoreResult

SCORING_VERSION = "v1"

# Point budget, documented and versioned alongside SCORING_VERSION so a
# future v2 can change these without silently reinterpreting v1 scores.
# Factors add up to a maximum of 100; the deterministic overrides below
# can still force the band down regardless of the arithmetic total.
_CERTIFICATION_POINTS = {
    "certified": 30,
    "proposed": 15,
    "pending_validation": 5,
}
_MISSING_DEFINITION_POINTS = 0

_SOURCE_HEALTHY_POINTS = 30
_SOURCE_DEGRADED_POINTS = 10
_SOURCE_CRITICAL_POINTS = -20
_SOURCE_UNKNOWN_POINTS = 0

_NO_MISMATCH_POINTS = 15
_ONE_MISMATCH_POINTS = 5
_MULTI_MISMATCH_POINTS = -10

_NO_FINDINGS_POINTS = 15
_MEDIUM_FINDINGS_ONLY_POINTS = 5
_HIGH_FINDINGS_POINTS = -15

_FULL_COVERAGE_POINTS = 5
_PARTIAL_COVERAGE_POINTS = 2
_LOW_COVERAGE_POINTS = -5

_GRAIN_DECLARED_POINTS = 3
_GRAIN_MISSING_POINTS = -3

_FRESHNESS_DECLARED_POINTS = 2
_FRESHNESS_MISSING_POINTS = -2

_HIGH_TRUST_THRESHOLD = 85
_REVIEW_REQUIRED_THRESHOLD = 50


def _score_certification(definition: MetricDefinition | None) -> TrustScoreFactor:
    if definition is None:
        return TrustScoreFactor(
            name="metric_certification",
            points=_MISSING_DEFINITION_POINTS,
            reason="No certified, proposed, or pending-validation definition found for this metric.",
        )
    points = _CERTIFICATION_POINTS[definition.certification_status]
    reason = {
        "certified": "Metric definition is certified.",
        "proposed": "Metric definition is proposed but not yet certified.",
        "pending_validation": "Metric definition is pending validation.",
    }[definition.certification_status]
    return TrustScoreFactor(name="metric_certification", points=points, reason=reason)


def _score_source_health(source_health: SourceHealth | None) -> TrustScoreFactor:
    if source_health is None:
        return TrustScoreFactor(
            name="source_health",
            points=_SOURCE_UNKNOWN_POINTS,
            reason="No source-health data available for the referenced table(s).",
        )
    if source_health.status == "healthy":
        return TrustScoreFactor(
            name="source_health", points=_SOURCE_HEALTHY_POINTS, reason="Source tables are healthy."
        )
    if source_health.status == "degraded":
        return TrustScoreFactor(
            name="source_health",
            points=_SOURCE_DEGRADED_POINTS,
            reason=(
                f"Source table has {len(source_health.active_incident_ids)} active, "
                "non-critical incident(s)."
            ),
        )
    return TrustScoreFactor(
        name="source_health",
        points=_SOURCE_CRITICAL_POINTS,
        reason=(
            f"Source table has an active critical incident "
            f"({len(source_health.active_incident_ids)} active incident(s) total)."
        ),
    )


def _score_definition_mismatches(count: int) -> TrustScoreFactor:
    if count <= 0:
        return TrustScoreFactor(
            name="definition_mismatches", points=_NO_MISMATCH_POINTS, reason="No definition mismatches detected."
        )
    if count == 1:
        return TrustScoreFactor(
            name="definition_mismatches", points=_ONE_MISMATCH_POINTS, reason="1 definition mismatch detected."
        )
    return TrustScoreFactor(
        name="definition_mismatches",
        points=_MULTI_MISMATCH_POINTS,
        reason=f"{count} definition mismatches detected.",
    )


def _score_review_findings(high_count: int, medium_count: int) -> TrustScoreFactor:
    if high_count > 0:
        return TrustScoreFactor(
            name="review_findings",
            points=_HIGH_FINDINGS_POINTS,
            reason=f"{high_count} high-severity review finding(s) detected.",
        )
    if medium_count > 0:
        return TrustScoreFactor(
            name="review_findings",
            points=_MEDIUM_FINDINGS_ONLY_POINTS,
            reason=f"{medium_count} medium-severity review finding(s), no high-severity findings.",
        )
    return TrustScoreFactor(
        name="review_findings", points=_NO_FINDINGS_POINTS, reason="No material review findings."
    )


def _score_approved_table_coverage(ratio: float) -> TrustScoreFactor:
    if ratio >= 1.0:
        return TrustScoreFactor(
            name="approved_table_coverage", points=_FULL_COVERAGE_POINTS, reason="All referenced tables are approved."
        )
    if ratio >= 0.5:
        return TrustScoreFactor(
            name="approved_table_coverage",
            points=_PARTIAL_COVERAGE_POINTS,
            reason=f"Partial approved-table coverage ({ratio:.0%}).",
        )
    return TrustScoreFactor(
        name="approved_table_coverage",
        points=_LOW_COVERAGE_POINTS,
        reason=f"Majority of referenced tables are not approved ({ratio:.0%} coverage).",
    )


def _score_grain(has_declared_grain: bool) -> TrustScoreFactor:
    if has_declared_grain:
        return TrustScoreFactor(name="grain_declared", points=_GRAIN_DECLARED_POINTS, reason="Grain is declared.")
    return TrustScoreFactor(name="grain_declared", points=_GRAIN_MISSING_POINTS, reason="Grain is not declared.")


def _score_freshness(has_freshness_expectation: bool) -> TrustScoreFactor:
    if has_freshness_expectation:
        return TrustScoreFactor(
            name="freshness_declared", points=_FRESHNESS_DECLARED_POINTS, reason="Freshness expectation is declared."
        )
    return TrustScoreFactor(
        name="freshness_declared",
        points=_FRESHNESS_MISSING_POINTS,
        reason="Freshness expectation is not declared.",
    )


def _band_for_score(score: int) -> TrustBand:
    if score >= _HIGH_TRUST_THRESHOLD:
        return "high_trust"
    if score >= _REVIEW_REQUIRED_THRESHOLD:
        return "review_required"
    return "do_not_rely"


def compute_trust_score(
    *,
    definition: MetricDefinition | None,
    source_health: SourceHealth | None,
    definition_mismatch_count: int = 0,
    high_severity_finding_count: int = 0,
    medium_severity_finding_count: int = 0,
    approved_table_coverage_ratio: float = 1.0,
    has_declared_grain: bool = True,
    has_freshness_expectation: bool = True,
) -> TrustScoreResult:
    """Compute a deterministic, itemized, versioned trust score.

    Scoring is additive: each factor contributes points on its own scale
    (see the _*_POINTS constants above), summed and clamped to [0, 100].
    The resulting numeric score is then mapped to a band -- but two
    conditions force "do_not_rely" regardless of the arithmetic total,
    per docs/contracts.md's explicit language ("Do not rely: active
    critical incident ... or unsafe query behavior"):

      1. source_health.status == "critical"
      2. high_severity_finding_count > 0

    Without this override, a metric could rack up enough points elsewhere
    (certification, grain, freshness, coverage) to arithmetically land in
    "review_required" territory while an active critical incident or a
    high-severity SQL finding is still live -- which would contradict the
    documented interpretation. The override makes that impossible.
    """

    factors = [
        _score_certification(definition),
        _score_source_health(source_health),
        _score_definition_mismatches(definition_mismatch_count),
        _score_review_findings(high_severity_finding_count, medium_severity_finding_count),
        _score_approved_table_coverage(approved_table_coverage_ratio),
        _score_grain(has_declared_grain),
        _score_freshness(has_freshness_expectation),
    ]

    raw_score = sum(factor.points for factor in factors)
    score = max(0, min(100, raw_score))
    band = _band_for_score(score)

    override_causes: list[str] = []
    if source_health is not None and source_health.status == "critical":
        override_causes.append("source health is critical")
    if high_severity_finding_count > 0:
        override_causes.append(
            f"{high_severity_finding_count} high-severity review finding(s) are present"
        )

    override_reason: str | None = None
    if override_causes:
        band = "do_not_rely"
        override_reason = (
            "Trust band forced to do_not_rely regardless of the arithmetic "
            f"score ({score}): " + "; ".join(override_causes) + "."
        )
    # `score` above is never touched by this override -- it stays the raw,
    # honest arithmetic total. Only `band` and `override_reason` change.

    return TrustScoreResult(
        score=score,
        band=band,
        scoring_version=SCORING_VERSION,
        factors=factors,
        override_reason=override_reason,
    )
