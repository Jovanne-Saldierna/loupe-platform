"""Audit event persistence.

Owns exactly one responsibility: reading and writing AuditEvent records.
Uses shared.data_service.run_query() and the injected client's
insert_rows_json() for the actual BigQuery I/O rather than constructing
its own client, so query-safety enforcement (parameter binding, byte
limits, timeouts) stays centralized in shared/data_service.py.

Like data_service.py's incident functions, the read/write functions here
define the intended contract and are tested against a fake client -- the
loupe_platform.audit_events table does not exist in BigQuery yet
(Phase 6A defines its schema in shared/schema_management.py; nothing
creates it until an explicit bootstrap command is run -- see that
module's docstring).

Naming note (Phase 6, amendment 5): this table was originally named
audit_log in early Phase 2/5 scaffolding. It has never existed in a live
BigQuery dataset, so renaming it to audit_events (matching the shared
AuditEvent contract and Phase 6's documentation) required no data
migration, only updating this constant and its references -- done here
alongside shared/schema_management.py's initial bootstrap DDL.
AUDIT_TABLE is kept as a plain module constant, matching
data_service.py's INCIDENTS_TABLE pattern, rather than routed through
shared.config.PlatformConfig yet: wiring every persistence module through
PlatformConfig's table-name properties is app-wiring work (Phase 6D),
not part of 6A's contract/schema/migration scope.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict
from typing import Any, Optional, Protocol

from shared.config import DEFAULT_DATASET
from shared.data_service import BigQueryClientLike, run_query
from shared.models import AuditEvent

# Phase 6E correction: dataset-parameterized from LOUPE_DATASET (see
# shared/data_service.py's INCIDENTS_TABLE for the identical rationale) --
# was previously a hardcoded "loupe_platform.audit_events" literal.
AUDIT_TABLE = f"{os.environ.get('LOUPE_DATASET', DEFAULT_DATASET)}.audit_events"

# Explicit, exact sensitive field names. Deliberately NOT a bare "key" --
# matching on the substring "key" alone would reject legitimate business
# fields like primary_key, metric_key, key_findings, and keyboard_event.
# Matching is done against the normalized full name and against exact
# multi-word suffixes (e.g. "anthropic_api_key" ends with "_api_key"),
# never bare substring containment.
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "secret",
        "token",
        "password",
        "credential",
        "private_key",
        "access_token",
    }
)


def _normalize_field_name(name: str) -> str:
    """Normalize a context key to snake_case for sensitive-field matching.

    Inserts underscores at camelCase boundaries, lowercases, and collapses
    any run of non-alphanumeric characters into a single underscore --
    e.g. "ANTHROPIC_API_KEY" and "anthropicApiKey" both normalize to
    "anthropic_api_key".
    """

    with_boundaries = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    lowered = with_boundaries.lower()
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def _is_sensitive_field_name(name: str) -> bool:
    """Return True only for genuinely sensitive field names.

    Matches the normalized name exactly against _SENSITIVE_FIELD_NAMES,
    or as an exact trailing word-boundary suffix (e.g. "gcp_credential"
    ends with "_credential"). This intentionally does NOT match on bare
    substring containment of "key" or any other fragment -- that would
    reject legitimate fields such as primary_key, metric_key,
    key_findings, and keyboard_event, none of which are sensitive.
    """

    normalized = _normalize_field_name(name)
    if normalized in _SENSITIVE_FIELD_NAMES:
        return True
    return any(
        normalized.endswith(f"_{sensitive}") for sensitive in _SENSITIVE_FIELD_NAMES
    )


class InsertableClient(BigQueryClientLike, Protocol):
    """A BigQueryClientLike that can also stream-insert rows, per
    google.cloud.bigquery.Client.insert_rows_json's signature.
    """

    def insert_rows_json(self, table: str, json_rows: list[dict]) -> list[Any]: ...


def _row_to_event(row: dict) -> AuditEvent:
    return AuditEvent(
        event_id=row["event_id"],
        timestamp=row["timestamp"],
        actor=row["actor"],
        event_type=row["event_type"],
        subject=row["subject"],
        outcome=row["outcome"],
        context=row.get("context") or {},
    )


def list_events_for_subject(
    client: BigQueryClientLike, subject: str, *, limit: int = 50
) -> list[AuditEvent]:
    """Return the most recent audit events for a given subject (e.g. a
    metric name, an incident_id, or a review_id), newest first.
    """

    sql = f"""
        SELECT * FROM `{AUDIT_TABLE}`
        WHERE subject = @subject
        ORDER BY timestamp DESC
        LIMIT @limit
    """
    rows = run_query(client, sql, {"subject": subject, "limit": limit})
    return [_row_to_event(row) for row in rows]


def validate_no_secrets(context: dict) -> None:
    """Public entry point for the same secret-scan build_event()/
    write_event() already apply internally.

    Exists so other persistence modules (e.g.
    shared/audit_persistence.py's idempotent, transactional audit-event
    write) can validate a context dict they did not construct via
    build_event() without reaching into this module's private
    _reject_secrets_in_context() -- keeping the sensitive-field list and
    the recursive-walk logic defined exactly once, here.
    """

    _reject_secrets_in_context(context)


def _reject_secrets_in_context(context: dict) -> None:
    """Fail loudly rather than silently write a secret into the audit
    log. This is a defensive check, not a substitute for callers being
    careful about what they pass in as context.

    The error message includes only the rejected field NAME, never its
    value -- the whole point of this check is to keep secret values out
    of logs and audit trails, so the error itself must not leak one.

    Recurses into nested dicts and lists (Phase 6, amendment 9): a
    top-level-only scan would miss e.g.
    context={"request": {"headers": {"api_key": "..."}}} or
    context={"attempts": [{"token": "..."}]} entirely. Traversal covers
    dict values that are themselves dicts or lists, and list elements
    that are themselves dicts or lists -- scalar list elements (strings,
    numbers) are not field names and are never checked as one.
    """

    _walk_context_for_secrets(context, path=())


def _walk_context_for_secrets(value: object, *, path: tuple[str, ...]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _is_sensitive_field_name(key):
                located = ".".join((*path, key))
                raise ValueError(
                    f"Audit event context key {located!r} looks like a "
                    "sensitive field and must not be written to the audit "
                    "log. (The value itself is never included in this error.)"
                )
            _walk_context_for_secrets(nested, path=(*path, key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_context_for_secrets(item, path=path)


def build_event(
    *,
    event_id: str,
    timestamp: str,
    actor: str,
    event_type: str,
    subject: str,
    outcome: str,
    context: Optional[dict] = None,
) -> AuditEvent:
    """Construct a well-formed AuditEvent.

    Callers (Governance, Triage) should build events through this
    function rather than constructing AuditEvent directly, so the
    secret-context check below has exactly one enforcement point instead
    of being re-implemented (or forgotten) at every call site.
    """

    event = AuditEvent(
        event_id=event_id,
        timestamp=timestamp,
        actor=actor,
        event_type=event_type,
        subject=subject,
        outcome=outcome,
        context=dict(context or {}),
    )
    _reject_secrets_in_context(event.context)
    return event


def write_event(client: "InsertableClient", event: AuditEvent) -> None:
    """Persist one audit event via BigQuery's streaming insert API.

    Uses insert_rows_json rather than an INSERT-statement query, since
    that's the standard, lower-latency path for appending single rows
    with nested/JSON fields (context) in BigQuery. Raises RuntimeError if
    BigQuery reports any row-level errors.
    """

    _reject_secrets_in_context(event.context)
    errors = client.insert_rows_json(AUDIT_TABLE, [asdict(event)])
    if errors:
        raise RuntimeError(f"Failed to write audit event {event.event_id!r}: {errors}")
