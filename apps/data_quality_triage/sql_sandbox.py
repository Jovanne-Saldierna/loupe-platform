"""Deterministic, read-only SQL sandbox for the Triage debugging playbook.

Lets a user run one of the playbook's suggested SQL checks (or a lightly
edited variant) against BigQuery and see results inside the app, without
this becoming a general-purpose SQL editor. Every safety decision here is
deterministic -- no AI call ever decides whether SQL is safe to run (per
the task's explicit "Do not use AI to decide whether SQL is safe" rule).

Layered, deliberately redundant defenses:
  1. Reject multiple statements / semicolon chains via a plain-text scan,
     before anything is parsed -- catches "SELECT 1; DROP TABLE t;" even
     before a parser sees it. At most one trailing semicolon is tolerated
     and stripped (normal end-of-statement punctuation); any semicolon
     found elsewhere means a second statement was stacked, and is refused.
  2. Reject any obvious write/DDL keyword via a word-boundary regex scan
     (INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/ALTER/TRUNCATE/EXECUTE/CALL/
     EXPORT/LOAD/GRANT/REVOKE) -- a coarse, intentionally strict filter
     that can in rare cases false-positive on a string literal containing
     one of these words (e.g. a WHERE clause filtering on the literal
     string 'insert'); that tradeoff is acceptable for a debugging
     sandbox and is not the sole line of defense.
  3. Parse the remaining single statement with sqlglot (BigQuery dialect)
     and require it to be a SELECT (WITH ... SELECT parses as a
     sqlglot.exp.Select with an attached CTE, so "WITH" queries are
     naturally included) / UNION / INTERSECT / EXCEPT -- the same
     statement-type check shared/data_service.py's run_query() already
     enforces on every query path in this platform, reimplemented here
     directly (rather than importing that module's private helper) so
     this module has no BigQuery-client dependency and stays trivially
     unit-testable.
  4. Wrap the validated statement in an outer
     `SELECT * FROM (<validated statement>) AS triage_sandbox_query
      LIMIT <max_rows>` -- unconditionally, whether or not the user's own
     SQL already had a LIMIT -- so the query BigQuery actually executes is
     always row-bounded, not just the rows handed back to the frontend.
  5. run_query() (shared/data_service.py), which actually executes the
     wrapped SQL, re-validates read-only-ness itself and applies a
     bytes-billed ceiling -- this module's checks are not the only gate.

Nothing accepted by this module can mutate warehouse data: every accepted
statement is, by construction, a single SELECT/WITH read wrapped in
another SELECT.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

MAX_ROWS = 25

_READ_ONLY_STATEMENT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)

_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "merge", "create", "drop", "alter",
    "truncate", "execute", "exec", "call", "export", "load", "grant", "revoke",
)


class UnsafeSandboxQueryError(ValueError):
    """Raised when submitted SQL fails the sandbox's deterministic,
    non-AI safety checks -- see this module's docstring for the full,
    layered rule set."""


def _reject_multiple_statements(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        raise UnsafeSandboxQueryError("No SQL was submitted.")
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise UnsafeSandboxQueryError(
            "Multiple SQL statements are not allowed in the debugging sandbox. "
            "Submit exactly one SELECT/WITH statement."
        )
    return stripped


def _reject_forbidden_keywords(sql: str) -> None:
    lowered = sql.lower()
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            raise UnsafeSandboxQueryError(
                f"The debugging sandbox is read-only: '{keyword.upper()}' is not allowed here. "
                "Only SELECT and WITH queries can be run."
            )


def _ensure_single_read_statement(sql: str) -> None:
    try:
        parsed = sqlglot.parse_one(sql, read="bigquery")
    except Exception as exc:
        raise UnsafeSandboxQueryError(
            f"Could not parse this SQL, so it was refused rather than assumed safe. Parse error: {exc}"
        ) from exc

    if parsed is None or not isinstance(parsed, _READ_ONLY_STATEMENT_TYPES):
        statement_type = type(parsed).__name__ if parsed is not None else "None"
        raise UnsafeSandboxQueryError(
            "Only a single read-only SELECT / WITH ... SELECT / UNION / INTERSECT / EXCEPT "
            f"statement is allowed; the parsed statement type was {statement_type!r}."
        )


def validate_and_wrap(sql: str, *, max_rows: int = MAX_ROWS) -> str:
    """Run every deterministic safety check on `sql` and, if it passes,
    return a wrapped, row-bounded query ready to execute. Raises
    UnsafeSandboxQueryError (never returns a partially-checked query) on
    any violation."""

    single_statement = _reject_multiple_statements(sql)
    _reject_forbidden_keywords(single_statement)
    _ensure_single_read_statement(single_statement)
    return f"SELECT * FROM (\n{single_statement}\n) AS triage_sandbox_query\nLIMIT {max_rows}"
