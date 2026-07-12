"""Honest source-health reporting for Loupe responses.

Per the Phase 5 correction review: "Until Phase 6 persistence exists,
Loupe must not silently imply that sources are healthy. Use an explicit
unknown/unavailable source-health state, award no trust benefit, and
display a warning. Once persistence is connected, derive health through
shared.data_service for every table used by the result."

This module is forward-compatible by construction, not by a later
rewrite: it always calls shared.data_service.derive_source_health()
first, exactly as Phase 6 will expect it to. Today, that call fails for
every table (the `loupe_platform.incidents` table it queries does not
exist yet -- see shared/data_service.py's module docstring, "no tables
have been created in it yet"), so every table's status is honestly
reported as "unknown" via the except branch below. Once Phase 6 creates
that table, the same code path starts returning real "healthy"/
"degraded"/"critical" statuses with no changes required here.

"unknown" is deliberately NOT one of shared.models.SourceHealthStatus's
three values ("healthy"/"degraded"/"critical") -- that Literal is a
cross-app contract shared with Governance and Triage, validated in
shared/models.py's __post_init__, and Phase 5 does not modify shared/.
Rather than force an invalid fourth value through that validated
dataclass, this module reports health as a plain, app-local dict (never a
shared.models.SourceHealth instance) whenever the real status cannot be
determined. That keeps this honesty requirement local to Loupe until
Phase 6 makes it real, without loosening a contract the other two apps
already rely on.
"""

from __future__ import annotations

from apps.loupe_agent.metrics import QUALIFIED_DATASET

# Which base tables each agent-facing/dashboard query result actually
# depends on -- used to report health for exactly the tables a given
# response's numbers came from, never a blanket "the whole dataset."
TABLE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "category_metrics": ("order_items", "products"),
    "company_benchmark": ("order_items", "products"),
    "multi_category_comparison": ("order_items", "products"),
    "state_metrics": ("order_items", "products", "users"),
    "multi_state_comparison": ("order_items", "products", "users"),
    "returns_leakage": ("order_items", "products"),
    "channel_mix_trend": ("order_items", "users"),
    "lever_price_position": ("order_items", "products"),
    "dashboard_kpis": ("order_items", "products", "users"),
    "revenue_trend": ("order_items", "products", "users"),
    "category_leaderboard": ("order_items", "products", "users"),
    "state_breakdown": ("order_items", "products", "users"),
    "channel_mix_range": ("order_items", "products", "users"),
}

# Ordered worst-to-best so summarize() can pick the single worst status
# present without guessing at a numeric severity scale of its own.
_SEVERITY_ORDER = ("critical", "unknown", "degraded", "healthy")


def table_health(client, table_id: str) -> dict:
    """Report health for one table: {"table_id", "status", "known"}.

    Always attempts shared.data_service.derive_source_health() first (see
    module docstring for why); any exception -- including the expected
    "incidents table does not exist yet" case -- is reported as an
    explicit status="unknown", known=False result, never silently
    upgraded to "healthy".
    """

    try:
        from shared.data_service import derive_source_health

        health = derive_source_health(client, QUALIFIED_DATASET, table_id)
        return {"table_id": table_id, "status": health.status, "known": True}
    except Exception:
        return {"table_id": table_id, "status": "unknown", "known": False}


def get_source_health(client, tables: tuple[str, ...]) -> list[dict]:
    """table_health() for every table in `tables`, in order."""

    return [table_health(client, table_id) for table_id in tables]


def summarize(health_rows: list[dict]) -> dict:
    """Reduce a list of table_health() results to one overall status plus
    a human-readable warning, or warning=None only when every table is
    known and healthy.

    No trust benefit is ever awarded for "unknown": it is treated as
    warning-worthy exactly like "degraded"/"critical", never as a neutral
    or passing state, per the explicit "award no trust benefit" direction.
    """

    if not health_rows:
        return {"status": "unknown", "warning": "No source tables were checked for this response.", "tables": []}

    worst = min(
        health_rows,
        key=lambda row: _SEVERITY_ORDER.index(row["status"]) if row["status"] in _SEVERITY_ORDER else 0,
    )
    status = worst["status"]

    if status == "healthy" and all(row["known"] for row in health_rows):
        return {"status": "healthy", "warning": None, "tables": health_rows}

    if status == "critical":
        warning = (
            f"Source health is CRITICAL for {worst['table_id']} (active high-severity incident). "
            "Treat this response's numbers as unreliable until the incident is resolved."
        )
    elif status == "degraded":
        warning = f"Source health is DEGRADED for {worst['table_id']} (active incident). Review before relying on this response."
    else:  # "unknown" -- includes any table whose status could not be verified
        unknown_tables = [row["table_id"] for row in health_rows if not row["known"]]
        warning = (
            "Source health could not be verified for "
            f"{', '.join(unknown_tables) if unknown_tables else 'one or more tables'} "
            "(incident persistence is not yet connected -- Phase 6). This response's numbers are real, "
            "live query results, but their source-table health is unconfirmed."
        )

    return {"status": status, "warning": warning, "tables": health_rows}


def health_for(client, dependency_key: str) -> dict:
    """Convenience: look up TABLE_DEPENDENCIES[dependency_key], fetch
    health for each, and summarize. Raises KeyError for an unregistered
    dependency_key -- callers must use one of the fixed keys above, never
    a caller-supplied table name (which would defeat the point of a fixed
    dependency map)."""

    tables = TABLE_DEPENDENCIES[dependency_key]
    return summarize(get_source_health(client, tables))
