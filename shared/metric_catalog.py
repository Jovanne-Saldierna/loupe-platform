"""The certified (and proposed / pending-validation) metric catalog.

Per the migration spec: "Populate shared/metric_catalog.py from Loupe's
current formulas, but initially mark each extracted definition as
proposed or pending_validation. ... Do not silently declare the existing
formulas certified merely because they are currently used."

The five entries below were extracted directly from
loupe-ecommerce-agent's real, deployed main.py (the confirmed-accurate
GitHub source) -- not invented. Every one is marked "pending_validation":
these are formulas already running in production, but nobody has yet
formally reviewed and certified them against docs/contracts.md's
certification bar (name, formula, grain, time behavior, source tables,
filters, owner, freshness, version, all recorded and approved). Promoting
any of these to "proposed" or "certified" is a deliberate, later action
-- not something this catalog does on its own.

This is an in-memory registry for now, matching current reality: per
docs/development.md, "Metric Governance currently runs on sample catalog
data," not live BigQuery-backed persistence. Swapping the backing store
to a real `loupe_platform.metric_catalog` BigQuery table (via
shared/data_service.py's run_query()) is later work, once Governance is
actually wired to live persistence -- the lookup functions below
(get_definition, list_definitions, definitions_referencing_table) are
written so that swap only touches this file's internals, not any caller.
"""

from __future__ import annotations

from shared.models import MetricDefinition

_CATALOG: dict[str, MetricDefinition] = {}


def _register(definition: MetricDefinition) -> None:
    _CATALOG[definition.name] = definition


_register(
    MetricDefinition(
        name="revenue",
        owner="loupe-agent-team",
        description=(
            "Total booked revenue from order items in the selected window. "
            "Does NOT exclude returned items -- this is gross booked revenue, "
            "not net/delivered revenue, per the current implementation in "
            "loupe-ecommerce-agent/main.py."
        ),
        formula="SUM(order_items.sale_price) over the selected date range and dimensions",
        measurement_grain=(
            "order_item -- one order_items row contributes its sale_price to "
            "revenue. Additive across any dimensional or temporal grouping "
            "(day, month, category, state, whole window, ...); see this "
            "definition's downstream query functions for which reporting "
            "grains actually ship today (docs/contracts.md's 'Measurement "
            "grain vs. reporting grain')."
        ),
        freshness_expectation="undeclared -- not enforced in the current implementation",
        certification_status="pending_validation",
        approved_source_tables=["order_items", "orders", "products"],
        required_filters=[],
        downstream_dashboards=["loupe_agent dashboard: KPI summary, revenue trend"],
        version="v1-extracted",
    )
)

_register(
    MetricDefinition(
        name="margin",
        owner="loupe-agent-team",
        description=(
            "Gross margin dollars: sale price minus product cost, at the "
            "declared measurement grain. Gross margin dollars and gross "
            "margin percentage are tracked as distinct figures, per "
            "docs/loupe-agent.md."
        ),
        formula="SUM(order_items.sale_price - products.cost) over the selected date range and dimensions",
        measurement_grain=(
            "order_item -- one order_items row (joined to its product for "
            "cost) contributes (sale_price - cost) to margin. Additive "
            "across any dimensional or temporal grouping, same as revenue."
        ),
        freshness_expectation="undeclared -- not enforced in the current implementation",
        certification_status="pending_validation",
        approved_source_tables=["order_items", "products"],
        required_filters=[],
        downstream_dashboards=["loupe_agent dashboard: KPI summary"],
        version="v1-extracted",
    )
)

_register(
    MetricDefinition(
        name="return_rate",
        owner="loupe-agent-team",
        description="Returned order items divided by total order items in the selected window.",
        formula="COUNT(order_items WHERE status = 'Returned') / COUNT(order_items)",
        measurement_grain=(
            "order_item -- a ratio metric whose numerator and denominator "
            "are BOTH counted at order-item grain: numerator = order_items "
            "rows with status='Returned', denominator = all order_items "
            "rows in the same filter scope (same date range/category/state "
            "applied to both sides of the division in the same query -- "
            "never a numerator and denominator computed from two "
            "differently-scoped queries). It is not orders, not units, and "
            "not sessions."
        ),
        freshness_expectation="undeclared -- not enforced in the current implementation",
        certification_status="pending_validation",
        approved_source_tables=["order_items"],
        required_filters=[],
        downstream_dashboards=["loupe_agent dashboard: KPI summary"],
        version="v1-extracted",
    )
)

_register(
    MetricDefinition(
        name="margin_leakage",
        owner="loupe-agent-team",
        description=(
            "Ranks categories/products by absolute gross-margin dollars lost "
            "to returns, not by return percentage -- a high return rate on "
            "negligible sales does not outrank a material dollar loss, per "
            "docs/loupe-agent.md."
        ),
        formula="SUM(margin dollars on returned order items), ranked descending by absolute value",
        measurement_grain=(
            "order_item -- margin dollars lost is summed from individual "
            "order_items rows with status='Returned' (sale_price - cost per "
            "row), then grouped by category (or product) for presentation. "
            "The underlying atomic entity is the same order_item as "
            "revenue/margin; only the grouping dimension differs."
        ),
        freshness_expectation="undeclared -- not enforced in the current implementation",
        certification_status="pending_validation",
        approved_source_tables=["order_items", "products"],
        required_filters=[],
        downstream_dashboards=["loupe_agent: returns/leakage analysis view"],
        version="v1-extracted",
    )
)

_register(
    MetricDefinition(
        name="channel_mix",
        owner="loupe-agent-team",
        description=(
            "Paid vs. organic traffic-channel share over a trailing 24-month "
            "window, per docs/loupe-agent.md and the real trailing-window "
            "logic in get_channel_mix_trend."
        ),
        formula="Share of order_items attributed to PAID_CHANNELS vs UNPAID_CHANNELS over a trailing 24-month window",
        measurement_grain=(
            "order_item -- each order_items row is attributed to the "
            "traffic_source of the user (users.traffic_source) who placed "
            "it, then classified paid vs. unpaid. The denominator for "
            "paid_share_pct is a COUNT(*) of order_items rows in scope, NOT "
            "a count of distinct orders and NOT a count of site sessions or "
            "events -- despite the SQL column alias `order_count` in "
            "get_channel_mix_trend()/get_channel_mix_range(), it counts "
            "order_item rows, one per line item, not one per order. This is "
            "flagged explicitly because that column name reads as if it "
            "were order-grain; it is not."
        ),
        freshness_expectation="undeclared -- not enforced in the current implementation",
        certification_status="pending_validation",
        # Corrected during the grain-mismatch review: the real
        # implementation (get_channel_mix_trend/get_channel_mix_range in
        # apps/loupe_agent/metrics.py) joins order_items to users on
        # traffic_source -- it never touches an `events` table. The
        # previous approved_source_tables=["order_items", "events"] did
        # not match what the query actually runs; fixed to reflect the
        # real query, matching apps/loupe_agent/source_health.py's
        # TABLE_DEPENDENCIES["channel_mix_trend"]. Not a certification
        # change -- this metric remains pending_validation.
        approved_source_tables=["order_items", "users"],
        required_filters=[],
        downstream_dashboards=["loupe_agent dashboard: channel mix view"],
        version="v1-extracted",
    )
)


def get_definition(name: str) -> MetricDefinition | None:
    """Look up a single metric definition by name, or None if not catalogued."""

    return _CATALOG.get(name)


def list_definitions() -> list[MetricDefinition]:
    """Return every catalogued definition, in registration order."""

    return list(_CATALOG.values())


def _normalize_table_identifier(table_id: str) -> str:
    """Reduce a BigQuery table identifier to its bare table name.

    BigQuery table identifiers can be written as `project.dataset.table`,
    `dataset.table`, or a bare `table`, optionally backtick-quoted -- and
    the table name is always the LAST dot-separated segment regardless of
    which form is used. This module's catalog stores
    approved_source_tables as bare table names (e.g. "order_items"), so
    definitions_referencing_table() below normalizes both the caller's
    input and the catalog's own stored values through this function
    before comparing -- a caller passing
    "bigquery-public-data.thelook_ecommerce.order_items" or
    "thelook_ecommerce.order_items" must match the same catalog entries as
    one passing "order_items". Normalizing the catalog side too (not just
    the input) keeps this correct even if a future definition is
    registered with a qualified approved_source_tables entry.

    This is a plain string split, never a guess at which segment is the
    project vs. the dataset -- there is nothing to get wrong here as long
    as the table name is genuinely the last segment, which BigQuery's
    identifier grammar guarantees.
    """

    return table_id.strip().strip("`").rsplit(".", 1)[-1].strip()


def definitions_referencing_table(table_id: str) -> list[MetricDefinition]:
    """Return every definition whose approved_source_tables includes
    `table_id` -- used to answer "which metrics does this table affect"
    when a data-quality incident lands on that table.

    `table_id` may be a bare table name, `dataset.table`, or
    `project.dataset.table` (optionally backtick-quoted) -- see
    _normalize_table_identifier()'s docstring. Matching is always done on
    normalized bare table names, so no caller can silently miss an
    impacted metric merely because it passed a different (but equally
    valid) qualified form than the catalog happens to store.
    """

    normalized_target = _normalize_table_identifier(table_id)
    return [
        definition
        for definition in _CATALOG.values()
        if normalized_target
        in {_normalize_table_identifier(table) for table in definition.approved_source_tables}
    ]
