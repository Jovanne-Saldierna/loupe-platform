"""The single application-facing gateway for BigQuery access and
persistence.

Per docs/architecture.md, this is the only module allowed to construct a
BigQuery client or execute a raw SQL query. shared/metric_catalog.py,
shared/audit.py, and (later) each app's own query modules call run_query()
from here rather than importing google.cloud.bigquery directly -- that
keeps parameter binding, byte-scanned limits, timeouts, and read-only
enforcement in exactly one place, per docs/contracts.md's "Query safety"
section.

Status of the persistence functions below: the `loupe_platform` BigQuery
dataset exists (created during service-account provisioning), but no
tables have been created in it yet. These functions define the intended
read/write contract for incidents and source health, and are proven
correct against a fake, in-memory client (tests/shared/conftest.py) --
not against a real warehouse. Creating the actual tables and validating
this contract end-to-end against live BigQuery is Phase 6 (integration
tests), and must not require real credentials to author or unit-test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dataclasses import replace
from typing import Any, Optional, Protocol

import sqlglot
from sqlglot import exp

from shared.config import DEFAULT_DATASET
from shared.incidents import is_active_status, validate_transition
from shared.models import Incident, IncidentStatus, SourceHealth, SourceHealthStatus

# Phase 6E correction: this constant used to be a hardcoded literal
# ("loupe_platform.incidents"), which meant every read through this
# module silently targeted production loupe_platform even when an
# operator had configured LOUPE_DATASET=loupe_platform_test for an
# isolated test run -- a real, load-bearing gap that would have made a
# "use the existing loupe_platform_test dataset only" live validation
# either impossible or, worse, silently unsafe. Resolved from the SAME
# LOUPE_DATASET environment variable shared.config.load_platform_config()
# already reads, with the identical default -- computed once at import
# time (this module, like every shared/*_persistence.py module, is
# always loaded fresh in a new process per CLI invocation, so import-time
# resolution is sufficient and requires no new abstraction).
INCIDENTS_TABLE = f"{os.environ.get('LOUPE_DATASET', DEFAULT_DATASET)}.incidents"


@dataclass(frozen=True)
class QuerySafetyConfig:
    """Query safety limits, per docs/contracts.md: 'Apply bytes-scanned
    limits and timeouts where supported.'
    """

    maximum_bytes_billed: int = 500_000_000  # 500 MB default ceiling
    timeout_seconds: float = 30.0


DEFAULT_SAFETY = QuerySafetyConfig()


class BigQueryClientLike(Protocol):
    """Structural type for anything run_query() can execute against --
    satisfied by both a real google.cloud.bigquery.Client and any test
    fake that implements .query(sql, job_config=...).result().
    """

    def query(self, sql: str, job_config: Any = None) -> Any: ...


def get_bigquery_client(project: str, location: Optional[str] = None) -> "BigQueryClientLike":
    """Construct a real BigQuery client for `project`.

    Uses Application Default Credentials locally and the deployed
    service-account identity in production, per the platform's
    credential strategy (docs/development.md) -- never a downloaded or
    checked-in JSON key file. Notably, no `credentials=` argument is ever
    passed to bigquery.Client(): omitting it is what makes ADC resolution
    happen automatically (environment variable, attached service account,
    or `gcloud auth application-default login`), and passing a stray
    `credentials=` here would be exactly the kind of hard-coded-file path
    docs/development.md prohibits.

    `location` is optional and passed straight through to the client --
    per BigQuery's job-locality rules, queries against datasets in a
    specific location (e.g. the "US" multi-region used by
    `loupe_platform`) should be run with that same client-level location
    set, rather than relying on per-query defaults.

    This function is intentionally the only place in the whole platform
    that imports google.cloud.bigquery for client construction. It is
    unit-tested by mocking google.cloud.bigquery.Client (see
    tests/shared/test_data_service.py::test_get_bigquery_client_*) so the
    project/location wiring and the absence of any credential argument
    are both verified -- but no live integration test is included here,
    since that would require real cloud credentials, which this migration
    explicitly must not introduce.
    """

    from google.cloud import bigquery  # local import: this stays the one
    # hard, credential-requiring dependency in an otherwise-testable module

    return bigquery.Client(project=project, location=location)


class UnsafeQueryError(ValueError):
    """Raised when SQL passed to run_query() is not a read-only statement.

    Per docs/contracts.md's query-safety section, run_query() is the one
    and only path any SQL -- hand-written or LLM-generated -- takes to
    reach BigQuery. Enforcing "read-only" here, not just at the caller,
    is what makes it safe to eventually let an LLM draft SQL for review:
    even a compromised or hallucinating caller cannot smuggle DML, DDL,
    or a stacked second statement through this function.

    Enforcement is done by parsing the SQL with sqlglot (BigQuery
    dialect) and requiring the parsed statement to be a SELECT, UNION,
    INTERSECT, or EXCEPT. This was verified directly against sqlglot's
    actual behavior (not assumed) for INSERT/UPDATE/DELETE/DROP/CREATE/
    MERGE (each parses to its own non-Select/Union expression type) and
    for multi-statement injection such as "SELECT 1; DROP TABLE t;"
    (parses to exp.Block, which is neither Select nor Union). Unparseable
    SQL is also rejected rather than assumed safe.
    """


_READ_ONLY_STATEMENT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def _ensure_read_only(sql: str) -> None:
    try:
        parsed = sqlglot.parse_one(sql, read="bigquery")
    except Exception as exc:
        raise UnsafeQueryError(
            "Could not parse this SQL to confirm it is read-only, so it "
            f"was refused rather than assumed safe. Parse error: {exc}"
        ) from exc

    if parsed is None or not isinstance(parsed, _READ_ONLY_STATEMENT_TYPES):
        statement_type = type(parsed).__name__ if parsed is not None else "None"
        raise UnsafeQueryError(
            "run_query() only executes read-only SELECT / UNION / INTERSECT "
            f"/ EXCEPT statements; the parsed statement type was "
            f"{statement_type!r}. DML, DDL, and multi-statement SQL (e.g. a "
            "trailing ';' followed by a second statement, which sqlglot "
            "parses as a Block) are rejected."
        )


def _scalar_type_for(value: Any) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    return "STRING"


def _build_job_config(params: Optional[dict[str, Any]], safety: QuerySafetyConfig) -> Any:
    from google.cloud import bigquery

    query_params = []
    for name, value in (params or {}).items():
        if isinstance(value, (list, tuple)):
            if not value:
                raise ValueError(f"Array parameter {name!r} must not be empty")
            array_type = _scalar_type_for(value[0])
            query_params.append(bigquery.ArrayQueryParameter(name, array_type, list(value)))
        else:
            query_params.append(
                bigquery.ScalarQueryParameter(name, _scalar_type_for(value), value)
            )

    return bigquery.QueryJobConfig(
        query_parameters=query_params,
        maximum_bytes_billed=safety.maximum_bytes_billed,
    )


def run_query(
    client: "BigQueryClientLike",
    sql: str,
    params: Optional[dict[str, Any]] = None,
    *,
    safety: QuerySafetyConfig = DEFAULT_SAFETY,
) -> list[dict]:
    """Execute a parameterized, read-only query and return rows as plain
    dicts, in the order BigQuery returned them.

    Full safety contract (see tests/shared/test_data_service.py for the
    tests proving each of these):

    - Read-only enforcement: `sql` is parsed with sqlglot and rejected
      via UnsafeQueryError unless it is a SELECT/UNION/INTERSECT/EXCEPT
      statement. This is not deferred -- it is enforced on every call,
      before any parameter binding or client interaction happens.
    - Named parameter binding, never string interpolation: `params`
      values are always passed to BigQuery as ScalarQueryParameter or
      ArrayQueryParameter objects. The `sql` text itself is never
      modified, concatenated, or formatted with parameter values -- it is
      passed to client.query() completely unchanged from what the caller
      supplied.
    - Bytes-scanned limit: `safety.maximum_bytes_billed` (500 MB by
      default) is always set on the QueryJobConfig.
    - Timeout: `safety.timeout_seconds` (30s by default) is always passed
      to `.result(timeout=...)`.
    - Query exceptions are never caught or swallowed here -- if
      client.query(...) or .result(...) raises, that exception propagates
      to the caller unchanged.
    - Empty results return an empty list, not None and not an error.
    - The returned structure is always `list[dict]`, one dict per row,
      built with the exact keys/values BigQuery's row iterator produced,
      in the iterator's order -- deterministic for a given result set.
    """

    _ensure_read_only(sql)
    job_config = _build_job_config(params, safety)
    result = client.query(sql, job_config=job_config).result(timeout=safety.timeout_seconds)
    return [dict(row) for row in result]


# ---------------------------------------------------------------------------
# Table metadata (schema/row-count introspection -- not SQL queries)
# ---------------------------------------------------------------------------
#
# list_tables() and get_table_metadata() call BigQuery's metadata REST API
# (Client.list_tables / Client.get_table), not the query-job API run_query()
# wraps. They have no SQL text to parse, so UnsafeQueryError's read-only
# enforcement does not apply to them -- there's nothing to smuggle DML/DDL
# through. They're kept in this module anyway, per docs/architecture.md:
# this stays the one place in the platform that touches a BigQuery client
# object at all, metadata calls included, so no caller (e.g. Data Quality
# Triage's table-profiling step) ever constructs or reaches into a client
# directly.


class TableMetadataClientLike(Protocol):
    """Structural type for anything list_tables()/get_table_metadata() can
    call against -- satisfied by both a real google.cloud.bigquery.Client
    and a test fake implementing .list_tables(dataset) and
    .get_table(table_ref).
    """

    def list_tables(self, dataset: str) -> Any: ...

    def get_table(self, table_ref: str) -> Any: ...


def list_tables(client: "TableMetadataClientLike", dataset: str) -> list[str]:
    """Return every table_id in `dataset` (e.g.
    "bigquery-public-data.thelook_ecommerce")."""

    return [table.table_id for table in client.list_tables(dataset)]


@dataclass(frozen=True)
class TableMetadata:
    """Raw schema/row-count facts about one table, straight from
    BigQuery's metadata API -- no derived heuristics (candidate key
    columns, freshness classification, etc.) live here. Callers (e.g.
    apps/data_quality_triage/profiling.py) build their own derived
    profile shapes on top of this.

    column_types maps each column name to BigQuery's field_type string
    (e.g. "STRING", "INTEGER", "TIMESTAMP") -- added alongside `columns`
    (Phase 4 correction) so callers can detect column *type* changes, not
    just additions/removals, without a second round-trip to BigQuery.
    Defaults to an empty dict so existing callers that only care about
    column names are unaffected.
    """

    table_id: str
    row_count: int
    modified_at: Optional[str]  # ISO-8601 UTC string, or None if BigQuery reported no modified time
    columns: list[str]
    column_types: dict[str, str] = field(default_factory=dict)


def get_table_metadata(client: "TableMetadataClientLike", dataset: str, table_id: str) -> TableMetadata:
    """Fetch one table's schema and row-count metadata."""

    table = client.get_table(f"{dataset}.{table_id}")
    modified = getattr(table, "modified", None)
    modified_at = modified.isoformat() if modified is not None else None
    columns = [column.name for column in table.schema]
    column_types = {column.name: getattr(column, "field_type", "UNKNOWN") for column in table.schema}
    return TableMetadata(
        table_id=table_id,
        row_count=int(table.num_rows or 0),
        modified_at=modified_at,
        columns=columns,
        column_types=column_types,
    )


# ---------------------------------------------------------------------------
# Incident persistence (contract only -- see module docstring)
# ---------------------------------------------------------------------------


def _row_to_incident(row: dict) -> Incident:
    return Incident(
        incident_id=row["incident_id"],
        created_at=row["created_at"],
        dataset=row["dataset"],
        table_id=row["table_id"],
        check_type=row["check_type"],
        severity=row["severity"],
        status=row["status"],
        observed_value=row.get("observed_value"),
        expected_value=row.get("expected_value"),
        sql_template=row.get("sql_template"),
        query_hash=row.get("query_hash"),
        affected_metrics=row.get("affected_metrics") or [],
        affected_dashboards=row.get("affected_dashboards") or [],
        playbook=row.get("playbook"),
        owner=row.get("owner"),
        acknowledged_at=row.get("acknowledged_at"),
        resolved_at=row.get("resolved_at"),
        resolution_notes=row.get("resolution_notes"),
        rule_version=row.get("rule_version"),
        recurrence_of_incident_id=row.get("recurrence_of_incident_id"),
    )


def get_incident(client: "BigQueryClientLike", incident_id: str) -> Optional[Incident]:
    """Fetch a single incident by id, or None if it does not exist.

    This is the one and only way apply_incident_transition() below reads
    "the current status" -- it never trusts a possibly-stale in-memory
    Incident object that the caller happened to be holding onto, which is
    exactly the read-modify-write hazard this function exists to close.
    """

    sql = f"""
        SELECT * FROM `{INCIDENTS_TABLE}`
        WHERE incident_id = @incident_id
        LIMIT 1
    """
    rows = run_query(client, sql, {"incident_id": incident_id})
    if not rows:
        return None
    return _row_to_incident(rows[0])


def list_active_incidents_for_table(
    client: "BigQueryClientLike", dataset: str, table_id: str
) -> list[Incident]:
    """Return only incidents whose status is currently active, per
    shared.incidents.ACTIVE_INCIDENT_STATUSES.

    Filtering happens here, in code, after the query returns -- not by
    trusting a WHERE clause alone -- so the active/inactive rule stays
    defined in exactly one place (shared/incidents.py) regardless of what
    the SQL selects.
    """

    sql = f"""
        SELECT * FROM `{INCIDENTS_TABLE}`
        WHERE dataset = @dataset AND table_id = @table_id
        ORDER BY created_at DESC
    """
    rows = run_query(client, sql, {"dataset": dataset, "table_id": table_id})
    incidents = [_row_to_incident(row) for row in rows]
    return [incident for incident in incidents if is_active_status(incident.status)]


def derive_source_health(
    client: "BigQueryClientLike", dataset: str, table_id: str
) -> SourceHealth:
    """Derive current source health from active incidents only.

    Resolved and detected-but-unconfirmed incidents never affect this
    result -- see shared/incidents.py's ACTIVE_INCIDENT_STATUSES, the
    explicit, non-ordering-based rule this depends on. The highest active
    severity present determines the result: any active "high" severity
    incident makes the table "critical"; otherwise any active incident at
    all (regardless of severity, including "mitigated" ones, which are
    still active per ACTIVE_INCIDENT_STATUSES) makes it "degraded"; zero
    active incidents (including tables with no incident rows at all, or
    an unknown table_id that simply returns no rows) makes it "healthy".
    """

    active = list_active_incidents_for_table(client, dataset, table_id)
    status: SourceHealthStatus
    if any(incident.severity == "high" for incident in active):
        status = "critical"
    elif active:
        status = "degraded"
    else:
        status = "healthy"

    return SourceHealth(
        dataset=dataset,
        table_id=table_id,
        status=status,
        active_incident_ids=[incident.incident_id for incident in active],
    )


class IncidentNotFoundError(RuntimeError):
    """Raised by apply_incident_transition() when incident_id does not
    resolve to any persisted incident."""


class ConcurrentModificationError(RuntimeError):
    """Raised when the incident's persisted status no longer matches what
    the caller expected it to be, per an explicit
    `expected_current_status` check.

    This is a caller-opt-in guard, not a substitute for real atomic
    persistence: see apply_incident_transition()'s docstring for the
    concurrency gap this does and does not close.
    """


def apply_incident_transition(
    client: "BigQueryClientLike",
    incident_id: str,
    target_status: IncidentStatus,
    *,
    expected_current_status: Optional[IncidentStatus] = None,
    resolution_notes: Optional[str] = None,
) -> Incident:
    """Validate a status transition against the CURRENTLY PERSISTED status
    and return the updated incident.

    This always re-fetches the incident via get_incident() rather than
    trusting an Incident object the caller happened to already be
    holding -- an earlier version of this function took an in-memory
    Incident directly and validated against its (possibly stale) .status,
    which meant two concurrent callers could both read "open", both
    validate open->acknowledged as legal, and both "succeed" even though
    one of their writes should have been rejected. Re-fetching closes the
    read side of that gap.

    Callers that need to guard against a second writer racing in between
    their own read and this call may pass `expected_current_status`
    (e.g. the status they last displayed to a user before that user
    clicked "acknowledge"). If the freshly-fetched persisted status does
    not match, ConcurrentModificationError is raised instead of silently
    proceeding against a status the caller never agreed to.

    IMPORTANT -- remaining concurrency limitation: this module has no
    real BigQuery persistence yet (see the module docstring). Once Phase
    6 adds a real UPDATE, re-fetching here is still not sufficient on its
    own to guarantee no lost updates, because a second writer could still
    commit between this function's read and that future UPDATE
    committing (classic TOCTOU). Closing that gap fully requires the
    eventual UPDATE itself to be conditional --
    `UPDATE ... SET status = @target_status
     WHERE incident_id = @incident_id AND status = @expected_current_status`
    -- and for the caller to check the affected-row count, retrying (by
    re-fetching and re-validating) if zero rows were affected. Until that
    conditional UPDATE exists, this function provides read-side
    protection only: it is explicitly NOT a claim of full atomic
    lifecycle safety, and callers relying on it under real concurrent
    load should not assume otherwise.

    Deciding whether a "resolved" -> "open" transition should reopen this
    same record or instead create a new Incident linked via
    recurrence_of_incident_id is still the caller's responsibility -- see
    shared/incidents.py's "Reopen vs. new linked incident" section.
    """

    current = get_incident(client, incident_id)
    if current is None:
        raise IncidentNotFoundError(f"No incident found with incident_id={incident_id!r}")

    if (
        expected_current_status is not None
        and current.status != expected_current_status
    ):
        raise ConcurrentModificationError(
            f"Incident {incident_id!r} status changed since it was last "
            f"read: expected {expected_current_status!r}, but the "
            f"currently persisted status is {current.status!r}. Re-fetch "
            "the incident and retry the transition."
        )

    validate_transition(current.status, target_status)
    updated = replace(current, status=target_status)
    if resolution_notes is not None:
        updated = replace(updated, resolution_notes=resolution_notes)
    return updated
