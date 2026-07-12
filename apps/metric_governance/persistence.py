"""Phase 6D persistence wiring for Metric Governance.

A thin app-level wrapper over shared/metric_catalog_persistence.py and
shared/data_service.py, following the same discipline as
apps/data_quality_triage/persistence.py: apps/ never registers a template
or calls execute_transaction() directly -- only the persistence-layer
functions those modules already expose.

Four responsibilities:

  read_catalog() -- resolve every known catalogued metric's current
    definition through shared.metric_catalog_persistence.
    resolve_current_definition(). If persisted mode is configured but
    storage is unreachable, returns catalog_unavailable=True and an
    EMPTY definitions list -- never a silent fallback to
    shared.metric_catalog's in-memory constants (that fallback exists
    only as its own, separate, explicitly-configured "constants mode,"
    never an automatic substitute for an unavailable persisted read).

  source_health_for_definition() -- the worst SourceHealth across a
    definition's approved_source_tables, plus the active incidents that
    produced it, so the UI can display exactly which incidents/tables
    affected a metric's trust score.

  trust_score_for_definition() -- wires that worst-source-health result
    into shared.trust_scoring.compute_trust_score(), the single
    deterministic scoring function every app must use unchanged.

  certify_definition() -- a thin, explicit pass-through to
    shared.metric_catalog_persistence.certify_metric_definition(). Never
    called from build_state() or any automatic path -- only from a
    human-triggered UI action (ui.py's Catalog page "Certify" button),
    exactly like triage's promote_schema_baseline_now().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shared.data_service import BigQueryClientLike, SourceHealth, derive_source_health, list_active_incidents_for_table
from shared.metric_catalog_persistence import (
    MetricCertificationResult,
    MetricDefinitionResolution,
    resolve_current_definition,
)
from shared.models import Incident, MetricDefinition
from shared.persistence_transactions import TransactionalClientLike
from shared.trust_scoring import compute_trust_score

# The fixed set of metric identities this platform governs -- the same
# five names the administrative metric-catalog seeding CLI populates from
# shared.metric_catalog's in-memory registry (see
# shared/metric_catalog_persistence.py's module docstring). This list is
# domain knowledge about "which metrics exist," not the metrics' live
# definitions -- the definitions themselves are always resolved fresh,
# per-call, never read from constants while in persisted mode.
KNOWN_METRIC_NAMES: tuple[str, ...] = (
    "revenue",
    "margin",
    "return_rate",
    "margin_leakage",
    "channel_mix",
)

QUALIFIED_DATASET = "bigquery-public-data.thelook_ecommerce"

_HEALTH_RANK = {"critical": 3, "degraded": 2, "healthy": 1}


@dataclass(frozen=True)
class CatalogReadResult:
    """The outcome of read_catalog(): either a real list of resolved
    definitions, or an honest "catalog unavailable" state -- never both
    partially populated and silently treated as complete."""

    definitions: list[MetricDefinition]
    catalog_unavailable: bool
    safe_error: Optional[str] = None


def read_catalog(client: "BigQueryClientLike") -> CatalogReadResult:
    """Resolve every KNOWN_METRIC_NAMES entry through
    shared.metric_catalog_persistence.resolve_current_definition().

    If ANY resolution reports ok=False (storage unreachable), the whole
    result is reported catalog_unavailable=True with an empty definitions
    list -- a partially-successful catalog read would be misleading (a
    caller seeing 3 of 5 metrics has no way to know 2 were silently
    dropped rather than genuinely uncatalogued). A metric that resolves
    ok=True but definition=None (not yet seeded) is simply omitted from
    `definitions`, which is a normal, non-error outcome.
    """

    definitions: list[MetricDefinition] = []
    for name in KNOWN_METRIC_NAMES:
        resolution: MetricDefinitionResolution = resolve_current_definition(client, name)
        if not resolution.ok:
            return CatalogReadResult(definitions=[], catalog_unavailable=True, safe_error=resolution.safe_error)
        if resolution.definition is not None:
            definitions.append(resolution.definition)
    return CatalogReadResult(definitions=definitions, catalog_unavailable=False, safe_error=None)


@dataclass(frozen=True)
class SourceEvidence:
    """Evidence backing a definition's trust score: per-table health plus
    the active incidents behind it, so the UI can show exactly what
    affected the score -- never just a bare number."""

    worst_health: Optional[SourceHealth]
    table_health: list[SourceHealth]
    active_incidents: list[Incident]


def source_health_for_definition(client: "BigQueryClientLike", definition: MetricDefinition) -> SourceEvidence:
    """Derive source health for every table `definition` approves, and
    return the worst one plus the full evidence trail.

    Any exception (persistence unavailable) degrades to an honest "no
    evidence available" SourceEvidence, never a fabricated healthy
    result -- the caller (trust_score_for_definition()) then passes
    source_health=None into compute_trust_score(), which scores that as
    zero points, never as healthy.
    """

    table_health: list[SourceHealth] = []
    active_incidents: list[Incident] = []
    try:
        for table in definition.approved_source_tables:
            health = derive_source_health(client, QUALIFIED_DATASET, table)
            table_health.append(health)
            active_incidents.extend(list_active_incidents_for_table(client, QUALIFIED_DATASET, table))
    except Exception:  # noqa: BLE001 -- storage-unavailable degrades to "no evidence," never a crash or fake health
        return SourceEvidence(worst_health=None, table_health=[], active_incidents=[])

    if not table_health:
        return SourceEvidence(worst_health=None, table_health=[], active_incidents=[])

    worst = max(table_health, key=lambda h: _HEALTH_RANK.get(h.status, 0))
    return SourceEvidence(worst_health=worst, table_health=table_health, active_incidents=active_incidents)


@dataclass(frozen=True)
class DefinitionTrust:
    definition: MetricDefinition
    evidence: SourceEvidence
    trust: "object"  # shared.models.TrustScoreResult


def trust_score_for_definition(client: "BigQueryClientLike", definition: MetricDefinition) -> DefinitionTrust:
    """Compute this definition's deterministic trust score, incorporating
    the worst active source health across its approved_source_tables --
    the concrete link between a persisted Triage incident and a
    Governance trust score this phase's cross-app workflow demonstrates."""

    evidence = source_health_for_definition(client, definition)
    trust = compute_trust_score(
        definition=definition,
        source_health=evidence.worst_health,
        approved_table_coverage_ratio=1.0,
        has_declared_grain=bool(definition.measurement_grain),
        has_freshness_expectation=bool(
            definition.freshness_expectation and "undeclared" not in definition.freshness_expectation.lower()
        ),
    )
    return DefinitionTrust(definition=definition, evidence=evidence, trust=trust)


# ---------------------------------------------------------------------------
# Governed certification -- explicit human action only. Never called from
# build_state() or any automatic/startup path. See ui.py's Catalog page
# "Certify" button, the only call site.
# ---------------------------------------------------------------------------


def certify_definition(
    client: "TransactionalClientLike",
    *,
    name: str,
    new_version: str,
    expected_current_version: str,
    description: str,
    formula: str,
    measurement_grain: str,
    freshness_expectation: str,
    approved_source_tables: list[str],
    created_by: str,
    reviewer: str,
    validation_evidence: str,
    reviewed_at: str,
    change_reason: str,
    event_id: str,
    require_separation_of_duties: bool,
    required_filters: Optional[list[str]] = None,
    downstream_dashboards: Optional[list[str]] = None,
    review_notes: Optional[str] = None,
) -> MetricCertificationResult:
    """Human-triggered pass-through to
    shared.metric_catalog_persistence.certify_metric_definition() --
    `require_separation_of_duties` must be threaded through explicitly by
    the caller (e.g. from shared.config.PlatformConfig.
    strict_separation_of_duties), never defaulted silently here."""

    from shared.metric_catalog_persistence import certify_metric_definition

    return certify_metric_definition(
        client,
        name=name,
        new_version=new_version,
        expected_current_version=expected_current_version,
        description=description,
        formula=formula,
        measurement_grain=measurement_grain,
        freshness_expectation=freshness_expectation,
        approved_source_tables=approved_source_tables,
        created_by=created_by,
        reviewer=reviewer,
        validation_evidence=validation_evidence,
        reviewed_at=reviewed_at,
        change_reason=change_reason,
        event_id=event_id,
        required_filters=required_filters,
        downstream_dashboards=downstream_dashboards,
        review_notes=review_notes,
        require_separation_of_duties=require_separation_of_duties,
    )
