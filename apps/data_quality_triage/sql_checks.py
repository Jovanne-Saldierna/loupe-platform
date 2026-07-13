"""Deterministic "suggested debugging SQL" templates for Data Quality Triage.

Per the product thesis, the Triage agent should give a data engineer a
running start on investigating an incident -- but the SQL offered here is
templated from `check_type` and `table_id` alone, entirely deterministically
(no LLM call, no execution). Nothing in this module ever runs a query
against BigQuery; it only builds read-only SQL TEXT for a human to review
and run themselves. Every template is explicitly documented as suggested,
not-yet-run SQL wherever it is rendered (see api/services/triage_playbook.py
and the frontend playbook panel) so nothing here can be mistaken for a
result that has already been computed.

Keyword matching on `check_type` intentionally stays loose (substring match,
not an exact enum) because real check_type values already vary across this
codebase's own detection code (apps/data_quality_triage/checks.py uses
"row_count_empty"/"freshness_delay"/"schema_drift"; anomaly_engine.py uses
"duplicate_key_ratio"/"null_ratio"/"volume_drift"/"query_exception") and
persisted incidents may carry either vocabulary or a future one. A single
deterministic generic fallback template covers any check_type this module
doesn't recognize, rather than raising or guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.data_quality_triage.profiling import QUALIFIED_DATASET


@dataclass(frozen=True)
class SuggestedSqlCheck:
    title: str
    sql: str


def _qualified(table_id: str) -> str:
    """Bare table name -> a fully-qualified, backtick-quoted BigQuery
    reference against the same dataset the rest of Triage already reads
    from. If `table_id` already looks qualified (contains a dot), it is
    used as-is rather than double-qualified."""

    if "." in table_id:
        return f"`{table_id}`"
    return f"`{QUALIFIED_DATASET}.{table_id}`"


_SQL_COMMENT_HEADER = "-- Suggested debugging SQL (not executed automatically). Review before running."


def _row_count_checks(table_id: str) -> list[SuggestedSqlCheck]:
    table = _qualified(table_id)
    return [
        SuggestedSqlCheck(
            title="Compare recent row counts to a trailing baseline",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"SELECT\n"
                f"  DATE(created_at) AS day,\n"
                f"  COUNT(*) AS row_count\n"
                f"FROM {table}\n"
                f"WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)\n"
                f"GROUP BY day\n"
                f"ORDER BY day DESC;"
            ),
        )
    ]


def _null_rate_checks(table_id: str) -> list[SuggestedSqlCheck]:
    table = _qualified(table_id)
    return [
        SuggestedSqlCheck(
            title="Calculate null rate over time for the affected column",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"-- Replace <column> with the column the incident flagged.\n"
                f"SELECT\n"
                f"  DATE(created_at) AS day,\n"
                f"  COUNTIF(<column> IS NULL) AS null_count,\n"
                f"  COUNT(*) AS total_rows,\n"
                f"  SAFE_DIVIDE(COUNTIF(<column> IS NULL), COUNT(*)) AS null_rate\n"
                f"FROM {table}\n"
                f"WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)\n"
                f"GROUP BY day\n"
                f"ORDER BY day DESC;"
            ),
        )
    ]


def _duplicate_key_checks(table_id: str) -> list[SuggestedSqlCheck]:
    table = _qualified(table_id)
    return [
        SuggestedSqlCheck(
            title="Check duplicate business keys",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"-- Replace `id` with the table's actual business/primary key if different.\n"
                f"SELECT\n"
                f"  id AS business_key,\n"
                f"  COUNT(*) AS occurrences\n"
                f"FROM {table}\n"
                f"GROUP BY business_key\n"
                f"HAVING COUNT(*) > 1\n"
                f"ORDER BY occurrences DESC\n"
                f"LIMIT 50;"
            ),
        )
    ]


def _freshness_checks(table_id: str) -> list[SuggestedSqlCheck]:
    table = _qualified(table_id)
    return [
        SuggestedSqlCheck(
            title="Compare latest timestamp to the expected freshness threshold",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"SELECT\n"
                f"  MAX(created_at) AS latest_row_timestamp,\n"
                f"  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(created_at), MINUTE) AS minutes_since_latest_row\n"
                f"FROM {table};"
            ),
        )
    ]


def _schema_drift_checks(table_id: str) -> list[SuggestedSqlCheck]:
    dataset = QUALIFIED_DATASET if "." not in table_id else table_id.rsplit(".", 1)[0]
    bare_name = table_id.rsplit(".", 1)[-1]
    return [
        SuggestedSqlCheck(
            title="Inspect current column names and types",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"SELECT column_name, data_type\n"
                f"FROM `{dataset}`.INFORMATION_SCHEMA.COLUMNS\n"
                f"WHERE table_name = '{bare_name}'\n"
                f"ORDER BY ordinal_position;"
            ),
        )
    ]


def _generic_checks(table_id: str) -> list[SuggestedSqlCheck]:
    table = _qualified(table_id)
    return [
        SuggestedSqlCheck(
            title="Spot-check recent rows",
            sql=(
                f"{_SQL_COMMENT_HEADER}\n"
                f"SELECT *\n"
                f"FROM {table}\n"
                f"ORDER BY created_at DESC\n"
                f"LIMIT 100;"
            ),
        )
    ]


def suggested_sql_checks(check_type: str, table_id: str) -> list[SuggestedSqlCheck]:
    """Deterministic, check_type-driven suggested SQL for a human to run
    next. No BigQuery client, no execution, no AI -- purely a template
    lookup keyed off the incident's own check_type and table_id."""

    key = (check_type or "").lower()
    if "duplicate" in key:
        return _duplicate_key_checks(table_id)
    if "null" in key:
        return _null_rate_checks(table_id)
    if "fresh" in key:
        return _freshness_checks(table_id)
    if "row_count" in key or "empty" in key or "volume" in key:
        return _row_count_checks(table_id)
    if "schema" in key:
        return _schema_drift_checks(table_id)
    return _generic_checks(table_id)


def suggested_debugging_steps(check_type: str, table_id: str) -> list[str]:
    """Deterministic, check_type-driven first-pass debugging steps -- plain
    checklist text, independent of and never overridden by AI narration.
    Kept in the same keyword-matched shape as suggested_sql_checks() so the
    two always agree on what kind of incident this is."""

    key = (check_type or "").lower()
    if "duplicate" in key:
        return [
            "Confirm whether the flagged business key is expected to be unique in this table.",
            "Check for a recent re-run, backfill, or retried load that may have double-inserted rows.",
            "Run the duplicate-key SQL check below to see which keys are affected and by how much.",
        ]
    if "null" in key:
        return [
            "Identify which column(s) are producing unexpected nulls.",
            "Check whether an upstream schema or ETL change introduced the nulls.",
            "Run the null-rate SQL check below to see whether this is a sudden spike or a gradual trend.",
        ]
    if "fresh" in key:
        return [
            f"Check whether the scheduled load or pipeline for {table_id} ran on time.",
            "Look for upstream extraction failures, retries, or delays in the pipeline's own logs.",
            "Run the freshness SQL check below to see exactly how far behind the data currently is.",
        ]
    if "row_count" in key or "empty" in key or "volume" in key:
        return [
            f"Confirm the latest load job for {table_id} completed successfully.",
            "Compare today's row count to the trailing 7-14 day baseline using the SQL check below.",
            "Check upstream pipeline logs for skipped, failed, or partial extraction runs.",
        ]
    if "schema" in key:
        return [
            "Compare the current schema to the last known-good baseline, if one is on file.",
            "Check for an upstream source or ETL change that added, removed, or renamed columns.",
            "Confirm downstream queries and dashboards still reference valid column names.",
        ]
    return [
        "Review the incident's observed/expected values and severity above.",
        f"Spot-check recent rows in {table_id} for anything unusual.",
        "Escalate to the table owner if the cause isn't clear from the data alone.",
    ]
