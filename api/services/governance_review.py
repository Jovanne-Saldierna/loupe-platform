from __future__ import annotations

from typing import Any

from api.models import (
    CatalogMetric,
    ContractAlignment,
    GovernanceCatalogResponse,
    GovernanceReviewResponse,
    ReviewFinding,
    TrustFactor,
)
from apps.metric_governance.persistence import read_catalog, source_health_for_definition
from apps.metric_governance.remediation import trust_score_inputs_from_review
from apps.metric_governance.review import review_sql
from shared.trust_scoring import compute_trust_score


class CatalogUnavailableError(RuntimeError):
    pass


class MetricNotFoundError(ValueError):
    pass


def _catalog(client: Any):
    result = read_catalog(client)
    if result.catalog_unavailable:
        raise CatalogUnavailableError("The persisted metric catalog is unavailable.")
    return result.definitions


def list_governed_metrics(client: Any) -> GovernanceCatalogResponse:
    return GovernanceCatalogResponse(
        metrics=[
            CatalogMetric(
                name=item.name,
                version=item.version,
                certification_status=item.certification_status,
                measurement_grain=item.measurement_grain,
            )
            for item in _catalog(client)
        ]
    )


def build_governance_review(client: Any, sql: str, metric_name: str) -> GovernanceReviewResponse:
    definitions = _catalog(client)
    definition = next((item for item in definitions if item.name == metric_name), None)
    if definition is None:
        raise MetricNotFoundError(f"Metric {metric_name!r} is not registered in the persisted catalog.")

    approved_tables = sorted({table for item in definitions for table in item.approved_source_tables})
    review = review_sql(sql, approved_tables)
    evidence = source_health_for_definition(client, definition)
    inputs = trust_score_inputs_from_review(review, approved_tables)
    trust = compute_trust_score(definition=definition, source_health=evidence.worst_health, **inputs)
    finding_categories = {finding.category for finding in review.findings}

    approved_observed = [table for table in review.referenced_tables if table in definition.approved_source_tables]
    unapproved_observed = [table for table in review.referenced_tables if table not in approved_tables]
    source_status = evidence.worst_health.status if evidence.worst_health else "unknown"

    return GovernanceReviewResponse(
        metric=CatalogMetric(
            name=definition.name,
            version=definition.version,
            certification_status=definition.certification_status,
            measurement_grain=definition.measurement_grain,
        ),
        review_score=review.score,
        summary=review.summary,
        findings=[ReviewFinding(severity=f.severity, category=f.category, message=f.message) for f in review.findings],
        referenced_tables=review.referenced_tables,
        recommended_next_steps=review.recommended_next_steps,
        trust_score=trust.score,
        trust_band=trust.band,
        scoring_version=trust.scoring_version,
        trust_factors=[TrustFactor(name=f.name, points=f.points, reason=f.reason) for f in trust.factors],
        override_reason=trust.override_reason,
        source_health=source_status,
        active_incident_ids=[incident.incident_id for incident in evidence.active_incidents],
        alignment=[
            ContractAlignment(
                contract="Measurement grain",
                expected=definition.measurement_grain,
                observed=definition.measurement_grain if "Grain" not in finding_categories else "undeclared",
                status="Aligned" if "Grain" not in finding_categories else "Review",
            ),
            ContractAlignment(
                contract="Approved source tables",
                expected=", ".join(definition.approved_source_tables),
                observed=", ".join(review.referenced_tables) or "none detected",
                status="Review" if unapproved_observed or not approved_observed else "Aligned",
            ),
            ContractAlignment(
                contract="Source health",
                expected="Healthy",
                observed=source_status.capitalize(),
                status="Aligned" if source_status == "healthy" else "Review",
            ),
        ],
    )
