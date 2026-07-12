"""Deterministic SQL review engine.

Owns exactly one responsibility: scoring a piece of SQL against an
approved-table catalog and a small set of governance lint rules, with no
Streamlit, no LLM, and no BigQuery access. Per docs/metric-governance.md's
SQL review contract, review_sql() must always return referenced_tables,
findings, and a deterministic score -- explanations.py may narrate this
result afterward, but never changes it.

Migrated from the original Metric Governance Copilot's src/review.py
essentially unchanged: the rule set (SELECT *, unapproved tables, missing
grain, unsafe joins, missing filters) was already sound and already used
sqlglot the same way shared/data_service.py does for its own read-only
enforcement.
"""

from __future__ import annotations

from functools import lru_cache

import sqlglot
from sqlglot import exp

from apps.metric_governance.models import SqlReviewFinding, SqlReviewResult


def _severity_weight(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(severity, 1)


@lru_cache(maxsize=256)
def _review_sql_cached(sql: str, approved_tables_key: tuple[str, ...]) -> SqlReviewResult:
    findings: list[SqlReviewFinding] = []
    referenced_tables: list[str] = []
    approved_tables = list(approved_tables_key)

    if not sql.strip():
        return SqlReviewResult(
            score=0,
            summary="No SQL provided.",
            findings=[SqlReviewFinding("medium", "Input", "Paste a query before running review.")],
            referenced_tables=[],
            recommended_next_steps=["Paste SQL", "Run again"],
        )

    try:
        parsed = sqlglot.parse_one(sql, read="bigquery")
        referenced_tables = sorted({table.name for table in parsed.find_all(exp.Table)})
    except Exception:
        return SqlReviewResult(
            score=25,
            summary="SQL could not be parsed.",
            findings=[SqlReviewFinding("critical", "Syntax", "The query is not valid BigQuery SQL.")],
            referenced_tables=[],
            recommended_next_steps=["Check syntax", "Confirm table names", "Run again"],
        )

    if not referenced_tables:
        findings.append(SqlReviewFinding("medium", "Lineage", "No table references were detected."))

    unapproved = [table for table in referenced_tables if table not in approved_tables]
    if unapproved:
        findings.append(
            SqlReviewFinding(
                "high",
                "Approved Tables",
                f"Referenced tables are not in the approved catalog: {', '.join(unapproved)}.",
            )
        )

    lower_sql = sql.lower()
    if "select *" in lower_sql:
        findings.append(SqlReviewFinding("medium", "Projection", "Avoid SELECT * in governed metric SQL."))
    if "join" in lower_sql and "on" not in lower_sql:
        findings.append(SqlReviewFinding("critical", "Join Logic", "Join clauses should include explicit ON conditions."))
    if "count(" in lower_sql and "group by" not in lower_sql and "distinct" not in lower_sql:
        findings.append(SqlReviewFinding("medium", "Grain", "Aggregate logic should make the grain explicit."))
    if "where" not in lower_sql:
        findings.append(SqlReviewFinding("medium", "Filters", "Add business filters and freshness filters where required."))

    if not findings:
        findings.append(SqlReviewFinding("low", "Governance", "The query looks aligned with the approved metric catalog."))

    raw_score = 100 - sum(_severity_weight(f.severity) * 8 for f in findings)
    score = max(min(raw_score, 100), 0)

    summary = "Query looks strong." if score >= 80 else "Query needs governance review."
    if score < 60:
        summary = "Query has several governance risks."

    recommended_next_steps = [
        "Confirm the grain of the result set",
        "Compare the SQL against the certified metric definition",
        "Check whether the selected tables are the approved source of truth",
    ]

    return SqlReviewResult(
        score=score,
        summary=summary,
        findings=findings,
        referenced_tables=referenced_tables,
        recommended_next_steps=recommended_next_steps,
    )


def review_sql(sql: str, approved_tables: list[str]) -> SqlReviewResult:
    """Review `sql` against `approved_tables`.

    `approved_tables` is caller-supplied on purpose: this module has no
    opinion on where the approved list comes from. In the running app,
    main.py derives it from shared.metric_catalog.list_definitions()'s
    approved_source_tables; tests pass in whatever fixed list they need.
    """

    return _review_sql_cached(sql.strip(), tuple(sorted(set(approved_tables))))
