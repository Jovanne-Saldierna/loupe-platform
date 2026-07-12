"""Phase 6D persistence wiring for Data Quality Triage.

A thin app-level wrapper over shared/incident_persistence.py,
shared/audit_persistence.py, and shared/schema_baseline_persistence.py --
exactly the same "apps/ never registers a template or calls
execute_transaction() directly" discipline incident_lifecycle.py already
follows for status transitions (see that module's docstring).

Three responsibilities:

  persist_confirmed_incidents() -- atomically create each freshly-detected
    Incident via shared.incident_persistence.create_incident(), then
    record a matching audit event via
    shared.audit_persistence.write_event_idempotent() (governed action,
    transactional/idempotent audit path -- never
    shared.audit.write_event()'s streaming path). Per-incident failures
    are collected, not raised: one bad row must not prevent every other
    confirmed incident in the same run from persisting.

  read_schema_baseline() -- a thin read wrapper over
    shared.schema_baseline_persistence.get_schema_baseline(), adapted into
    this app's own SchemaSnapshot shape (models.py) so checks.py's
    check_schema_drift() never has to know about the persistence layer's
    row shape.

  promote_schema_baseline_now() -- an explicit, human-triggered action
    (never run automatically as part of build_state()'s live pass) that
    promotes a table's just-profiled schema as the new baseline, via
    shared.schema_baseline_persistence.promote_schema_baseline().

Never substitutes fabricated/sample incidents for a persistence failure:
every function below either returns an honest per-item outcome or raises
a real, meaningful exception -- callers (main.py) are responsible for
degrading to a "not persisted" label, never a fake success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from apps.data_quality_triage.models import SchemaSnapshot
from shared.audit_persistence import write_event_idempotent
from shared.config import PlatformConfig
from shared.incident_persistence import IncidentCreationResult, create_incident
from shared.models import AuditEvent, Incident
from shared.persistence_transactions import PayloadConflictError, TransactionalClientLike
from shared.schema_baseline_persistence import (
    SchemaBaselinePromotionResult,
    get_schema_baseline,
    promote_schema_baseline,
)

# ---------------------------------------------------------------------------
# Incident + audit persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncidentPersistOutcome:
    """One incident's persistence attempt outcome -- always present for
    every incident passed in, whether it succeeded, conflicted, or failed
    for some other reason. Never silently dropped."""

    incident_id: str
    persisted: bool
    created: bool
    error: Optional[str] = None


def persist_confirmed_incidents(
    client: "TransactionalClientLike",
    incidents: list[Incident],
    *,
    actor: str,
    build_audit_event: "callable",
    config: Optional[PlatformConfig] = None,
) -> list[IncidentPersistOutcome]:
    """Persist every confirmed incident, atomically, one at a time, and
    record a matching audit event for each successfully persisted (or
    idempotently-already-persisted) incident.

    `build_audit_event(incident) -> shared.models.AuditEvent` is supplied
    by the caller (main.py, via checks.build_audit_event_for_incident) so
    this module does not need to know how to construct event_id/timestamp
    itself -- keeping this module a thin persistence wrapper, not a second
    place that invents audit-event shapes.

    `config`, if supplied, is passed straight through to
    shared.incident_persistence.create_incident() as the sole source of
    truth for which dataset's tables this call targets (Phase 6E
    correction 2) -- tools/phase6e_ops/live_integration_validation.py
    passes this explicitly; main.py's normal deployed-app call leaves it
    as None and gets the default, import-time-resolved target.

    A PayloadConflictError or any other exception for one incident is
    recorded in that incident's own IncidentPersistOutcome.error and does
    NOT stop the remaining incidents in `incidents` from being attempted --
    per this module's docstring, one bad row must not silently swallow an
    entire run's other real findings.
    """

    outcomes: list[IncidentPersistOutcome] = []
    for incident in incidents:
        try:
            result: IncidentCreationResult = create_incident(client, incident, actor=actor, config=config)
        except Exception as exc:  # noqa: BLE001 -- one incident's failure must not abort the batch
            outcomes.append(
                IncidentPersistOutcome(incident_id=incident.incident_id, persisted=False, created=False, error=repr(exc))
            )
            continue

        try:
            event: AuditEvent = build_audit_event(incident)
            write_event_idempotent(client, event, actor=actor, config=config)
        except Exception as exc:  # noqa: BLE001 -- the incident itself already committed; audit failure is reported separately
            outcomes.append(
                IncidentPersistOutcome(
                    incident_id=incident.incident_id,
                    persisted=True,
                    created=result.created,
                    error=f"incident persisted but audit event failed: {exc!r}",
                )
            )
            continue

        outcomes.append(
            IncidentPersistOutcome(incident_id=incident.incident_id, persisted=True, created=result.created, error=None)
        )
    return outcomes


# ---------------------------------------------------------------------------
# Schema baseline read + promotion
# ---------------------------------------------------------------------------


def read_schema_baseline(
    client,
    *,
    dataset: str,
    table_id: str,
    config: Optional[PlatformConfig] = None,
) -> Optional[SchemaSnapshot]:
    """Read the persisted baseline for one table, adapted into this app's
    own SchemaSnapshot shape. Returns None if no baseline has ever been
    promoted for this table (a normal outcome -- checks.check_schema_drift()
    already handles that as an honest not_evaluated finding) or if the
    read itself fails (persistence unavailable is also, deliberately,
    just "no baseline available" from this check's point of view -- it
    never fabricates drift or assumes the schema matches)."""

    try:
        baseline = get_schema_baseline(client, dataset=dataset, table_id=table_id, config=config)
    except Exception:  # noqa: BLE001 -- storage-unavailable degrades to "no baseline available," never a crash
        return None
    if baseline is None:
        return None
    return SchemaSnapshot(table_id=baseline.table_id, captured_at=baseline.promoted_at, columns=baseline.columns)


def promote_schema_baseline_now(
    client: "TransactionalClientLike",
    *,
    dataset: str,
    table_id: str,
    columns: dict[str, str],
    source_snapshot_id: str,
    promoted_by: str,
    event_id: str,
    event_timestamp: str,
    config: Optional[PlatformConfig] = None,
) -> SchemaBaselinePromotionResult:
    """Promote the current schema as the new baseline for one table.

    This is an EXPLICIT human action -- callers must never invoke this
    from build_state()'s automatic live pass (which only detects and
    persists incidents); it belongs behind a deliberate "promote this
    schema as the baseline" UI action, exactly like Metric Governance's
    certification action is an explicit governance action, never an
    automatic side effect of a routine data-quality run.
    """

    return promote_schema_baseline(
        client,
        dataset=dataset,
        table_id=table_id,
        columns=columns,
        source_snapshot_id=source_snapshot_id,
        promoted_by=promoted_by,
        event_id=event_id,
        event_timestamp=event_timestamp,
        config=config,
    )
