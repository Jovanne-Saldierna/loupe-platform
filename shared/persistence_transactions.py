"""The one, narrowly-scoped API for executing multi-statement BigQuery
transactions against persisted platform state.

Per Phase 6's approved amendments 1 and 2, this module exists precisely
BECAUSE shared.data_service.run_query() must stay read-only: certain
writes (a metric certification's version-insert + pointer-update +
audit-event-insert; an incident's status-update + transition-insert;
a schema-baseline promotion's upsert + audit-event-insert) must succeed
or fail as one atomic unit, and BigQuery supports this via multi-
statement DML scripts (`BEGIN TRANSACTION; ...; COMMIT;`), not via
run_query()'s SELECT-only surface.

execute_transaction() accepts ONLY a list of BoundStatement values, each
naming a template that was registered (at import time, by this codebase,
never at request time) via register_template(). There is no code path
from a caller-supplied or LLM-generated SQL string into a transaction --
a caller can only ever say "run the CERTIFY_METRIC_VERSION_TXN template
with these parameter values," never "run this SQL." This mirrors
run_query()'s own discipline of "the SQL text a caller can execute is
constrained by this module, not by caller intent" -- just enforced by a
closed template registry instead of a parser.

--- What Phase 6A actually implements here ---
Per amendment 3 ("Phase 6A may implement the fake-client contracts,
schemas, and transaction scaffolding"), this module builds the full
mechanism -- template registration/allowlisting, script rendering,
retry-with-backoff-and-jitter, error classification, write-lock
handling, and result parsing -- but does NOT yet register any of the
real business templates (metric certification, incident transition,
baseline promotion). Those are registered in Phase 6B/6C alongside the
functions that use them. This keeps 6A's diff reviewable as "the
mechanism, proven against a fake client" without also asking you to
review real business-transaction SQL in the same subphase.

--- Concurrency correction (amendment 1) ---
An earlier draft of this plan claimed a conflicting concurrent
transaction would "observe zero affected rows." That is not how BigQuery
multi-statement transactions behave: Google's documentation states that
when two transactions conflict, BigQuery CANCELS one of them outright
(the cancelled transaction's query job fails with a retryable
"transaction aborted" style error), rather than committing it with a
zero-row DML result. This module's retry loop is built around that real
behavior: a cancelled/aborted transaction is classified as retryable,
retried with exponential backoff and jitter up to a bounded limit, and
only turned into a final ConcurrentModificationError once that limit is
exhausted -- never assumed to be distinguishable from a successful
zero-row UPDATE.

--- Uniqueness correction (amendment 2) ---
BigQuery does not enforce primary keys, and an insert-only MERGE alone
does not guarantee two concurrent writers can't both observe an ID as
absent and both attempt to insert it. For this low-volume platform, this
module adds a `write_locks` table with a small, fixed, predefined set of
domain rows (LOCK_DOMAINS below) -- never a caller- or LLM-supplied lock
name. A transaction that must guarantee logical uniqueness for a write
(e.g. "insert this incident_id only if absent") first mutates (touches)
the relevant domain's lock row as its FIRST statement, which forces any
truly concurrent transaction targeting the same domain to conflict and
be cancelled/retried by BigQuery's own transaction manager, rather than
racing past each other on independent MERGE statements that never
contend. If a live-BigQuery spike (Phase 6B, amendment 10) shows this
lock-row approach does not reliably force contention in practice, this
module's guarantee is to be honestly downgraded to "at-least-once
storage with deterministic IDs and reader-side deduplication" -- never
silently described as exactly-once without that having been proven
against real BigQuery.

--- Correctness mechanism correction (pre-6B spike revision) ---
An earlier draft of this module derived per-statement affected-row
counts from a `job.child_statement_results()` hook and used that as its
primary correctness signal. That was never validated against real
BigQuery (no `google.cloud.bigquery.QueryJob` actually exposes such a
method) and, worse, it created an ambiguous state if the adapter failed
AFTER a transaction had already committed: the caller would see an
exception and have no way to know the underlying write had, in fact,
already succeeded.

This module now uses the mechanism BigQuery's own scripting language
provides for exactly this purpose, per Google's documented behavior:
each StatementTemplate is responsible for embedding its own
`ASSERT @@row_count = N AS '...'` immediately after any DML statement
whose row-count it cares about. `@@row_count` is a script-local system
variable reflecting the immediately preceding statement's affected-row
count -- available inside the script itself, with no need to introspect
child jobs after the fact. If an ASSERT fails, BigQuery aborts the
script and rolls back the whole transaction; `client.query(script)
.result()` then raises, and NOTHING has committed -- there is no
ambiguous "maybe it committed" state, because the failure happens
inside the same atomic script that would have committed the write.

execute_transaction() itself no longer inspects job internals at all
beyond calling `.result()`. On success (no exception), it returns
whatever rows the script's FINAL statement produced (typically a
trailing `SELECT` template authors include for exactly this purpose) as
`TransactionResult.result_rows` -- the same "list of plain dicts" shape
shared.data_service.run_query() already returns, for consistency.
Child-job enumeration (`client.list_jobs(parent_job=job)`) remains
available as an optional DIAGNOSTIC tool (see
tools/phase6b_spike/live_transaction_spike.py's step 6) but is never
part of this module's correctness path.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Write-lock domains: a small, fixed, closed set. Never dynamic, never
# derived from caller input.
# ---------------------------------------------------------------------------

LOCK_DOMAINS = frozenset({"incidents", "audit_events", "metric_catalog", "schema_baselines"})


class UnknownLockDomainError(ValueError):
    """Raised when a template or caller references a lock domain outside
    LOCK_DOMAINS. This can only happen from a programming error in this
    codebase's own template definitions -- never from external input,
    since lock domains are never accepted as a parameter value."""


def _require_known_lock_domain(domain: str) -> None:
    if domain not in LOCK_DOMAINS:
        raise UnknownLockDomainError(
            f"{domain!r} is not a recognized write-lock domain. Known "
            f"domains: {sorted(LOCK_DOMAINS)}. Lock domain names must "
            "always be one of this fixed, predefined set -- never a "
            "caller-supplied or dynamically constructed string."
        )


# ---------------------------------------------------------------------------
# Statement templates: a closed, code-owned registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementTemplate:
    """One predefined, application-owned SQL template.

    `sql` is a BigQuery script fragment (DML + any `ASSERT`s) that runs
    INSIDE the `BEGIN TRANSACTION ... COMMIT TRANSACTION` block, with
    named `@param` placeholders bound the same way
    shared.data_service.run_query() binds them: as real BigQuery query
    parameters, never string interpolation. `lock_domain`, if set, names
    the LOCK_DOMAINS entry this template's transaction must touch first.

    Template authors are responsible for embedding their own
    `ASSERT @@row_count = N AS '...'` immediately after any DML statement
    whose affected-row count matters for correctness (e.g. "exactly one
    incident row updated"). execute_transaction() does not add these
    automatically and does not otherwise inspect per-statement row
    counts -- the ASSERT is the correctness mechanism (see this module's
    docstring, "Correctness mechanism correction").

    `result_sql`, if set, is a SELECT statement rendered AFTER
    `COMMIT TRANSACTION` (not before it) -- confirmed necessary by the
    Phase 6B live spike, whose step 9 initially returned an empty
    TransactionResult.result_rows despite the transaction committing
    successfully: a SELECT embedded inside `sql` (i.e. before COMMIT)
    is not the script's final statement once `_render_script()` appends
    `COMMIT TRANSACTION;` after it, so `job.result()` returns the
    COMMIT statement's (empty) result, not the SELECT's rows. Only ONE
    statement across an entire execute_transaction() call may declare
    `result_sql` -- `_render_script()` raises if a second one is found.
    `result_sql` participates in the same per-statement `@param`
    namespacing as `sql`.
    """

    name: str
    sql: str
    lock_domain: Optional[str] = None
    result_sql: Optional[str] = None

    def __post_init__(self) -> None:
        if self.lock_domain is not None:
            _require_known_lock_domain(self.lock_domain)


_TEMPLATES: dict[str, StatementTemplate] = {}


class UnregisteredTemplateError(ValueError):
    """Raised when execute_transaction() is asked to run a template name
    that was never registered via register_template(). There is no
    fallback: an unregistered name is refused outright, exactly like
    shared.data_service.UnsafeQueryError refuses non-read-only SQL."""


def register_template(template: StatementTemplate) -> None:
    """Register a predefined statement template, once, at import time.

    This is the ONLY way a name becomes callable via execute_transaction().
    Real business templates are registered by shared/data_service.py,
    shared/metric_catalog.py, and shared/schema_baselines.py in Phase
    6B/6C, immediately below their module-level imports -- never inside a
    request-handling function, and never with a name or SQL text derived
    from a caller argument.
    """

    if template.name in _TEMPLATES and _TEMPLATES[template.name] is not template:
        raise ValueError(
            f"A different StatementTemplate is already registered under "
            f"{template.name!r}. Template names must be registered exactly "
            "once each."
        )
    _TEMPLATES[template.name] = template


def _resolve_template(name: str) -> StatementTemplate:
    try:
        return _TEMPLATES[name]
    except KeyError:
        raise UnregisteredTemplateError(
            f"{name!r} is not a registered transaction template. "
            "execute_transaction() only runs predefined, application-owned "
            "templates registered via register_template() -- it never "
            "accepts or executes arbitrary SQL."
        ) from None


@dataclass(frozen=True)
class BoundStatement:
    """One call to a registered template with bound parameter values.

    `params` values flow into BigQuery's named ScalarQueryParameter/
    ArrayQueryParameter binding, exactly like shared.data_service.
    run_query() -- never concatenated into the template text.
    """

    template_name: str
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransactionAbortedError(RuntimeError):
    """Raised internally when a transaction attempt fails in a way this
    module classifies as retryable (BigQuery cancelled it due to a
    genuine conflict, a transient timeout, etc.). Callers of
    execute_transaction() should never see this directly -- it is caught
    and retried internally, and only surfaces as ConcurrentModificationError
    once the retry budget is exhausted."""


class ConcurrentModificationError(RuntimeError):
    """Raised by execute_transaction() after `max_retries` retryable
    failures in a row. This is the final, caller-visible signal that a
    transaction could not be committed due to sustained contention -- the
    caller should re-fetch whatever it read before building this
    transaction's parameters and decide whether to try again."""


class PayloadConflictError(RuntimeError):
    """Raised when a deterministic, caller-generated ID was reused with a
    payload that does not match what is already persisted under that ID.
    Per amendment 2's idempotency contract:

      - same ID, identical payload -> not an error (idempotent success)
      - same ID, different payload -> this error
      - two concurrent writers, same ID -> BigQuery's own transaction
        contention (plus, where configured, this module's lock-domain
        mechanism) ensures at most one insert lands; the other party's
        attempt resolves via the same two rules above once it re-reads.

    The message never includes the conflicting payload's actual values --
    only the ID and which fields differed, so this never becomes a
    secondary channel for leaking sensitive content.
    """


class TransactionTemplateError(RuntimeError):
    """Reserved for a template's own internal ASSERT invariant failing --
    i.e. the script ran, but a condition this codebase itself expected to
    hold did not (e.g. "expected exactly one row" but found zero or two).
    This is distinct from TransactionAbortedError (an external
    contention/transient failure): an ASSERT failure means something is
    actually wrong, not merely "try again," and must never be retried.

    execute_transaction() does not currently wrap ASSERT failures into
    this type automatically -- the real exception type/module BigQuery's
    client library raises for a failed ASSERT inside a script is
    confirmed by the Phase 6B live transaction spike
    (tools/phase6b_spike/live_transaction_spike.py, step 2). Once
    confirmed, `_is_retryable()` below is the place that classification
    is recorded, and this class becomes the wrapper callers can catch for
    "a template's own invariant failed" specifically. Until then, an
    ASSERT failure simply propagates as whatever BigQuery's client
    raises, unclassified but never silently retried (unclassified
    exceptions are treated as non-retryable by `_is_retryable()`'s
    conservative default)."""


# ---------------------------------------------------------------------------
# Error classification (Phase 6, amendment 1)
# ---------------------------------------------------------------------------
#
# The exact set of exception types/messages BigQuery raises for a
# cancelled/aborted multi-statement transaction is one of the concrete
# things the Phase 6B live transaction spike (amendment 10) must verify.
# This function is intentionally conservative and centralizes that
# judgment call in one place so the spike's findings only require editing
# this one function, not every call site.


_UNCONDITIONALLY_RETRYABLE_TYPE_NAMES = frozenset(
    {"Aborted", "DeadlineExceeded", "ServiceUnavailable", "TransactionAbortedError"}
)

# BigQuery's REST client surfaces a genuine SQL/ASSERT failure, an
# unrelated syntax bug, AND a real concurrent-transaction conflict as the
# SAME exception type (google.api_core.exceptions.BadRequest, HTTP 400)
# with the SAME structured reason code ("invalidQuery") -- confirmed
# against real BigQuery by TWO Phase 6B live spike runs. The first run
# (2026-07-12, run_id=c0536479d3) only showed this at the type-name
# level. The second run (2026-07-12, run_id=ad466ad893), after adding
# bigquery_error_diagnostics(), confirmed it precisely: the forced-ASSERT
# failure (step 2) and the genuine concurrent lock-row conflicts (step
# 7/8) BOTH surfaced as BadRequest / HTTP 400 / reason "invalidQuery".
# Neither exception type nor reason code alone can distinguish a genuine,
# permanent SQL/ASSERT defect from a transient, retryable concurrent-
# transaction conflict -- classifying on either alone would either retry
# permanent bugs forever (masking them as "transient contention") or
# never retry a real conflict.
#
# The one signal that DOES distinguish them is BigQuery's own documented
# message text for the concurrent-conflict condition specifically:
# "Transaction is aborted due to concurrent update against table ..."
# (confirmed present only on the genuine-conflict failures in the second
# spike run, never on the ASSERT-failure or syntax-error cases). This
# module inspects that text ONLY to compute a boolean
# (`concurrent_update_signature_matched`, see bigquery_error_diagnostics()
# below) -- the raw message itself is never returned, logged, stored, or
# otherwise propagated past that one boolean computation.
_CONCURRENT_UPDATE_SIGNATURE = "transaction is aborted due to concurrent update against table"


def _matches_concurrent_update_signature(exc: Exception) -> bool:
    """Inspects `exc`'s raw message ONLY to compute this one boolean --
    the message itself never leaves this function. Confirmed against
    real BigQuery (Phase 6B spike, 2026-07-12 run, run_id=ad466ad893) to
    be present on a genuine concurrent-transaction conflict and absent
    on an ordinary SQL/ASSERT `invalidQuery` failure, even though both
    share the same exception type and reason code."""

    message = getattr(exc, "message", None)
    if message is None:
        message = str(exc)
    return _CONCURRENT_UPDATE_SIGNATURE in message.lower()


def bigquery_error_diagnostics(exc: Exception) -> dict[str, Any]:
    """Extract a small, sanitized, structured summary of a BigQuery
    client-library exception -- safe to log, print, or persist in full.

    Returns exactly: exception class name, exception module, the list of
    structured `reason` codes BigQuery attached (from `exc.errors`, if
    present -- short fixed identifiers like "invalidQuery", never free
    text), the HTTP status code (from `exc.code`, if present), and
    `concurrent_update_signature_matched` -- a boolean computed by
    inspecting the raw message internally (see
    _matches_concurrent_update_signature()) without ever including that
    message in this return value.

    Deliberately NEVER includes `exc.message`/`str(exc)`/anything else
    that could embed a table name, query fragment, or other identifier
    from the failed script. This is the one function in this module
    responsible for drawing that line -- every caller (this module's own
    `_is_retryable()`, the Phase 6B spike's diagnostics) must go through
    it rather than reaching into a raw exception itself.
    """

    errors = getattr(exc, "errors", None) or []
    reason_codes = sorted(
        {entry.get("reason") for entry in errors if isinstance(entry, dict) and entry.get("reason")}
    )
    return {
        "exception_type": type(exc).__name__,
        "exception_module": type(exc).__module__,
        "reason_codes": reason_codes,
        "http_status": getattr(exc, "code", None),
        "concurrent_update_signature_matched": _matches_concurrent_update_signature(exc),
    }


def _is_retryable(exc: Exception) -> bool:
    """Return True if `exc` represents a transient/contention failure
    that should be retried, rather than a genuine, permanent error.

    For BadRequest specifically (the type both a genuine SQL/ASSERT
    failure and a real concurrent-transaction conflict share against
    real BigQuery), ALL THREE of the following must hold, per the second
    Phase 6B live spike run's confirmed evidence:
      - HTTP status is 400 (BadRequest's own status; checked explicitly
        rather than assumed from the type name alone);
      - the structured reason includes "invalidQuery" (necessary but,
        confirmed by that same run, NOT sufficient on its own -- a
        syntax error and a failed ASSERT share this exact reason);
      - `concurrent_update_signature_matched` is True (the one
        discriminator that IS sufficient, per
        _matches_concurrent_update_signature()'s docstring).

    Never inspects or logs the exception's full message content when
    classifying -- only its type name and the sanitized, structured
    fields from bigquery_error_diagnostics() -- so a raw exception string
    (which could embed internal identifiers) is never propagated further
    than necessary to make this yes/no decision.
    """

    type_name = type(exc).__name__
    if type_name in _UNCONDITIONALLY_RETRYABLE_TYPE_NAMES:
        return True
    if type_name == "BadRequest":
        diagnostics = bigquery_error_diagnostics(exc)
        return (
            diagnostics["http_status"] == 400
            and "invalidQuery" in diagnostics["reason_codes"]
            and diagnostics["concurrent_update_signature_matched"]
        )
    return False


def _safe_log_retry(*, template_names: list[str], attempt: int, exc: Exception) -> None:
    """Log a retry event without ever including the raw exception message
    (which could embed identifiers or, in principle, parameter values) --
    only the exception's class name, the attempt number, and which
    templates were involved. Uses the standard logging module rather than
    print() so log level/destination stay controlled by the app, not this
    library module."""

    import logging

    logging.getLogger(__name__).warning(
        "Transaction attempt %d failed for templates %s; classified as "
        "retryable (%s). Retrying with backoff.",
        attempt,
        template_names,
        type(exc).__name__,
    )


# ---------------------------------------------------------------------------
# Retry/backoff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 4
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff with full jitter: a random delay between 0
        and min(max_delay, base * 2**attempt). Full jitter (rather than a
        fixed or additive jitter) is the pattern AWS's and Google's own
        retry guidance recommend to avoid synchronized retry storms
        across multiple concurrent callers hitting the same contention."""

        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * (2**attempt))
        return random.uniform(0, ceiling)


DEFAULT_RETRY_POLICY = RetryPolicy()


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class TransactionalClientLike(Protocol):
    """Structural type for anything execute_transaction() can run
    against. `.query(sql, job_config=...).result()` is the same surface
    shared.data_service.BigQueryClientLike already requires -- a
    multi-statement transaction script is submitted through the exact
    same Client.query() API a single SELECT is, per BigQuery's actual
    scripting model.

    Deliberately narrow: execute_transaction() needs nothing beyond
    `.query().result()`. Per-statement correctness lives inside the
    script itself (ASSERT @@row_count, see StatementTemplate's
    docstring), not in anything this module reads off the job object
    afterward -- so no `child_statement_results()`-style method is
    required here (a prior draft required one; real BigQuery Job objects
    never provided it, which is exactly the ambiguous-adapter-failure
    problem this correction removes).
    """

    def query(self, sql: str, job_config: Any = None) -> Any: ...


# ---------------------------------------------------------------------------
# Script rendering
# ---------------------------------------------------------------------------


def _render_script(statements: list[BoundStatement]) -> tuple[str, dict[str, Any]]:
    """Render a list of BoundStatement values into one BEGIN TRANSACTION
    ... COMMIT; script, plus the merged, namespaced parameter dict.

    Parameters are namespaced per-statement-index (e.g. `s0_incident_id`)
    so that two statements in the same script can each safely use a
    parameter named e.g. `incident_id` without colliding.

    Any one statement's `template.result_sql` (see StatementTemplate's
    docstring) is rendered AFTER `COMMIT TRANSACTION;`, not inline with
    the rest of that statement's `sql` -- this is the corrected ordering
    confirmed by the Phase 6B live spike (a SELECT placed before COMMIT
    is not the script's final statement, so job.result() would not
    return its rows). At most one statement may declare `result_sql`;
    a second one raises ValueError, since only one script-level result
    set is meaningful.
    """

    resolved = [_resolve_template(s.template_name) for s in statements]
    lines = ["BEGIN", "BEGIN TRANSACTION;"]
    merged_params: dict[str, Any] = {}
    trailing_result_sql: Optional[str] = None
    trailing_result_template_name: Optional[str] = None
    for index, (bound, template) in enumerate(zip(statements, resolved)):
        sql_fragment = template.sql
        result_fragment = template.result_sql
        for param_name, value in bound.params.items():
            namespaced = f"s{index}_{param_name}"
            sql_fragment = sql_fragment.replace(f"@{param_name}", f"@{namespaced}")
            if result_fragment is not None:
                result_fragment = result_fragment.replace(f"@{param_name}", f"@{namespaced}")
            merged_params[namespaced] = value
        lines.append(sql_fragment.rstrip().rstrip(";") + ";")
        if result_fragment is not None:
            if trailing_result_sql is not None:
                raise ValueError(
                    "At most one statement in a single execute_transaction() "
                    "call may declare result_sql (the trailing SELECT run "
                    f"after COMMIT); {trailing_result_template_name!r} already "
                    f"declared one, and {template.name!r} declares a second."
                )
            trailing_result_sql = result_fragment.rstrip().rstrip(";") + ";"
            trailing_result_template_name = template.name
    lines.append("COMMIT TRANSACTION;")
    if trailing_result_sql is not None:
        lines.append(trailing_result_sql)
    lines.append("END;")
    return "\n".join(lines), merged_params


def _build_job_config(params: dict[str, Any]) -> Any:
    # Reuses the exact same scalar/array parameter binding rules as
    # shared.data_service._build_job_config, duplicated narrowly here
    # rather than imported, since importing run_query()'s private helper
    # would couple this module's parameter-binding behavior to
    # data_service.py's private implementation details. Both must bind
    # named parameters, never string-interpolate -- that invariant is
    # what matters, not sharing the exact function.
    from google.cloud import bigquery

    query_params = []
    timestamp_parameter_names = {
        "acknowledged_at",
        "created_at",
        "event_timestamp",
        "resolved_at",
        "reviewed_at",
        "timestamp",
    }
    float_parameter_names = {"observed_value", "expected_value"}
    integer_parameter_names = {"column_count", "row_version_before"}
    for name, value in params.items():
        if isinstance(value, (list, tuple)):
            if not value:
                # Phase 6B correction: an empty list is a legitimate value
                # for an ARRAY<STRING> business column (e.g. an incident
                # with no affected_metrics yet) -- rejecting it outright
                # made every such write impossible. BigQuery's
                # ArrayQueryParameter accepts an empty `values` list given
                # an explicit element type; every current caller's
                # empty-array fields are ARRAY<STRING> columns
                # (Incident.affected_metrics/affected_dashboards), so
                # STRING is used as the default element type here. If a
                # future template needs an empty array of a different
                # element type, this default will need to become
                # caller-specified rather than assumed.
                query_params.append(bigquery.ArrayQueryParameter(name, "STRING", []))
                continue
            first = value[0]
            array_type = "STRING"
            if isinstance(first, bool):
                array_type = "BOOL"
            elif isinstance(first, int):
                array_type = "INT64"
            elif isinstance(first, float):
                array_type = "FLOAT64"
            query_params.append(bigquery.ArrayQueryParameter(name, array_type, list(value)))
        else:
            scalar_type = "STRING"
            # Transaction parameters are namespaced while rendering (for
            # example ``s0_created_at``). Persistence APIs intentionally use
            # ISO-8601 strings at their boundary, so bind the known temporal
            # fields as TIMESTAMP instead of letting BigQuery see STRING.
            # This also types optional NULL timestamps correctly.
            base_name = name.split("_", 1)[1] if name.startswith("s") and name.split("_", 1)[0][1:].isdigit() else name
            if base_name in timestamp_parameter_names:
                scalar_type = "TIMESTAMP"
            elif base_name in float_parameter_names:
                scalar_type = "FLOAT64"
            elif base_name in integer_parameter_names:
                scalar_type = "INT64"
            elif isinstance(value, bool):
                scalar_type = "BOOL"
            elif isinstance(value, int):
                scalar_type = "INT64"
            elif isinstance(value, float):
                scalar_type = "FLOAT64"
            query_params.append(bigquery.ScalarQueryParameter(name, scalar_type, value))
    return bigquery.QueryJobConfig(query_parameters=query_params)


# ---------------------------------------------------------------------------
# execute_transaction()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionResult:
    """The outcome of a successfully committed transaction.

    `result_rows` is whatever the script's FINAL statement produced,
    as plain dicts (the same shape shared.data_service.run_query()
    returns) -- typically a trailing SELECT a template author included
    specifically to hand structured data back to the caller. If the
    final statement was DML with no SELECT, this is an empty list, which
    is a normal, expected outcome, not an error: per-statement
    correctness was already enforced by the script's own ASSERT
    statements (StatementTemplate's docstring) before this ever returns.

    `attempts` is how many tries it took to commit (1 if it succeeded on
    the first try, more if retryable contention caused internal retries).
    """

    result_rows: list[dict]
    attempts: int


def execute_transaction(
    client: "TransactionalClientLike",
    statements: list[BoundStatement],
    *,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> TransactionResult:
    """Execute a list of predefined, bound statements as one atomic
    multi-statement BigQuery transaction: all commit together, or none do.

    Every `statements[i].template_name` must already be registered via
    register_template() -- an unregistered name raises
    UnregisteredTemplateError immediately, before any BigQuery call is
    made. This is the sole enforcement point that keeps this API from
    ever running caller- or LLM-supplied SQL, mirroring
    shared.data_service.run_query()'s read-only enforcement for reads.

    Retries a bounded number of times (per `retry_policy`) with
    exponential backoff and full jitter when a failure is classified as
    retryable (`_is_retryable`) -- i.e. BigQuery cancelled the transaction
    due to a genuine conflict with another concurrent transaction, per
    amendment 1's corrected understanding of BigQuery's real concurrency
    behavior. After the retry budget is exhausted, raises
    ConcurrentModificationError; the caller is expected to re-fetch
    whatever state it read before constructing these statements and
    decide whether to retry the whole operation from scratch (a full
    restart from a fresh read, not merely resubmitting the same stale
    parameters).

    Non-retryable failures (a template's own ASSERT invariant failing, an
    unregistered template, a malformed parameter) propagate immediately,
    without retrying -- retrying a genuinely broken transaction would
    just waste the retry budget on a failure that will never succeed.
    """

    # Fail fast on any unregistered template, before touching BigQuery at
    # all -- resolves every template up front rather than one at a time
    # mid-script, so a caller never partially executes a script it wasn't
    # allowed to run.
    for statement in statements:
        _resolve_template(statement.template_name)

    template_names = [s.template_name for s in statements]
    last_exc: Optional[Exception] = None

    for attempt in range(retry_policy.max_retries + 1):
        script, params = _render_script(statements)
        job_config = _build_job_config(params)
        try:
            job = client.query(script, job_config=job_config)
            rows = list(job.result())
        except Exception as exc:  # noqa: BLE001 -- classified immediately below
            if _is_retryable(exc) and attempt < retry_policy.max_retries:
                _safe_log_retry(template_names=template_names, attempt=attempt, exc=exc)
                sleep_fn(retry_policy.delay_for_attempt(attempt))
                last_exc = exc
                continue
            if _is_retryable(exc):
                raise ConcurrentModificationError(
                    f"Transaction for templates {template_names} did not "
                    f"commit after {retry_policy.max_retries + 1} attempts "
                    "due to sustained contention. Re-fetch the underlying "
                    "state and retry from scratch."
                ) from None
            # Not retryable: propagate as-is (e.g. a template's ASSERT
            # invariant failure, or any other genuine error). Never
            # re-wrapped in a way that would obscure what actually failed
            # in application logs -- only the audit-log-facing surfaces
            # (shared/audit.py) apply the "no raw exception text" rule to
            # what gets PERSISTED; this is an in-process exception, not a
            # persisted record.
            raise

        return TransactionResult(
            result_rows=[dict(row) for row in rows],
            attempts=attempt + 1,
        )

    # Unreachable in practice (the loop above always returns or raises),
    # but keeps type checkers and reviewers honest about there being no
    # silent fallthrough.
    raise ConcurrentModificationError(
        f"Transaction for templates {template_names} exhausted its retry "
        f"budget without a definitive outcome (last error: "
        f"{type(last_exc).__name__ if last_exc else 'none'})."
    )
