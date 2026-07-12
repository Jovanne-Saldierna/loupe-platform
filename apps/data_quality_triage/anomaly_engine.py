"""Live, ratio-based deterministic checks that require querying BigQuery
(duplicate-key ratio, null-value ratio, volume drift), plus
evaluate_profiles(), which combines these with checks.py's metadata-only
checks to produce the full set of TableFindings for a dataset.

All querying goes through shared.data_service.run_query() -- this module
never constructs a bigquery.Client or calls .query()/.get_table() directly,
per docs/architecture.md. SQL text is assembled with the dataset/table/
column *identifiers* the profiling step already discovered (not user input,
so no parameter binding is needed for them); no query VALUE is ever
interpolated into SQL text here.

--- Behavioral change vs. the original app ---
The original src/anomaly_engine.py called `client.query(sql).result()` /
`.to_dataframe()` directly and returned a pandas DataFrame alongside a list
of local Incident objects. This version calls
shared.data_service.run_query(), which returns `list[dict]` (see
shared/data_service.py's run_query() docstring) -- there is no DataFrame
anywhere in this module. evaluate_profiles() returns `list[TableFinding]`
(apps/data_quality_triage/models.py), not Incidents; promoting a finding
into a shared.models.Incident is checks.py's job
(build_incident_from_finding / findings_to_incidents), keeping "detect" and
"promote to the cross-app contract" as two separate steps.

--- Phase 4 correction: query exceptions ---
_safe_run_query() below isolates every live check's run_query() call: if
the query itself fails to execute (timeout, permission denial, malformed
SQL, or any other warehouse/query-layer failure), that failure is caught
and converted into a deterministic, status="error" TableFinding via
_query_exception_finding() -- never left to propagate and abort
evaluate_profiles() for every other table and check. This is the
documented distinction between "the query failed to execute" (an error
finding, an operability problem) and "the query executed fine and found
bad data" (a normal fail/warn finding from the calling check function).
_classify_exception() performs this classification deterministically --
type-name plus a safe, internal-only substring match against the
exception's message -- never via an LLM call and never by echoing the raw
message text into a finding: only the exception's type name and a small,
fixed category label ever reach a TableFinding's summary/likely_root_cause,
so credentials, parameter values, or query text a warehouse error might
embed can never leak into a finding, an incident, or an audit event.
"""

from __future__ import annotations

from typing import Literal, Optional

from apps.data_quality_triage.checks import (
    classify_ratio_severity,
    classify_volume_drift_severity,
    run_metadata_checks,
    status_for_severity,
)
from apps.data_quality_triage.models import CheckSeverity, SchemaSnapshot, TableFinding
from apps.data_quality_triage.profiling import TableProfile
from shared.data_service import BigQueryClientLike, run_query

# Thresholds ported verbatim from the original app's src/anomaly_engine.py.
_DUPLICATE_RATIO_THRESHOLDS = {"medium": 0.001, "high": 0.01, "critical": 0.05}
_NULL_RATIO_THRESHOLDS = {"medium": 0.02, "high": 0.1, "critical": 0.25}


# ---------------------------------------------------------------------------
# Query-exception handling (Phase 4 correction item 1, "Query exceptions")
# ---------------------------------------------------------------------------

QueryExceptionCategory = Literal["timeout", "permission_denied", "malformed_query", "execution_failure"]

# Keyword lists used only for internal classification -- never surfaced
# verbatim. Matching is on the exception's type name plus a lowercased
# substring search of str(exc), so this works across the different
# exception classes google-cloud-bigquery, its transport layer, or a fake
# test double might raise, without importing any of those types directly.
_TIMEOUT_KEYWORDS = ("timeout", "timed out", "deadline exceeded")
_PERMISSION_KEYWORDS = ("permission", "forbidden", "access denied", "unauthorized", "403")
_MALFORMED_KEYWORDS = ("syntax error", "invalid query", "parse error", "invalid_argument", "bad request", "400")

_ROOT_CAUSE_BY_CATEGORY: dict[QueryExceptionCategory, str] = {
    "timeout": (
        "The check query did not complete within the warehouse's allotted time -- "
        "the table may be very large, the warehouse under heavy load, or the query "
        "itself may need optimization."
    ),
    "permission_denied": (
        "The credentials running this check lack permission to query this table -- "
        "check IAM / service-account role bindings for the dataset."
    ),
    "malformed_query": (
        "The check's generated SQL was rejected by the warehouse as invalid -- "
        "likely a bug in the check's query-building logic rather than a data problem."
    ),
    "execution_failure": (
        "The check query failed for an unspecified warehouse-side reason. See "
        "platform logs (never this finding) for the underlying exception detail."
    ),
}

_SEVERITY_BY_CATEGORY: dict[QueryExceptionCategory, CheckSeverity] = {
    "timeout": "high",
    "permission_denied": "high",
    "malformed_query": "medium",
    "execution_failure": "high",
}


def _classify_exception(exc: Exception) -> QueryExceptionCategory:
    """Deterministically classify a query-execution exception into one of
    four fixed categories, using only the exception's type name and a
    safe, lowercased substring match against its message. This is a pure
    function of the exception object -- no LLM involvement, no
    caller-supplied severity or category, per docs/data-quality-triage.md's
    "AI does not decide whether data is broken" constraint applied to the
    query layer as well.
    """

    haystack = f"{type(exc).__name__} {exc}".lower()
    if any(keyword in haystack for keyword in _TIMEOUT_KEYWORDS):
        return "timeout"
    if any(keyword in haystack for keyword in _PERMISSION_KEYWORDS):
        return "permission_denied"
    if any(keyword in haystack for keyword in _MALFORMED_KEYWORDS):
        return "malformed_query"
    return "execution_failure"


def _query_exception_finding(*, table_id: str, check_name: str, exc: Exception) -> TableFinding:
    """Build a deterministic, status="error" TableFinding for a check
    query that failed to execute. Only the exception's type name and its
    fixed category label are ever included -- str(exc) (which could
    contain a credential, a bound parameter value, or raw query text
    depending on what the warehouse driver embeds in its error message)
    is used solely inside _classify_exception()'s internal keyword match
    and never appears in the returned finding.
    """

    category = _classify_exception(exc)
    return TableFinding(
        table_id=table_id,
        check_name="query_exception",
        status="error",
        severity=_SEVERITY_BY_CATEGORY[category],
        observed_value=None,
        threshold=None,
        summary=(
            f"The {check_name!r} check on {table_id} failed to execute "
            f"({category.replace('_', ' ')}: {type(exc).__name__})."
        ),
        likely_root_cause=_ROOT_CAUSE_BY_CATEGORY[category],
        sql=None,
    )


def _safe_run_query(
    client: "BigQueryClientLike", sql: str, *, table_id: str, check_name: str
) -> tuple[Optional[list[dict]], Optional[TableFinding]]:
    """Run `sql` via shared.data_service.run_query(), isolating any
    exception into a deterministic query_exception TableFinding instead of
    letting it propagate and abort evaluate_profiles() for every other
    table/check.

    Returns (rows, None) on success, or (None, finding) if the query
    itself failed to execute -- as distinct from a query that executed
    fine and simply found bad data (that produces a normal fail/warn
    finding from the calling check function, never this path).
    """

    try:
        return run_query(client, sql), None
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any warehouse/query-layer failure must be caught and classified, never left to crash the run.
        return None, _query_exception_finding(table_id=table_id, check_name=check_name, exc=exc)


def _duplicate_ratio_finding(
    client: "BigQueryClientLike", dataset: str, profile: TableProfile
) -> Optional[TableFinding]:
    if profile.primary_candidate is None:
        return None

    column = profile.primary_candidate
    sql = (
        "SELECT SAFE_DIVIDE(COUNT(*) - COUNT(DISTINCT CAST("
        f"{column} AS STRING)), COUNT(*)) AS ratio "
        f"FROM `{dataset}.{profile.table_id}`"
    )
    rows, exception_finding = _safe_run_query(
        client, sql, table_id=profile.table_id, check_name="duplicate_key_ratio"
    )
    if exception_finding is not None:
        return exception_finding
    ratio = rows[0]["ratio"] if rows and rows[0].get("ratio") is not None else 0.0

    severity = classify_ratio_severity(ratio, **_DUPLICATE_RATIO_THRESHOLDS)
    status = status_for_severity(severity)
    if status == "pass":
        return None

    return TableFinding(
        table_id=profile.table_id,
        check_name="duplicate_key_ratio",
        status=status,
        severity=severity,
        observed_value=ratio,
        threshold=_DUPLICATE_RATIO_THRESHOLDS["medium"],
        summary=f"{profile.table_id}.{column} has a {ratio:.2%} duplicate-key ratio.",
        likely_root_cause=(
            "Upstream ingestion may have re-run without deduplication, or "
            f"{column} is not actually a unique key for this table."
        ),
        sql=sql,
    )


def _null_ratio_finding(
    client: "BigQueryClientLike", dataset: str, profile: TableProfile
) -> Optional[TableFinding]:
    if not profile.nullable_candidates:
        return None

    column = profile.nullable_candidates[0]
    sql = (
        f"SELECT SAFE_DIVIDE(COUNTIF({column} IS NULL), COUNT(*)) AS ratio "
        f"FROM `{dataset}.{profile.table_id}`"
    )
    rows, exception_finding = _safe_run_query(client, sql, table_id=profile.table_id, check_name="null_ratio")
    if exception_finding is not None:
        return exception_finding
    ratio = rows[0]["ratio"] if rows and rows[0].get("ratio") is not None else 0.0

    severity = classify_ratio_severity(ratio, **_NULL_RATIO_THRESHOLDS)
    status = status_for_severity(severity)
    if status == "pass":
        return None

    return TableFinding(
        table_id=profile.table_id,
        check_name="null_ratio",
        status=status,
        severity=severity,
        observed_value=ratio,
        threshold=_NULL_RATIO_THRESHOLDS["medium"],
        summary=f"{profile.table_id}.{column} has a {ratio:.2%} null ratio.",
        likely_root_cause=(
            f"An upstream field ({column}) may have started arriving incomplete, "
            "or a schema/mapping change stopped populating it."
        ),
        sql=sql,
    )


def _volume_drift_finding(
    client: "BigQueryClientLike", dataset: str, profile: TableProfile
) -> Optional[TableFinding]:
    if not profile.temporal_candidates:
        return None

    column = profile.temporal_candidates[0]
    sql = (
        "WITH daily AS ("
        f"SELECT DATE({column}) AS day, COUNT(*) AS row_count "
        f"FROM `{dataset}.{profile.table_id}` "
        "GROUP BY day"
        "), "
        "latest AS (SELECT row_count FROM daily ORDER BY day DESC LIMIT 1), "
        "prior AS ("
        "SELECT AVG(row_count) AS avg_row_count FROM daily "
        "WHERE day < (SELECT MAX(day) FROM daily) "
        "AND day >= DATE_SUB((SELECT MAX(day) FROM daily), INTERVAL 7 DAY)"
        ") "
        "SELECT latest.row_count AS latest_count, prior.avg_row_count AS prior_avg "
        "FROM latest, prior"
    )
    rows, exception_finding = _safe_run_query(client, sql, table_id=profile.table_id, check_name="volume_drift")
    if exception_finding is not None:
        return exception_finding
    if not rows or not rows[0].get("prior_avg"):
        return None

    latest_count = rows[0]["latest_count"]
    prior_avg = rows[0]["prior_avg"]
    if latest_count is None or prior_avg in (None, 0):
        return None

    drift_ratio = latest_count / prior_avg
    severity = classify_volume_drift_severity(drift_ratio)
    if severity is None:
        return None
    status = status_for_severity(severity)

    return TableFinding(
        table_id=profile.table_id,
        check_name="volume_drift",
        status=status,
        severity=severity,
        observed_value=drift_ratio,
        threshold=1.0,
        summary=(
            f"{profile.table_id}'s latest-day row count is {drift_ratio:.2f}x "
            "its trailing 7-day average."
        ),
        likely_root_cause=(
            "Ingestion pipeline may have failed, double-run, or an upstream "
            "business volume shift occurred."
        ),
        sql=sql,
    )


def evaluate_profiles(
    client: "BigQueryClientLike",
    dataset: str,
    profiles: list[TableProfile],
    *,
    schema_baselines: Optional[dict[str, SchemaSnapshot]] = None,
) -> list[TableFinding]:
    """Run every deterministic check -- metadata-only and live ratio-query
    -- against every profile, returning all findings that fired (passing
    findings are already excluded by the individual check functions; a
    query_exception finding takes the place of a check's normal result
    whenever that check's query fails to execute -- see
    _safe_run_query()).

    Wires both check families into the live path, closing the gap where
    the original app's metadata-only checks (checks.py) existed but were
    never called by its live state assembly -- see checks.py's module
    docstring for the full account of that behavioral change.

    `schema_baselines` is an optional {table_id: SchemaSnapshot} map,
    None by default. There is no live baseline-storage mechanism yet
    (Phase 6); callers with nothing to pass simply omit it, and every
    profile's schema_drift check reports an honest not_evaluated finding
    rather than being skipped -- see checks.check_schema_drift()'s
    docstring.
    """

    baselines = schema_baselines or {}
    findings: list[TableFinding] = []
    for profile in profiles:
        findings.extend(run_metadata_checks(profile, schema_baseline=baselines.get(profile.table_id)))

        duplicate_finding = _duplicate_ratio_finding(client, dataset, profile)
        if duplicate_finding is not None:
            findings.append(duplicate_finding)

        null_finding = _null_ratio_finding(client, dataset, profile)
        if null_finding is not None:
            findings.append(null_finding)

        drift_finding = _volume_drift_finding(client, dataset, profile)
        if drift_finding is not None:
            findings.append(drift_finding)

    return findings
