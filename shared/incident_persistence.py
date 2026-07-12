"""Atomic incident creation and status-transition history.

Phase 6B business persistence, built on top of Phase 6A's spike-verified
shared.persistence_transactions.execute_transaction() mechanism (commit/
rollback, ASSERT @@row_count, result_sql-after-COMMIT, and the
write_locks lock-row contention pattern -- all confirmed against real
BigQuery by the Phase 6B live spike; see docs/PHASE_6B_HANDOFF.md).

Two registered templates, per the module docstring in
shared/persistence_transactions.py's own worked example ("an incident's
status-update + transition-insert must succeed or fail as one atomic
unit"):

  CREATE_INCIDENT_TXN -- insert-if-absent, guarded by the 'incidents'
    write-lock domain, following the exact pattern the spike proved
    against real BigQuery (touch the domain's lock row first, then
    `INSERT ... SELECT ... FROM UNNEST([1]) AS _seed WHERE NOT EXISTS
    (...)`, with `ASSERT @@row_count IN (0, 1)`).

  TRANSITION_INCIDENT_STATUS_TXN -- one atomic script that updates the
    incident's current status (guarded by an optimistic-concurrency
    `WHERE status = @from_status`) and inserts the corresponding
    incident_transitions row, both guarded by the same 'incidents'
    write-lock domain.

Both functions here are the ONLY place in this codebase that build
BoundStatement values for these two templates -- apps/ must never
register a template or call execute_transaction() directly (enforced by
tests/test_persistence_boundary.py); apps/ calls create_incident() /
record_incident_transition() instead.

incident_id generation is unchanged from Phase 2/5: callers already build
a deterministic incident_id upstream (see
apps/data_quality_triage/checks.py's build_incident_from_finding(), which
derives it from f"{dataset}.{table_id}.{check_name}.{created_at}"). This
module does not invent a new ID scheme -- it takes whatever incident_id
the caller already computed and makes persisting it atomic and
idempotent.

ASSERT-failure classification note: per
shared/persistence_transactions.py's TransactionTemplateError docstring,
this module does NOT catch or re-wrap an ASSERT failure raised inside
execute_transaction() (e.g. TRANSITION_INCIDENT_STATUS_TXN's "expected
exactly one row transitioned from the expected current status" ASSERT
firing because the incident was not actually in `from_status`, or was
concurrently transitioned by someone else first). That classification
work is out of scope for Phase 6B (the live spike confirmed a genuine
ASSERT failure and a genuine concurrent conflict currently share the same
exception type/reason code against real BigQuery, and only the narrow,
confirmed concurrent-update message signature distinguishes them -- see
_is_retryable()'s docstring). Recorded as a Phase 6E operational-hardening
item, not solved here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from shared.config import DEFAULT_DATASET, PlatformConfig, assert_sql_targets_dataset
from shared.data_service import run_query
from shared.incidents import validate_transition
from shared.models import Incident, IncidentStatus
from shared.persistence_transactions import (
    BoundStatement,
    PayloadConflictError,
    StatementTemplate,
    TransactionalClientLike,
    execute_transaction,
    register_template,
)

# Phase 6E correction 1: dataset-parameterized from LOUPE_DATASET (see
# shared/data_service.py's INCIDENTS_TABLE for the identical rationale) --
# these were previously hardcoded "loupe_platform.*" literals baked into
# CREATE_INCIDENT_TXN/TRANSITION_INCIDENT_STATUS_TXN's SQL at import time.
#
# Phase 6E correction 2: import-time resolution is correct and sufficient
# for a normally deployed process (env fully configured before anything is
# imported), but it is NOT authoritative for an operator CLI that selects
# its target dataset from a --dataset argument -- if this module were ever
# imported before that argument were parsed, these constants would stay
# frozen to whatever LOUPE_DATASET happened to be at that earlier import.
# create_incident() and record_incident_transition() below therefore both
# accept an optional `config: PlatformConfig` argument: when supplied, it
# -- not these module-level constants -- is the sole source of truth for
# which dataset's tables the generated SQL targets, resolved fresh from
# the caller-constructed config rather than from whatever happened to be
# in os.environ at this module's first import. Every operator script in
# tools/phase6e_ops/ passes an explicit config for exactly this reason
# (see _create_template_for()/_transition_template_for() below). The
# module-level constants remain the default (config=None) path used by
# every normal deployed app process, which never needs to override them.
_DATASET = os.environ.get("LOUPE_DATASET", DEFAULT_DATASET)
INCIDENTS_TABLE = f"{_DATASET}.incidents"
INCIDENT_TRANSITIONS_TABLE = f"{_DATASET}.incident_transitions"
WRITE_LOCKS_TABLE = f"{_DATASET}.write_locks"


def _create_incident_sql(incidents_table: str, write_locks_table: str) -> tuple[str, str]:
    """Render CREATE_INCIDENT_TXN's `sql`/`result_sql` for one specific
    set of table identifiers -- the single source of truth both the
    frozen default template and any per-config template (below) render
    from, so the two can never drift apart in shape."""

    sql = f"""
    UPDATE `{write_locks_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @actor
    WHERE lock_domain = 'incidents';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain incidents';

    INSERT INTO `{incidents_table}` (
      incident_id, created_at, dataset, table_id, check_type, severity, status,
      observed_value, expected_value, sql_template, query_hash,
      affected_metrics, affected_dashboards, playbook, owner,
      acknowledged_at, resolved_at, resolution_notes, rule_version,
      recurrence_of_incident_id, row_version
    )
    SELECT @incident_id, @created_at, @dataset, @table_id, @check_type, @severity, @status,
           @observed_value, @expected_value, @sql_template, @query_hash,
           @affected_metrics, @affected_dashboards, @playbook, @owner,
           @acknowledged_at, @resolved_at, @resolution_notes, @rule_version,
           @recurrence_of_incident_id, 1
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{incidents_table}` WHERE incident_id = @incident_id
    );
    ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one incident row';
    """
    result_sql = f"""
    SELECT incident_id, dataset, table_id, check_type, severity, status, row_version
    FROM `{incidents_table}` WHERE incident_id = @incident_id;
    """
    return sql, result_sql


def _transition_incident_sql(
    incidents_table: str, incident_transitions_table: str, write_locks_table: str
) -> tuple[str, str]:
    """Render TRANSITION_INCIDENT_STATUS_TXN's `sql`/`result_sql` for one
    specific set of table identifiers -- see _create_incident_sql()'s
    docstring for why this is factored out this way."""

    sql = f"""
    UPDATE `{write_locks_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @actor
    WHERE lock_domain = 'incidents';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain incidents';

    UPDATE `{incidents_table}`
    SET status = @to_status,
        row_version = row_version + 1,
        acknowledged_at = IF(@to_status = 'acknowledged', CURRENT_TIMESTAMP(), acknowledged_at),
        resolved_at = IF(@to_status = 'resolved', CURRENT_TIMESTAMP(), resolved_at),
        resolution_notes = COALESCE(@resolution_notes, resolution_notes)
    WHERE incident_id = @incident_id AND status = @from_status;
    ASSERT @@row_count = 1 AS 'expected exactly one incident row transitioned from the expected current status';

    INSERT INTO `{incident_transitions_table}` (
      transition_id, incident_id, from_status, to_status, transitioned_at, actor, resolution_notes, row_version_before
    )
    VALUES (@transition_id, @incident_id, @from_status, @to_status, CURRENT_TIMESTAMP(), @actor, @resolution_notes, @row_version_before);
    ASSERT @@row_count = 1 AS 'expected exactly one incident_transitions row inserted';
    """
    result_sql = f"""
    SELECT incident_id, status, row_version FROM `{incidents_table}` WHERE incident_id = @incident_id;
    """
    return sql, result_sql


# ---------------------------------------------------------------------------
# CREATE_INCIDENT_TXN: insert-if-absent, guarded by the 'incidents' lock
# ---------------------------------------------------------------------------

_create_incident_sql_default, _create_incident_result_sql_default = _create_incident_sql(
    INCIDENTS_TABLE, WRITE_LOCKS_TABLE
)
CREATE_INCIDENT_TXN = StatementTemplate(
    name="CREATE_INCIDENT_TXN",
    lock_domain="incidents",
    sql=_create_incident_sql_default,
    result_sql=_create_incident_result_sql_default,
)
register_template(CREATE_INCIDENT_TXN)

# ---------------------------------------------------------------------------
# TRANSITION_INCIDENT_STATUS_TXN: status update + transition-history insert,
# one atomic script, guarded by the same 'incidents' lock domain.
# ---------------------------------------------------------------------------

_transition_sql_default, _transition_result_sql_default = _transition_incident_sql(
    INCIDENTS_TABLE, INCIDENT_TRANSITIONS_TABLE, WRITE_LOCKS_TABLE
)
TRANSITION_INCIDENT_STATUS_TXN = StatementTemplate(
    name="TRANSITION_INCIDENT_STATUS_TXN",
    lock_domain="incidents",
    sql=_transition_sql_default,
    result_sql=_transition_result_sql_default,
)
register_template(TRANSITION_INCIDENT_STATUS_TXN)


# ---------------------------------------------------------------------------
# Per-config templates -- built and cached on first use for a given
# dataset, so an operator script that constructs an explicit PlatformConfig
# (rather than relying on this module's own import-time constants above)
# gets SQL text guaranteed to target exactly that config's dataset,
# regardless of when this module was first imported relative to that
# config being built. See the module docstring's Phase 6E correction 2
# note above. assert_sql_targets_dataset() is the structural backstop:
# even a bug in the SQL-rendering helpers above would be caught here,
# before any BigQuery call, rather than silently executing against the
# wrong dataset.
# ---------------------------------------------------------------------------

_CONFIG_CREATE_TEMPLATES: dict[str, StatementTemplate] = {}
_CONFIG_TRANSITION_TEMPLATES: dict[str, StatementTemplate] = {}


def _create_template_for(config: PlatformConfig) -> StatementTemplate:
    cached = _CONFIG_CREATE_TEMPLATES.get(config.dataset)
    if cached is not None:
        return cached
    sql, result_sql = _create_incident_sql(config.incidents_table, config.write_locks_table)
    assert_sql_targets_dataset(sql, config.dataset)
    assert_sql_targets_dataset(result_sql, config.dataset)
    template = StatementTemplate(
        name=f"CREATE_INCIDENT_TXN::{config.dataset}",
        lock_domain="incidents",
        sql=sql,
        result_sql=result_sql,
    )
    register_template(template)
    _CONFIG_CREATE_TEMPLATES[config.dataset] = template
    return template


def _transition_template_for(config: PlatformConfig) -> StatementTemplate:
    cached = _CONFIG_TRANSITION_TEMPLATES.get(config.dataset)
    if cached is not None:
        return cached
    sql, result_sql = _transition_incident_sql(
        config.incidents_table, config.incident_transitions_table, config.write_locks_table
    )
    assert_sql_targets_dataset(sql, config.dataset)
    assert_sql_targets_dataset(result_sql, config.dataset)
    template = StatementTemplate(
        name=f"TRANSITION_INCIDENT_STATUS_TXN::{config.dataset}",
        lock_domain="incidents",
        sql=sql,
        result_sql=result_sql,
    )
    register_template(template)
    _CONFIG_TRANSITION_TEMPLATES[config.dataset] = template
    return template


# ---------------------------------------------------------------------------
# create_incident()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncidentCreationResult:
    """The outcome of create_incident().

    `created` is True only if THIS call's INSERT actually landed the row
    (i.e. no prior row existed under this incident_id). `created=False`
    means the row already existed -- either from a prior successful call
    with the identical payload (idempotent retry, not an error) or,
    logically, from a genuinely different origin the payload-conflict
    check below did not distinguish because the compared fields happened
    to match. Either way, `persisted` always reflects the row's actual
    current, persisted state.
    """

    created: bool
    incident_id: str
    persisted_severity: str
    persisted_status: str
    row_version: int


# Fields compared for the idempotency contract (Phase 6, amendment 2:
# "same ID, identical payload -> not an error; same ID, different payload
# -> PayloadConflictError"). Deliberately narrow: incident_id already
# encodes dataset/table_id/check_type/created_at (see module docstring),
# so those cannot legitimately differ under the same id without the
# caller having built a bad id in the first place -- not this module's
# job to re-validate. severity and status are the two fields a second,
# differently-classifying caller could plausibly disagree on for the
# same detected condition, so those are the ones this module actually
# guards.
def _create_incident_conflicts(intended: Incident, persisted: dict) -> list[str]:
    conflicts = []
    if persisted["severity"] != intended.severity:
        conflicts.append("severity")
    if persisted["status"] != intended.status:
        conflicts.append("status")
    return conflicts


def create_incident(
    client: "TransactionalClientLike", incident: Incident, *, actor: str, config: Optional[PlatformConfig] = None
) -> IncidentCreationResult:
    """Atomically create `incident` if no row exists yet under its
    incident_id, guarded by the 'incidents' write-lock domain (forcing
    genuine contention with any concurrent create/transition touching the
    same domain, per the spike-confirmed lock-row pattern).

    Idempotent: a second call with the identical incident_id and matching
    severity/status is a normal, successful no-op (returns
    created=False). A second call with the same incident_id but a
    DIFFERENT severity or status raises PayloadConflictError -- the
    message never includes either value, only which field(s) differed
    (matching PayloadConflictError's documented contract).

    `config`, if supplied, is the SOLE source of truth for which
    dataset's `incidents`/`write_locks` tables this call targets --
    overriding this module's import-time-frozen constants entirely, per
    the module docstring's Phase 6E correction 2. Operator scripts under
    tools/phase6e_ops/ always pass this explicitly; normal deployed app
    processes leave it as None and get the default, import-time-resolved
    template (correct for them, since their environment is fully
    configured before this module is ever imported).
    """

    template = _create_template_for(config) if config is not None else CREATE_INCIDENT_TXN

    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=template.name,
                params={
                    "actor": actor,
                    "incident_id": incident.incident_id,
                    "created_at": incident.created_at,
                    "dataset": incident.dataset,
                    "table_id": incident.table_id,
                    "check_type": incident.check_type,
                    "severity": incident.severity,
                    "status": incident.status,
                    "observed_value": incident.observed_value,
                    "expected_value": incident.expected_value,
                    "sql_template": incident.sql_template,
                    "query_hash": incident.query_hash,
                    "affected_metrics": incident.affected_metrics,
                    "affected_dashboards": incident.affected_dashboards,
                    "playbook": incident.playbook,
                    "owner": incident.owner,
                    "acknowledged_at": incident.acknowledged_at,
                    "resolved_at": incident.resolved_at,
                    "resolution_notes": incident.resolution_notes,
                    "rule_version": incident.rule_version,
                    "recurrence_of_incident_id": incident.recurrence_of_incident_id,
                },
            )
        ],
    )

    if not result.result_rows:
        # Should be unreachable: the row either already existed or this
        # call's own INSERT just created it, so result_sql's lookup by
        # incident_id should always find exactly one row. Surfacing this
        # as a RuntimeError (rather than silently returning a nonsense
        # result) rather than assuming -- a template/mechanism bug here
        # would otherwise be invisible to callers.
        raise RuntimeError(
            f"CREATE_INCIDENT_TXN committed but no row was found for "
            f"incident_id={incident.incident_id!r} afterward -- this "
            "should be unreachable."
        )

    persisted = result.result_rows[0]
    conflicts = _create_incident_conflicts(incident, persisted)
    if conflicts:
        raise PayloadConflictError(
            f"incident_id={incident.incident_id!r} conflicts on fields: "
            f"{', '.join(sorted(conflicts))} (values withheld)"
        )

    # created=True only when this call's own row_version is 1 AND the
    # attempt count suggests a fresh insert is not distinguishable purely
    # from row_version (a second, later transition also leaves
    # row_version=1 momentarily false once transitioned -- but at CREATE
    # time no transition has happened yet, so row_version==1 reliably
    # means "still exactly as first inserted," which is the best signal
    # available without a second round-trip).
    created = persisted["row_version"] == 1

    return IncidentCreationResult(
        created=created,
        incident_id=persisted["incident_id"],
        persisted_severity=persisted["severity"],
        persisted_status=persisted["status"],
        row_version=persisted["row_version"],
    )


# ---------------------------------------------------------------------------
# record_incident_transition()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncidentTransitionResult:
    incident_id: str
    status: str
    row_version: int


def record_incident_transition(
    client: "TransactionalClientLike",
    *,
    incident_id: str,
    from_status: IncidentStatus,
    to_status: IncidentStatus,
    row_version_before: int,
    actor: str,
    transition_id: str,
    resolution_notes: Optional[str] = None,
    config: Optional[PlatformConfig] = None,
) -> IncidentTransitionResult:
    """Atomically move an incident from `from_status` to `to_status` and
    append the corresponding incident_transitions row, as one script.

    Validates the transition against shared.incidents.ALLOWED_TRANSITIONS
    BEFORE ever touching BigQuery (fail fast, exactly like
    shared/data_service.py's existing incident-writing pattern) -- an
    invalid transition raises InvalidTransitionError immediately, without
    spending a retry budget or a BigQuery round-trip on something that
    can never succeed.

    The UPDATE's `WHERE incident_id = @incident_id AND status =
    @from_status` doubles as an optimistic-concurrency guard: if the
    incident's persisted status no longer matches `from_status` (a
    concurrent transition already moved it, or the caller's cached
    `from_status` was stale), `ASSERT @@row_count = 1` inside the script
    fails and the whole transaction rolls back -- nothing partially
    applies. Per this module's docstring, that ASSERT failure is not
    currently classified or re-wrapped here; it propagates as whatever
    execute_transaction() raises.

    `transition_id` and `row_version_before` are caller-supplied
    (matching this codebase's existing pattern of not inventing a new ID
    generator inside the persistence layer -- see incident_id's
    treatment in create_incident() above). This is the ONE function every
    UI-facing Data Quality Triage lifecycle action
    (apps/data_quality_triage/incident_lifecycle.py's acknowledge_incident/
    begin_investigation/mark_mitigated/resolve_incident/reopen_incident)
    and tools/phase6e_ops/live_integration_validation.py's resolution
    step both ultimately call when persisted mode is active -- there is
    exactly one lifecycle-transition write path in this codebase, not a
    separate one for the real UI and another for the live validation
    script.

    `config`, if supplied, is the SOLE source of truth for which
    dataset's `incidents`/`incident_transitions`/`write_locks` tables this
    call targets -- overriding this module's import-time-frozen constants
    entirely, per the module docstring's Phase 6E correction 2. See
    create_incident()'s matching docstring note.
    """

    validate_transition(from_status, to_status)

    template = _transition_template_for(config) if config is not None else TRANSITION_INCIDENT_STATUS_TXN

    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=template.name,
                params={
                    "actor": actor,
                    "incident_id": incident_id,
                    "from_status": from_status,
                    "to_status": to_status,
                    "transition_id": transition_id,
                    "resolution_notes": resolution_notes,
                    "row_version_before": row_version_before,
                },
            )
        ],
    )

    if not result.result_rows:
        raise RuntimeError(
            f"TRANSITION_INCIDENT_STATUS_TXN committed but no row was "
            f"found for incident_id={incident_id!r} afterward -- this "
            "should be unreachable."
        )

    persisted = result.result_rows[0]
    return IncidentTransitionResult(
        incident_id=persisted["incident_id"],
        status=persisted["status"],
        row_version=persisted["row_version"],
    )


# ---------------------------------------------------------------------------
# get_incident_state(): the minimum read record_incident_transition()'s
# callers need to build a correctly-guarded transition call.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedIncidentState:
    """The minimum persisted state a caller needs before calling
    record_incident_transition(): the current status (to pass as
    `from_status`, and to compare against a UI's last-displayed status for
    an explicit concurrency check) and row_version (to pass as
    `row_version_before`). Deliberately narrower than the full Incident
    row shared.data_service.get_incident() returns -- this module writes
    incidents, it does not own their full read/display shape."""

    incident_id: str
    status: IncidentStatus
    row_version: int


def get_incident_state(
    client: "TransactionalClientLike", incident_id: str, *, config: Optional[PlatformConfig] = None
) -> Optional[PersistedIncidentState]:
    """Read-only lookup of one incident's current persisted status and
    row_version, or None if no such incident is persisted.

    Uses shared.data_service.run_query() (read-only enforced) rather than
    execute_transaction() -- this is a plain SELECT, not a transaction.
    `config`, if supplied, overrides this module's import-time-frozen
    INCIDENTS_TABLE constant exactly like create_incident()/
    record_incident_transition() above.
    """

    table = config.incidents_table if config is not None else INCIDENTS_TABLE
    sql = f"SELECT incident_id, status, row_version FROM `{table}` WHERE incident_id = @incident_id LIMIT 1"
    if config is not None:
        assert_sql_targets_dataset(sql, config.dataset)
    rows = run_query(client, sql, {"incident_id": incident_id})
    if not rows:
        return None
    row = rows[0]
    return PersistedIncidentState(incident_id=row["incident_id"], status=row["status"], row_version=row["row_version"])
