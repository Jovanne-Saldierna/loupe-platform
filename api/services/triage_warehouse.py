from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.models import (
    AuditTrailEntry,
    IncidentTransitionResponse,
    LineageMetric,
    TriageIncident,
    TriageLineage,
    TriageTableHealth,
    TriageWarehouseResponse,
)
from apps.data_quality_triage.incident_lifecycle import (
    acknowledge_incident,
    begin_investigation,
    mark_mitigated,
    next_allowed_statuses,
    reopen_incident,
    resolve_incident,
)
from apps.data_quality_triage.profiling import QUALIFIED_DATASET
from apps.data_quality_triage.seed_incidents import seed_row_if_needed
from apps.metric_governance.persistence import read_catalog
from shared.config import PlatformConfig
from shared.data_service import derive_source_health, get_table_metadata, run_query
from shared.incidents import ACTIVE_INCIDENT_STATUSES


class TriageUnavailableError(RuntimeError):
    pass


def _governed_tables_and_metric_map(
    client: Any,
) -> tuple[list[str], dict[str, list[str]], dict[str, list[LineageMetric]]]:
    """Resolve the governed table set and, alongside it, a table_id ->
    governed metric names map -- the reverse of Governance's
    active_incident_ids (metric -> incidents via approved_source_tables) --
    plus a table_id -> lineage (metric name + that metric's own
    downstream_dashboards) map for the lineage/downstream-impact view. All
    three are derived from the same persisted catalog read Triage already
    depends on, so this adds no new data source and no new failure mode.
    downstream_dashboards comes straight from shared.models.MetricDefinition
    (seeded conservatively in shared/metric_catalog.py, e.g. "loupe_agent
    dashboard: KPI summary, revenue trend" for revenue) -- never invented
    here; an empty list simply means the catalog has no downstream asset on
    file yet for that metric."""
    catalog = read_catalog(client)
    if catalog.catalog_unavailable:
        raise TriageUnavailableError("The persisted catalog is unavailable.")
    tables = sorted({table for definition in catalog.definitions for table in definition.approved_source_tables})
    metrics_by_table: dict[str, list[str]] = {}
    lineage_by_table: dict[str, list[LineageMetric]] = {}
    for definition in catalog.definitions:
        for table in definition.approved_source_tables:
            metrics_by_table.setdefault(table, []).append(definition.name)
            lineage_by_table.setdefault(table, []).append(
                LineageMetric(name=definition.name, downstream_dashboards=list(definition.downstream_dashboards))
            )
    return tables, metrics_by_table, lineage_by_table


def _downstream_assets_for_table(table_id: str, lineage_by_table: dict[str, list[LineageMetric]]) -> list[str]:
    """Deduplicated, order-stable union of every downstream dashboard/asset
    name across all governed metrics on `table_id` -- a flattened
    convenience view of the same lineage data TriageWarehouseResponse.lineage
    carries in full, for grounding an incident's playbook/detail panel
    without a second lookup."""
    seen: dict[str, None] = {}
    for metric in lineage_by_table.get(table_id, []):
        for asset in metric.downstream_dashboards:
            seen.setdefault(asset, None)
    return list(seen.keys())


def _incident_audit_trail(
    row: dict,
    *,
    table_metadata_loaded_at: str | None,
) -> list[AuditTrailEntry]:
    """Deterministic, grounded audit-trail facts for one incident -- never
    AI-narrated. Three steps, each tied to a real fact this function
    already has on hand:
      1. metadata_loaded  -- the table metadata read that fed source-health
         and freshness derivation (see build_warehouse_health's own
         get_table_metadata() call). Timestamp is the table's own
         last-modified time when known, honestly None otherwise -- never
         backfilled with "now" to look complete.
      2. check_evaluated   -- the deterministic check (check_type) that
         actually produced this incident.
      3. incident_generated -- the incident record itself.
    AI-narrated steps ("ai_playbook_generated", "helper_question_asked")
    are NOT added here -- those only happen if/when a user actually
    triggers them client-side, so they are appended by the frontend from
    the real response it receives (see triage-web/app/page.tsx), never
    fabricated ahead of time by the backend."""

    created_at = row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"])
    table_id = row["table_id"]
    seeded = bool(row.get("_seeded"))
    # A seeded incident (see apps/data_quality_triage/seed_incidents.py) is
    # labeled honestly here, not presented as a live detection -- it only
    # ever appears when the persisted incidents table has zero active rows.
    check_source = (
        "apps.data_quality_triage.seed_incidents (seeded -- no live persisted incident on file)"
        if seeded
        else "apps.data_quality_triage.checks"
    )
    check_description = (
        f"Seeded check '{row['check_type']}' on {table_id}, modeled on "
        "apps.data_quality_triage.checks.check_stale_freshness -- no live "
        "persisted incident currently exists for this table."
        if seeded
        else f"Deterministic check '{row['check_type']}' evaluated on {table_id}."
    )
    incident_source = "backend seed (no live persisted incident on file)" if seeded else "deterministic detection"
    incident_description = (
        f"Incident {row['incident_id']} seeded by the backend ({row['severity']} severity, {row['status']}) "
        "to keep the triage product story demoable while no live incident is persisted."
        if seeded
        else f"Incident {row['incident_id']} generated ({row['severity']} severity, {row['status']})."
    )
    return [
        AuditTrailEntry(
            step="metadata_loaded",
            description=f"Table metadata loaded for {table_id}.",
            timestamp=table_metadata_loaded_at,
            source="shared.data_service.get_table_metadata",
        ),
        AuditTrailEntry(
            step="check_evaluated",
            description=check_description,
            timestamp=created_at,
            source=check_source,
        ),
        AuditTrailEntry(
            step="incident_generated",
            description=incident_description,
            timestamp=created_at,
            source=incident_source,
        ),
    ]


def _active_incident_rows(client: Any, config: PlatformConfig) -> list[dict]:
    return run_query(
        client,
        f"""
        SELECT incident_id, table_id, check_type, severity, status, created_at,
               observed_value, expected_value, affected_metrics, owner
        FROM `{config.incidents_table}`
        WHERE status IN UNNEST(@active_statuses)
        ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                 created_at ASC
        """,
        {"active_statuses": sorted(ACTIVE_INCIDENT_STATUSES)},
    )


def build_warehouse_health(client: Any, config: PlatformConfig) -> TriageWarehouseResponse:
    tables, metrics_by_table, lineage_by_table = _governed_tables_and_metric_map(client)
    rows = _active_incident_rows(client, config)
    # A real persisted incident always wins; the seed only ever fills in
    # when the live query genuinely returned nothing (see
    # apps/data_quality_triage/seed_incidents.py for why that currently
    # happens and why this is not a silent failure fallback).
    rows = seed_row_if_needed(rows)
    incidents_by_table: dict[str, list[dict]] = {table: [] for table in tables}
    for row in rows:
        incidents_by_table.setdefault(row["table_id"], []).append(row)

    table_health: list[TriageTableHealth] = []
    freshness_values: list[float] = []
    metadata_loaded_at_by_table: dict[str, str | None] = {}
    for table in tables:
        try:
            health = derive_source_health(client, QUALIFIED_DATASET, table)
            status = health.status
        except Exception:
            status = "unknown"
        try:
            metadata = get_table_metadata(client, QUALIFIED_DATASET, table)
            metadata_loaded_at_by_table[table] = metadata.modified_at
            if metadata.modified_at:
                modified = datetime.fromisoformat(metadata.modified_at)
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                freshness = max((datetime.now(timezone.utc) - modified).total_seconds() / 60, 0)
                freshness_values.append(freshness)
            else:
                freshness = None
        except Exception:
            metadata_loaded_at_by_table[table] = None
            freshness = None
        table_health.append(
            TriageTableHealth(
                table_id=table,
                status=status,
                freshness_minutes=round(freshness, 1) if freshness is not None else None,
                active_incident_count=len(incidents_by_table.get(table, [])),
            )
        )

    incidents = [
        TriageIncident(
            incident_id=row["incident_id"],
            table_id=row["table_id"],
            check_type=row["check_type"],
            severity=row["severity"],
            status=row["status"],
            created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            observed_value=float(row["observed_value"]) if row.get("observed_value") is not None else None,
            expected_value=float(row["expected_value"]) if row.get("expected_value") is not None else None,
            affected_metrics=list(row.get("affected_metrics") or []),
            owner=row.get("owner"),
            next_allowed_statuses=next_allowed_statuses(row["status"]),
            governed_metric_names=sorted(metrics_by_table.get(row["table_id"], [])),
            downstream_assets=_downstream_assets_for_table(row["table_id"], lineage_by_table),
            audit_trail=_incident_audit_trail(
                row, table_metadata_loaded_at=metadata_loaded_at_by_table.get(row["table_id"])
            ),
        )
        for row in rows
    ]
    counts = {status: sum(1 for item in table_health if item.status == status) for status in ("healthy", "degraded", "critical")}
    lineage = [
        TriageLineage(table_id=table, governed_metrics=lineage_by_table.get(table, []))
        for table in tables
    ]
    return TriageWarehouseResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset=QUALIFIED_DATASET,
        monitored_tables=len(tables),
        healthy_tables=counts["healthy"],
        degraded_tables=counts["degraded"],
        critical_tables=counts["critical"],
        open_incidents=len(incidents),
        freshness_minutes=max(freshness_values) if freshness_values else None,
        tables=table_health,
        incidents=incidents,
        lineage=lineage,
    )


_TRANSITIONS = {
    "acknowledged": acknowledge_incident,
    "investigating": begin_investigation,
    "mitigated": mark_mitigated,
    "resolved": resolve_incident,
    "open": reopen_incident,
}


def transition_incident(
    client: Any,
    config: PlatformConfig,
    *,
    incident_id: str,
    target_status: str,
    expected_current_status: str,
    resolution_notes: str | None,
    actor: str,
) -> IncidentTransitionResponse:
    operation = _TRANSITIONS[target_status]
    kwargs = dict(
        expected_current_status=expected_current_status,
        mode="persisted",
        actor=actor,
        config=config,
    )
    if target_status == "resolved":
        if not resolution_notes or not resolution_notes.strip():
            raise ValueError("Resolution notes are required to resolve an incident.")
        kwargs["resolution_notes"] = resolution_notes.strip()
    outcome = operation(client, incident_id, **kwargs)
    return IncidentTransitionResponse(
        incident_id=outcome.incident_id,
        status=outcome.status,
        persisted=outcome.persisted,
        row_version=outcome.row_version,
    )
