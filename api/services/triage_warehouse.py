from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.models import (
    IncidentTransitionResponse,
    TriageIncident,
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
from apps.metric_governance.persistence import read_catalog
from shared.config import PlatformConfig
from shared.data_service import derive_source_health, get_table_metadata, run_query
from shared.incidents import ACTIVE_INCIDENT_STATUSES


class TriageUnavailableError(RuntimeError):
    pass


def _governed_tables(client: Any) -> list[str]:
    catalog = read_catalog(client)
    if catalog.catalog_unavailable:
        raise TriageUnavailableError("The persisted catalog is unavailable.")
    return sorted({table for definition in catalog.definitions for table in definition.approved_source_tables})


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
    tables = _governed_tables(client)
    rows = _active_incident_rows(client, config)
    incidents_by_table: dict[str, list[dict]] = {table: [] for table in tables}
    for row in rows:
        incidents_by_table.setdefault(row["table_id"], []).append(row)

    table_health: list[TriageTableHealth] = []
    freshness_values: list[float] = []
    for table in tables:
        try:
            health = derive_source_health(client, QUALIFIED_DATASET, table)
            status = health.status
        except Exception:
            status = "unknown"
        try:
            metadata = get_table_metadata(client, QUALIFIED_DATASET, table)
            if metadata.modified_at:
                modified = datetime.fromisoformat(metadata.modified_at)
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=timezone.utc)
                freshness = max((datetime.now(timezone.utc) - modified).total_seconds() / 60, 0)
                freshness_values.append(freshness)
            else:
                freshness = None
        except Exception:
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
        )
        for row in rows
    ]
    counts = {status: sum(1 for item in table_health if item.status == status) for status in ("healthy", "degraded", "critical")}
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
