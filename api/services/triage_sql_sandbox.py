from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from api.dependencies import get_client
from api.models import TriageSqlSandboxRequest, TriageSqlSandboxResponse
from apps.data_quality_triage.sql_sandbox import MAX_ROWS, UnsafeSandboxQueryError, validate_and_wrap
from shared.data_service import run_query


def _stringify_cell(value: Any) -> Any:
    """JSON-safe, presentation-ready coercion for one result cell.
    Booleans/ints/floats/strings/None pass through unchanged; anything
    else (BigQuery's date/datetime/Decimal/bytes/etc. row values) is
    stringified so the response always serializes cleanly and the
    frontend never has to special-case a cell type."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    return str(value)


def _try_dry_run_bytes(client: Any, sql: str) -> int | None:
    """Best-effort BigQuery dry run to estimate bytes_processed before
    actually running the query, per "Prefer using BigQuery dry run first
    if available." Never raises -- a fake/test client, or any other
    reason the dry run can't be performed, simply means bytes_processed
    stays None rather than failing the whole sandbox request."""

    try:
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = client.query(sql, job_config=job_config)
        return getattr(job, "total_bytes_processed", None)
    except Exception:
        return None


def run_sandbox_query(payload: TriageSqlSandboxRequest) -> TriageSqlSandboxResponse:
    """Validate, then run, one read-only debugging SQL check.

    Never raises -- every outcome (deterministic rejection, a BigQuery
    execution error, or success) is returned as a TriageSqlSandboxResponse
    so the Playbook tab can render each state without a try/except of its
    own. Safety is decided entirely by
    apps.data_quality_triage.sql_sandbox.validate_and_wrap() before this
    function ever constructs a BigQuery client or touches the warehouse --
    no AI call is involved in that decision, per the product requirement."""

    try:
        wrapped_sql = validate_and_wrap(payload.sql, max_rows=MAX_ROWS)
    except UnsafeSandboxQueryError as exc:
        return TriageSqlSandboxResponse(status="rejected", error=str(exc), row_limit=MAX_ROWS)

    try:
        client = get_client()
    except Exception:
        return TriageSqlSandboxResponse(
            status="error",
            error="Could not connect to the warehouse to run this check.",
            row_limit=MAX_ROWS,
        )

    bytes_processed = _try_dry_run_bytes(client, wrapped_sql)

    try:
        rows = run_query(client, wrapped_sql)
    except Exception as exc:
        return TriageSqlSandboxResponse(
            status="error",
            error=str(exc),
            bytes_processed=bytes_processed,
            row_limit=MAX_ROWS,
        )

    columns = list(rows[0].keys()) if rows else []
    clean_rows = [{key: _stringify_cell(value) for key, value in row.items()} for row in rows]
    return TriageSqlSandboxResponse(
        status="success",
        columns=columns,
        rows=clean_rows,
        row_count=len(clean_rows),
        bytes_processed=bytes_processed,
        row_limit=MAX_ROWS,
    )
