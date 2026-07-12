"""Idempotent, transactional audit-event persistence.

shared/audit.py's existing write_event() uses BigQuery's streaming
insert API (insert_rows_json) -- the standard, low-latency path for
appending single rows, but neither atomic-with-another-write nor
idempotent: calling it twice with the same event_id after an ambiguous
failure (the caller never learned whether the first attempt landed)
produces two rows, not one.

This module adds the complementary, narrower-use-case path: an audit
event written THROUGH shared.persistence_transactions.execute_transaction()
-- guarded by the 'audit_events' write-lock domain and an insert-if-absent
by event_id, following the exact pattern the Phase 6B live spike proved
against real BigQuery for incidents (see shared/incident_persistence.py's
CREATE_INCIDENT_TXN, the same shape applied here to audit_events). Use
this when a caller needs "this exact event lands exactly once, and I can
safely retry the call if I'm not sure it landed" -- e.g. an audit event a
retried business operation emits as a side effect. Continue using
shared.audit.write_event() for ordinary, single-shot audit writes that
don't need that guarantee; both write to the same audit_events table and
shared.audit.list_events_for_subject() reads rows from either path
identically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
import json
from typing import Optional

from shared.audit import validate_no_secrets
from shared.config import DEFAULT_DATASET, PlatformConfig, assert_sql_targets_dataset
from shared.models import AuditEvent
from shared.persistence_transactions import (
    BoundStatement,
    PayloadConflictError,
    StatementTemplate,
    TransactionalClientLike,
    execute_transaction,
    register_template,
)

# Phase 6E correction: dataset-parameterized from LOUPE_DATASET (see
# shared/data_service.py's INCIDENTS_TABLE for the identical rationale) --
# these were previously hardcoded "loupe_platform.*" literals baked into
# WRITE_AUDIT_EVENT_TXN's SQL at import time, which meant this module
# could never actually target an isolated test dataset regardless of
# configuration.
_DATASET = os.environ.get("LOUPE_DATASET", DEFAULT_DATASET)
AUDIT_EVENTS_TABLE = f"{_DATASET}.audit_events"
WRITE_LOCKS_TABLE = f"{_DATASET}.write_locks"

# ---------------------------------------------------------------------------
# WRITE_AUDIT_EVENT_TXN: insert-if-absent, guarded by the 'audit_events' lock
# ---------------------------------------------------------------------------
#
# `context` is declared JSON in schema_management.py's DDL; there is no
# named-parameter binding for a JSON-typed value in the BigQuery Python
# client, so it is bound as a plain STRING parameter (a canonical,
# sort_keys=True json.dumps of the already secret-scanned context dict)
# and reconstructed with PARSE_JSON(@context_json) inside the script --
# the standard, documented BigQuery pattern for passing structured data
# through a query parameter. This specific SQL shape (PARSE_JSON on a
# bound STRING) has not itself been exercised by the Phase 6B live spike
# -- only execute_transaction()'s core mechanism (script rendering,
# commit/rollback, ASSERT @@row_count, result_sql-after-COMMIT, the
# lock-row pattern) was live-verified. Recorded as a follow-up live check
# alongside the rest of Phase 6B's new business templates.

def _write_audit_sql(audit_events_table: str, write_locks_table: str) -> tuple[str, str]:
    sql = f"""
    UPDATE `{write_locks_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @actor
    WHERE lock_domain = 'audit_events';
    ASSERT @@row_count = 1 AS 'expected exactly one write_locks row for domain audit_events';

    INSERT INTO `{audit_events_table}` (event_id, timestamp, actor, event_type, subject, outcome, context)
    SELECT @event_id, @timestamp, @actor, @event_type, @subject, @outcome, PARSE_JSON(@context_json)
    FROM UNNEST([1]) AS _seed
    WHERE NOT EXISTS (
      SELECT 1 FROM `{audit_events_table}` WHERE event_id = @event_id
    );
    ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one audit_events row';
    """
    result_sql = f"""
    SELECT event_id, event_type, subject, outcome FROM `{audit_events_table}` WHERE event_id = @event_id;
    """
    return sql, result_sql


_write_audit_sql_default, _write_audit_result_sql_default = _write_audit_sql(
    AUDIT_EVENTS_TABLE, WRITE_LOCKS_TABLE
)
WRITE_AUDIT_EVENT_TXN = StatementTemplate(
    name="WRITE_AUDIT_EVENT_TXN",
    lock_domain="audit_events",
    sql=_write_audit_sql_default,
    result_sql=_write_audit_result_sql_default,
)
register_template(WRITE_AUDIT_EVENT_TXN)

_CONFIG_WRITE_TEMPLATES: dict[str, StatementTemplate] = {}


def _write_template_for(config: PlatformConfig) -> StatementTemplate:
    cached = _CONFIG_WRITE_TEMPLATES.get(config.dataset)
    if cached is not None:
        return cached
    sql, result_sql = _write_audit_sql(config.audit_events_table, config.write_locks_table)
    assert_sql_targets_dataset(sql, config.dataset)
    assert_sql_targets_dataset(result_sql, config.dataset)
    template = StatementTemplate(
        name=f"WRITE_AUDIT_EVENT_TXN::{config.dataset}",
        lock_domain="audit_events",
        sql=sql,
        result_sql=result_sql,
    )
    register_template(template)
    _CONFIG_WRITE_TEMPLATES[config.dataset] = template
    return template


@dataclass(frozen=True)
class AuditEventPersistResult:
    """The outcome of write_event_idempotent().

    Unlike IncidentCreationResult, this has no `created` flag: the
    audit_events table has no row_version-style counter to distinguish
    "this call's own INSERT just landed the row" from "the row already
    existed with a matching payload" without a second round-trip, and
    that distinction is not needed for this function's actual use case
    (guaranteeing an event lands exactly once under retry) -- only
    whether the intended payload matches what's now persisted, which the
    PayloadConflictError check below already enforces.
    """

    event_id: str
    persisted_event_type: str
    persisted_subject: str
    persisted_outcome: str


def _conflicts(intended: AuditEvent, persisted: dict) -> list[str]:
    conflicts = []
    if persisted["event_type"] != intended.event_type:
        conflicts.append("event_type")
    if persisted["subject"] != intended.subject:
        conflicts.append("subject")
    if persisted["outcome"] != intended.outcome:
        conflicts.append("outcome")
    return conflicts


def write_event_idempotent(
    client: "TransactionalClientLike",
    event: AuditEvent,
    *,
    actor: Optional[str] = None,
    config: Optional[PlatformConfig] = None,
) -> AuditEventPersistResult:
    """Atomically write `event` if no row exists yet under its event_id,
    guarded by the 'audit_events' write-lock domain.

    Applies the same secret-scan shared.audit.write_event() applies
    (via shared.audit.validate_no_secrets(), the public wrapper around
    that module's single sensitive-field check) before anything is sent
    to BigQuery -- this module never re-implements that scan.

    Idempotent: a second call with the identical event_id and matching
    event_type/subject/outcome is a successful no-op (created=False). A
    second call with the same event_id but different event_type/subject/
    outcome raises PayloadConflictError -- the message never includes
    any of the actual values, only which field(s) differed.

    `actor` defaults to `event.actor` (the audit event's own actor field)
    when not given explicitly -- it is used only to stamp the write_locks
    row's last_touched_by for observability, never persisted as part of
    the audit_events row itself (event.actor already covers that).
    """

    validate_no_secrets(event.context)
    context_json = json.dumps(event.context, sort_keys=True)

    template = _write_template_for(config) if config is not None else WRITE_AUDIT_EVENT_TXN
    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=template.name,
                params={
                    "actor": actor if actor is not None else event.actor,
                    "event_id": event.event_id,
                    "timestamp": event.timestamp,
                    "event_type": event.event_type,
                    "subject": event.subject,
                    "outcome": event.outcome,
                    "context_json": context_json,
                },
            )
        ],
    )

    if not result.result_rows:
        raise RuntimeError(
            f"WRITE_AUDIT_EVENT_TXN committed but no row was found for "
            f"event_id={event.event_id!r} afterward -- this should be "
            "unreachable."
        )

    persisted = result.result_rows[0]
    conflicts = _conflicts(event, persisted)
    if conflicts:
        raise PayloadConflictError(
            f"event_id={event.event_id!r} conflicts on fields: "
            f"{', '.join(sorted(conflicts))} (values withheld)"
        )

    return AuditEventPersistResult(
        event_id=persisted["event_id"],
        persisted_event_type=persisted["event_type"],
        persisted_subject=persisted["subject"],
        persisted_outcome=persisted["outcome"],
    )
