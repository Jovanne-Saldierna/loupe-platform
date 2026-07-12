"""Deterministic metric queries for the Loupe E-Commerce Agent.

Ported from the original ecommerce-analytics-agent/main.py (read-only
reference; that repository is not modified), with these behavioral changes:

1. No bigquery.Client is ever constructed here. Every query goes through
   shared.data_service.run_query(), which enforces read-only, parameterized,
   byte-limited, timed-out execution -- see docs/architecture.md's "one
   gateway" rule. get_bq_client() and the google.cloud.bigquery import are
   not migrated.
2. Every function that used to return a pre-formatted string now returns a
   plain dict/list[dict] of real values -- string formatting for chat
   narration or Streamlit display happens in chat.py/ui.py, not here. This
   keeps this module's output directly assertable in tests without string
   parsing, and lets ui.py/chat.py format the same underlying numbers
   differently for their respective audiences.
3. Every function that computes a certified metric (revenue, margin,
   return_rate, margin_leakage, channel_mix) returns which
   shared.metric_catalog definition and version it used, per
   docs/loupe-agent.md: "Every revenue result must state whether it is
   gross, net, delivered-only, or another certified version." These
   FORMULAS were verified against shared/metric_catalog.py's five
   pending_validation entries during the Phase 5 mapping review: no
   disagreement was found, so this module reads the catalog rather than
   redefining the formulas a second time.

4. Measurement grain vs. reporting grain (resolved during the Phase 5
   grain-mismatch correction; see docs/contracts.md's "Measurement grain
   vs. reporting grain" section and shared/models.py's MetricDefinition
   docstring for the full rationale). These are two different concepts
   that an earlier version of this module's docstring conflated:

   - shared.metric_catalog's `measurement_grain` field states the atomic
     business entity a metric is DEFINED over (e.g. revenue/margin/
     return_rate/margin_leakage/channel_mix are all order_item-grain).
     That never changes based on how a query happens to group its output.
   - Each function below declares its own REPORTING grain in its own
     docstring -- the dimensional/temporal shape that PARTICULAR query
     returns (one row per month, one row per category, one row per state,
     one aggregate row for the whole window, ...).

   It is normal and correct for many different reporting grains to exist
   for the same order_item-grain metric: get_category_metrics() returns
   one row per category (no date breakdown), get_revenue_trend() returns
   one row per month, get_dashboard_kpis() returns one aggregate row for
   the whole filtered window -- all three are legitimate, simultaneously
   valid ways to report revenue/margin/return_rate, not competing or
   disagreeing definitions. tests/loupe_agent/test_query_contracts.py
   proves this explicitly: the same catalog metric definition backs
   monthly, category, state, and whole-window reporting grains without
   any of them being flagged as a "grain mismatch" against the catalog,
   because the catalog was never making a reporting-grain claim in the
   first place. Every function's own docstring below still names its real
   reporting grain precisely, and that is what
   tests/loupe_agent/test_query_contracts.py asserts against.

Recovery note: get_dashboard_kpis, get_revenue_trend,
get_category_leaderboard_dashboard, get_state_breakdown_dashboard,
get_channel_mix_range, and STATE_ABBREV were deleted from
ecommerce-analytics-agent/main.py in an UNCOMMITTED working-tree change
(189 lines) at the time this migration ran -- confirmed via `git diff HEAD
-- main.py` and `git log` (commit 0cef813 is the last commit and still
contains the full block; the working tree does not). app.py's imports and
call sites were checked against the committed signatures and match
exactly. Per explicit user direction, commit 0cef813 was used read-only
(`git show 0cef813:main.py`) solely to recover this behavior; the source
repository's working tree was never modified, staged, or checked out.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from shared.config import load_persistence_mode
from shared.data_service import BigQueryClientLike, run_query
from shared.metric_catalog import get_definition
from shared.metric_catalog_persistence import resolve_current_definition

QUALIFIED_DATASET = "bigquery-public-data.thelook_ecommerce"

# ASSUMPTION carried over unchanged from the original implementation:
# "Search" in this dataset represents organic/SEO search results, not paid
# search ads (there is no separate "Paid Search" value in the data).
# Facebook, Display, and Email are treated as paid/marketing-driven
# channels.
PAID_CHANNELS = ["Facebook", "Display", "Email"]
UNPAID_CHANNELS = ["Search", "Organic"]

ALL_CATEGORIES = [
    "Accessories", "Active", "Blazers & Jackets", "Clothing Sets", "Dresses",
    "Fashion Hoodies & Sweatshirts", "Intimates", "Jeans", "Jumpsuits & Rompers",
    "Leggings", "Maternity", "Outerwear & Coats", "Pants", "Pants & Capris",
    "Plus", "Shorts", "Skirts", "Sleep & Lounge", "Socks", "Socks & Hosiery",
    "Suits", "Suits & Sport Coats", "Sweaters", "Swim", "Tops & Tees", "Underwear",
]

STATE_ABBREV = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}


def _metric_ref(client: BigQueryClientLike, name: str) -> dict:
    """Attach the certified-catalog identity for `name` to a result, per
    docs/loupe-agent.md's "state whether it is gross, net, delivered-only,
    or another certified version" requirement.

    Per Phase 6D: when LOUPE_PERSISTENCE_MODE=persisted, this resolves the
    REAL, currently-persisted certification status via
    shared.metric_catalog_persistence.resolve_current_definition() rather
    than shared.metric_catalog's in-memory constants -- so a metric
    certified through Governance's UI is reflected here immediately, and a
    persistence outage is reported honestly as "unavailable" rather than
    silently substituting the constants-mode value. In explicit constants
    mode (the default, pre-cutover/demo configuration), this reads
    shared.metric_catalog unchanged from before.

    Returns {"name": ..., "certification_status": ..., "version": ...} --
    "unregistered" means the catalog has no entry at all for `name`;
    "unavailable" means persisted mode is configured but storage could not
    be read. Neither is ever silently treated as "certified."
    """

    if load_persistence_mode() == "persisted":
        resolution = resolve_current_definition(client, name)
        if not resolution.ok:
            return {"name": name, "certification_status": "unavailable", "version": None}
        if resolution.definition is None:
            return {"name": name, "certification_status": "unregistered", "version": None}
        return {
            "name": resolution.definition.name,
            "certification_status": resolution.definition.certification_status,
            "version": resolution.definition.version,
        }

    definition = get_definition(name)
    if definition is None:
        return {"name": name, "certification_status": "unregistered", "version": None}
    return {
        "name": definition.name,
        "certification_status": definition.certification_status,
        "version": definition.version,
    }


# ---------------------------------------------------------------------------
# Filter/param builder shared by the dashboard queries
# ---------------------------------------------------------------------------


def _build_filters(
    start_date: date,
    end_date: date,
    categories: Optional[list[str]] = None,
    states: Optional[list[str]] = None,
    alias_products: str = "p",
    alias_users: str = "u",
) -> tuple[str, dict, bool]:
    """Build a parameterized WHERE clause plus the run_query() params dict.

    Returns (where_clause_sql, params, join_users) -- join_users tells the
    caller whether a `users` join is required to satisfy the state filter
    (some callers, like the state breakdown, always join users regardless
    of this flag for their own reasons).
    """

    # start_date/end_date are bound as STRING (shared.data_service.run_query
    # has no DATE scalar type -- see _scalar_type_for()), so the SQL casts
    # them back to DATE explicitly rather than relying on an implicit
    # STRING-vs-DATE comparison, which BigQuery does not always perform.
    filters = ["DATE(oi.created_at) BETWEEN DATE(@start_date) AND DATE(@end_date)"]
    params: dict = {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
    join_users = False
    if categories:
        filters.append(f"{alias_products}.category IN UNNEST(@categories)")
        params["categories"] = list(categories)
    if states:
        filters.append(f"{alias_users}.state IN UNNEST(@states)")
        params["states"] = list(states)
        join_users = True
    return " AND ".join(filters), params, join_users


# ---------------------------------------------------------------------------
# Agent-facing single/multi entity queries
# ---------------------------------------------------------------------------


def get_category_metrics(client: BigQueryClientLike, category: str) -> Optional[dict]:
    """Revenue, margin, return rate, and volume for a single category.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row for the given category, aggregated over ALL
    order_items for that category (no date filter, no day/month breakdown).
    Source tables: order_items, products.
    """

    sql = f"""
    SELECT
        p.category,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS total_items,
        COUNTIF(oi.status = 'Returned') AS returned_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    WHERE p.category = @category
    GROUP BY p.category
    """
    rows = run_query(client, sql, {"category": category})
    if not rows:
        return None
    row = rows[0]
    return {
        "category": row["category"],
        "revenue": row["revenue"],
        "margin": row["margin"],
        "total_items": row["total_items"],
        "returned_items": row["returned_items"],
        "return_rate_pct": row["return_rate_pct"],
        "revenue_metric": _metric_ref(client, "revenue"),
        "margin_metric": _metric_ref(client, "margin"),
        "return_rate_metric": _metric_ref(client, "return_rate"),
    }


def get_company_benchmark(client: BigQueryClientLike) -> dict:
    """Company-wide blended averages for comparison context.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row, aggregated over the entire order_items table (no
    date, category, or state filter). Source tables: order_items, products.
    """

    sql = f"""
    SELECT
        ROUND(SAFE_DIVIDE(SUM(oi.sale_price - p.cost), SUM(oi.sale_price)) * 100, 2) AS avg_margin_pct,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS avg_return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    """
    rows = run_query(client, sql)
    row = rows[0]
    return {
        "avg_margin_pct": row["avg_margin_pct"],
        "avg_return_rate_pct": row["avg_return_rate_pct"],
    }


def get_multi_category_comparison(client: BigQueryClientLike, categories: list[str]) -> list[dict]:
    """Revenue/margin/return rate for multiple categories, ordered by
    return rate ascending (best-performing first), matching the original
    presentation order.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per requested category, aggregated over ALL order_items
    for that category (no date filter). Source tables: order_items, products.
    """

    sql = f"""
    SELECT
        p.category,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS total_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    WHERE p.category IN UNNEST(@categories)
    GROUP BY p.category
    ORDER BY return_rate_pct ASC
    """
    return run_query(client, sql, {"categories": list(categories)})


def get_state_metrics(client: BigQueryClientLike, state: str) -> Optional[dict]:
    """Revenue, margin, return rate, and volume for a single state.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row for the given state, aggregated over ALL order_items
    for that state (no date filter). Source tables: order_items, products, users.
    """

    sql = f"""
    SELECT
        u.state,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS total_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id
    WHERE u.state = @state
    GROUP BY u.state
    """
    rows = run_query(client, sql, {"state": state})
    if not rows:
        return None
    row = rows[0]
    return {
        "state": row["state"],
        "revenue": row["revenue"],
        "margin": row["margin"],
        "total_items": row["total_items"],
        "return_rate_pct": row["return_rate_pct"],
        "revenue_metric": _metric_ref(client, "revenue"),
        "margin_metric": _metric_ref(client, "margin"),
        "return_rate_metric": _metric_ref(client, "return_rate"),
    }


def get_multi_state_comparison(client: BigQueryClientLike, states: list[str]) -> list[dict]:
    """Revenue/margin/return rate for multiple states, ordered by return
    rate ascending, matching the original presentation order.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per requested state, aggregated over ALL order_items
    for that state (no date filter). Source tables: order_items, products, users.
    """

    sql = f"""
    SELECT
        u.state,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS total_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id
    WHERE u.state IN UNNEST(@states)
    GROUP BY u.state
    ORDER BY return_rate_pct ASC
    """
    return run_query(client, sql, {"states": list(states)})


def get_returns_leakage(client: BigQueryClientLike) -> list[dict]:
    """Every category ranked by absolute margin dollars lost to returns,
    worst first -- per shared.metric_catalog's margin_leakage definition:
    ranked by absolute dollars, not by return-rate percentage.

    Measurement grain (per shared.metric_catalog): order_item (margin
    dollars lost, summed per returned order_item, then grouped by category).
    Reporting grain: one row per category, aggregated over ALL order_items
    for that category (no date filter -- this function's "window" is
    unbounded/all-time). Source tables: order_items, products.
    """

    sql = f"""
    SELECT
        p.category,
        COUNTIF(oi.status = 'Returned') AS returned_items,
        COUNT(*) AS total_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct,
        SUM(IF(oi.status = 'Returned', oi.sale_price - p.cost, 0)) AS margin_lost_to_returns
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    GROUP BY p.category
    ORDER BY margin_lost_to_returns DESC
    """
    return run_query(client, sql)


def get_channel_mix_trend(client: BigQueryClientLike) -> dict:
    """Trailing 24-month order volume by paid vs. unpaid traffic source,
    per shared.metric_catalog's channel_mix definition.

    Measurement grain (per shared.metric_catalog): order_item (each row
    attributed to the traffic_source of the user who placed it; the
    denominator behind paid_share_pct is a count of order_item rows, not
    orders or sessions -- see shared.metric_catalog's channel_mix entry).
    Reporting grain: one row per month per channel group (paid/unpaid).
    Source tables: order_items, users.
    """

    sql = f"""
    SELECT
        FORMAT_DATE('%Y-%m', DATE(oi.created_at)) AS month,
        u.traffic_source,
        COUNT(*) AS order_count
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id
    WHERE DATE(oi.created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 24 MONTH)
    GROUP BY month, u.traffic_source
    ORDER BY month ASC
    """
    rows = run_query(client, sql)

    monthly: dict[str, dict] = {}
    for row in rows:
        bucket = monthly.setdefault(row["month"], {"paid": 0, "unpaid": 0, "total": 0})
        if row["traffic_source"] in PAID_CHANNELS:
            bucket["paid"] += row["order_count"]
        else:
            bucket["unpaid"] += row["order_count"]
        bucket["total"] += row["order_count"]

    months = [
        {
            "month": month,
            "paid": v["paid"],
            "unpaid": v["unpaid"],
            "total": v["total"],
            "paid_share_pct": round((v["paid"] / v["total"]) * 100, 1) if v["total"] else 0,
        }
        for month, v in sorted(monthly.items())
    ]
    return {"months": months, "channel_mix_metric": _metric_ref(client, "channel_mix")}


def get_lever_price_position(client: BigQueryClientLike, category: str) -> Optional[dict]:
    """Average sale price, cost, and margin percentage for a category --
    the query body of the original get_lever_baseline()'s
    "category_price_position" branch.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row for the given category, aggregated over ALL order_items
    for that category (no date filter). Source tables: order_items, products.
    """

    sql = f"""
    SELECT
        p.category,
        ROUND(AVG(oi.sale_price), 2) AS avg_sale_price,
        ROUND(AVG(p.cost), 2) AS avg_cost,
        ROUND(SAFE_DIVIDE(SUM(oi.sale_price - p.cost), SUM(oi.sale_price)) * 100, 2) AS margin_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    WHERE p.category = @category
    GROUP BY p.category
    """
    rows = run_query(client, sql, {"category": category})
    if not rows:
        return None
    row = rows[0]
    return {
        "category": row["category"],
        "avg_sale_price": row["avg_sale_price"],
        "avg_cost": row["avg_cost"],
        "margin_pct": row["margin_pct"],
        "margin_metric": _metric_ref(client, "margin"),
    }


# ---------------------------------------------------------------------------
# Dashboard queries -- recovered from commit 0cef813 (see module docstring)
# ---------------------------------------------------------------------------


def get_dashboard_kpis(
    client: BigQueryClientLike,
    start_date: date,
    end_date: date,
    categories: Optional[list[str]] = None,
    states: Optional[list[str]] = None,
) -> dict:
    """Top-line KPIs for the dashboard, filtered by date range and
    optional category/state.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row, aggregated over the filtered date range/category/state.
    Source tables: order_items, products, and users (only when a state
    filter is supplied). Date boundary: DATE(oi.created_at) BETWEEN
    DATE(@start_date) AND DATE(@end_date) -- inclusive on both ends.
    """

    where_clause, params, join_users = _build_filters(start_date, end_date, categories, states)
    join_clause = f"JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id" if join_users else ""

    sql = f"""
    SELECT
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS total_items,
        COUNTIF(oi.status = 'Returned') AS returned_items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    {join_clause}
    WHERE {where_clause}
    """
    rows = run_query(client, sql, params)
    row = rows[0] if rows else {}
    return {
        "revenue": row.get("revenue") or 0,
        "margin": row.get("margin") or 0,
        "total_items": row.get("total_items") or 0,
        "returned_items": row.get("returned_items") or 0,
        "return_rate_pct": row.get("return_rate_pct") or 0,
    }


def get_revenue_trend(
    client: BigQueryClientLike,
    start_date: date,
    end_date: date,
    categories: Optional[list[str]] = None,
    states: Optional[list[str]] = None,
) -> list[dict]:
    """Monthly revenue/margin/volume trend, filtered.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per month within the filtered date range/category/state.
    Source tables: order_items, products, and users (only when a state
    filter is supplied). Date boundary: same inclusive DATE(...) BETWEEN
    as get_dashboard_kpis().
    """

    where_clause, params, join_users = _build_filters(start_date, end_date, categories, states)
    join_clause = f"JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id" if join_users else ""

    sql = f"""
    SELECT
        FORMAT_DATE('%Y-%m', DATE(oi.created_at)) AS month,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS items
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    {join_clause}
    WHERE {where_clause}
    GROUP BY month
    ORDER BY month ASC
    """
    return run_query(client, sql, params)


def get_category_leaderboard_dashboard(
    client: BigQueryClientLike,
    start_date: date,
    end_date: date,
    states: Optional[list[str]] = None,
) -> list[dict]:
    """Every category with revenue, margin, return rate, filtered by
    date/state.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per category within the filtered date range/state.
    Source tables: order_items, products, and users (only when a state
    filter is supplied). Date boundary: same inclusive DATE(...) BETWEEN
    as get_dashboard_kpis().
    """

    where_clause, params, join_users = _build_filters(start_date, end_date, categories=None, states=states)
    join_clause = f"JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id" if join_users else ""

    sql = f"""
    SELECT
        p.category,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS items,
        ROUND(SAFE_DIVIDE(COUNTIF(oi.status = 'Returned'), COUNT(*)) * 100, 2) AS return_rate_pct
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    {join_clause}
    WHERE {where_clause}
    GROUP BY p.category
    ORDER BY revenue DESC
    """
    return run_query(client, sql, params)


def get_state_breakdown_dashboard(
    client: BigQueryClientLike,
    start_date: date,
    end_date: date,
    categories: Optional[list[str]] = None,
) -> list[dict]:
    """Every state with revenue, margin, order volume, filtered by
    date/category. Always joins users (needed to group by state), per the
    original implementation's `params_with_users = params` comment.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per state within the filtered date range/category.
    Source tables: order_items, products, users (unconditional). Date
    boundary: same inclusive DATE(...) BETWEEN as get_dashboard_kpis().
    """

    where_clause, params, _ = _build_filters(start_date, end_date, categories=categories, states=None)

    sql = f"""
    SELECT
        u.state,
        SUM(oi.sale_price) AS revenue,
        SUM(oi.sale_price - p.cost) AS margin,
        COUNT(*) AS items
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id
    WHERE {where_clause}
    GROUP BY u.state
    ORDER BY revenue DESC
    """
    rows = run_query(client, sql, params)
    return [
        {
            "state": row["state"],
            "state_abbrev": STATE_ABBREV.get(row["state"], ""),
            "revenue": row["revenue"],
            "margin": row["margin"],
            "items": row["items"],
        }
        for row in rows
    ]


def get_channel_mix_range(
    client: BigQueryClientLike,
    start_date: date,
    end_date: date,
    categories: Optional[list[str]] = None,
    states: Optional[list[str]] = None,
) -> list[dict]:
    """Monthly paid vs. unpaid order mix, filtered by date range and
    optional category/state.

    The original implementation's join_clause ternary
    (`... if not join_users else ...`) produced the identical JOIN on both
    branches -- a channel-mix query always needs users.traffic_source
    regardless of whether a state filter is active. That dead conditional
    is not ported; the join is unconditional here, per explicit Phase 5
    direction to simplify no-op branches during the rewrite. Behavior is
    unchanged.

    Measurement grain (per shared.metric_catalog): order_item.
    Reporting grain: one row per month within the filtered date range/category/state.
    Source tables: order_items, products, users (unconditional). Date
    boundary: same inclusive DATE(...) BETWEEN as get_dashboard_kpis().
    """

    where_clause, params, _ = _build_filters(start_date, end_date, categories, states)

    sql = f"""
    SELECT
        FORMAT_DATE('%Y-%m', DATE(oi.created_at)) AS month,
        u.traffic_source,
        COUNT(*) AS order_count
    FROM `{QUALIFIED_DATASET}.order_items` oi
    JOIN `{QUALIFIED_DATASET}.products` p ON oi.product_id = p.id
    JOIN `{QUALIFIED_DATASET}.users` u ON oi.user_id = u.id
    WHERE {where_clause}
    GROUP BY month, u.traffic_source
    ORDER BY month ASC
    """
    rows = run_query(client, sql, params)

    monthly: dict[str, dict] = {}
    for row in rows:
        bucket = monthly.setdefault(row["month"], {"paid": 0, "unpaid": 0, "total": 0})
        if row["traffic_source"] in PAID_CHANNELS:
            bucket["paid"] += row["order_count"]
        else:
            bucket["unpaid"] += row["order_count"]
        bucket["total"] += row["order_count"]

    return [
        {"month": month, "paid": v["paid"], "unpaid": v["unpaid"], "total": v["total"]}
        for month, v in sorted(monthly.items())
    ]
