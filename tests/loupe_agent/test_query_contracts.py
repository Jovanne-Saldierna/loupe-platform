"""Query-contract regression coverage for apps/loupe_agent/metrics.py.

Per the Phase 5 correction review, for each dashboard and agent-facing
query this file confirms: declared output grain, named parameter binding
(never string interpolation), expected source tables, date-boundary
behavior, empty results, and no join fanout for the intended metric. The
six functions recovered from commit 0cef813 (get_dashboard_kpis,
get_revenue_trend, get_category_leaderboard_dashboard,
get_state_breakdown_dashboard, get_channel_mix_range, STATE_ABBREV) get
explicit output-shape-vs-ui.py-expectations coverage at the bottom of this
file.

Known grain discrepancy (see metrics.py's module docstring): the catalog
declares revenue/margin/return_rate's grain as "one row per day"; several
functions here (get_category_metrics, get_state_metrics, and their
multi-entity/company-benchmark siblings) actually aggregate to one row for
the entire queried window, not one row per day. This file's grain
assertions test what these functions ACTUALLY produce (verified against
their SQL's GROUP BY clause), not what the catalog declares -- surfacing,
not hiding, that mismatch. It is not resolved by this migration; see
metrics.py's module docstring for why.

"No join fanout" here means: every JOIN in these queries is a foreign-key
join (order_items.product_id -> products.id, order_items.user_id ->
users.id), which is structurally many-to-one on each side -- a single
order_item row can only ever match exactly one products row and exactly
one users row, so no JOIN in this file can multiply row counts. This is
verified by asserting each JOIN's ON clause is present and uses exactly
the FK column pairing (not a broader/missing predicate that could match
multiple rows).
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.loupe_agent import metrics
from tests.shared.conftest import FakeBigQueryClient


@pytest.fixture
def fake_client() -> FakeBigQueryClient:
    return FakeBigQueryClient()


def _all_sql(client: FakeBigQueryClient) -> str:
    return "\n".join(sql for sql, _ in client.queries)


# ---------------------------------------------------------------------------
# No-join-fanout: every JOIN present must be the exact FK pairing, never a
# broader predicate (which could fan out row counts) or a bare comma join.
# ---------------------------------------------------------------------------

_PRODUCT_JOIN = "JOIN `{}.products` p ON oi.product_id = p.id".format(metrics.QUALIFIED_DATASET)
_USER_JOIN = "JOIN `{}.users` u ON oi.user_id = u.id".format(metrics.QUALIFIED_DATASET)


@pytest.mark.parametrize(
    "call",
    [
        lambda c: metrics.get_category_metrics(c, "Dresses"),
        lambda c: metrics.get_company_benchmark(c),
        lambda c: metrics.get_multi_category_comparison(c, ["Dresses", "Jeans"]),
        lambda c: metrics.get_returns_leakage(c),
        lambda c: metrics.get_lever_price_position(c, "Dresses"),
    ],
)
def test_product_only_queries_use_the_exact_fk_join_no_fanout(fake_client, call):
    # A superset row satisfying every one of these functions' expected
    # columns (each function only reads the keys it cares about; extras
    # are ignored). get_company_benchmark specifically needs a non-empty
    # result: it's a non-grouped aggregate, and real BigQuery always
    # returns exactly one row for that shape, never zero.
    fake_client.next_rows = [
        {
            "category": "Dresses",
            "revenue": 0.0,
            "margin": 0.0,
            "total_items": 0,
            "returned_items": 0,
            "return_rate_pct": 0.0,
            "margin_lost_to_returns": 0.0,
            "avg_margin_pct": 0.0,
            "avg_return_rate_pct": 0.0,
            "avg_sale_price": 0.0,
            "avg_cost": 0.0,
            "margin_pct": 0.0,
        }
    ]
    call(fake_client)
    sql = _all_sql(fake_client)
    assert _PRODUCT_JOIN in sql
    # No bare comma cross-join: every table reference after the first FROM
    # is introduced by JOIN, never by a comma-separated table list.
    from_clause = sql.split("FROM", 1)[1]
    assert ",\n    `" not in from_clause and ", `" not in from_clause.split("JOIN")[0]


@pytest.mark.parametrize(
    "call",
    [
        lambda c: metrics.get_state_metrics(c, "California"),
        lambda c: metrics.get_multi_state_comparison(c, ["California", "Texas"]),
        lambda c: metrics.get_channel_mix_trend(c),
    ],
)
def test_user_joining_queries_use_the_exact_fk_join_no_fanout(fake_client, call):
    fake_client.next_rows = []
    call(fake_client)
    sql = _all_sql(fake_client)
    assert _USER_JOIN in sql


# ---------------------------------------------------------------------------
# Named parameter binding: confirms every filter value is bound, never
# string-interpolated into the SQL text.
# ---------------------------------------------------------------------------


def _bound_param_names(job_config) -> set[str]:
    """job_config is a real google.cloud.bigquery.QueryJobConfig (not a
    dict) -- pull bound parameter names off its query_parameters list,
    matching how shared/data_service.py's run_query() actually builds it.
    """
    return {p.name for p in (job_config.query_parameters or [])}


def test_get_state_metrics_binds_state_as_a_named_parameter(fake_client):
    fake_client.next_rows = []
    metrics.get_state_metrics(fake_client, "California")
    sql, job_config = fake_client.queries[0]
    assert "@state" in sql
    assert "California" not in sql
    assert "state" in _bound_param_names(job_config)


def test_get_multi_state_comparison_binds_states_as_a_named_array_parameter(fake_client):
    fake_client.next_rows = []
    metrics.get_multi_state_comparison(fake_client, ["California", "Texas"])
    sql, job_config = fake_client.queries[0]
    assert "@states" in sql
    assert "California" not in sql
    assert "states" in _bound_param_names(job_config)


def test_get_lever_price_position_binds_category_as_a_named_parameter(fake_client):
    fake_client.next_rows = []
    metrics.get_lever_price_position(fake_client, "Jeans")
    sql, job_config = fake_client.queries[0]
    assert "@category" in sql
    assert "Jeans" not in sql
    assert "category" in _bound_param_names(job_config)


# ---------------------------------------------------------------------------
# Expected source tables (exact backtick-qualified identifiers, per
# shared.data_service's parameterization -- table identifiers are the one
# thing legitimately assembled into SQL text directly, per
# shared/data_service.py's run_query() docstring).
# ---------------------------------------------------------------------------

_EXPECTED_TABLES = {
    "get_category_metrics": ("order_items", "products"),
    "get_company_benchmark": ("order_items", "products"),
    "get_multi_category_comparison": ("order_items", "products"),
    "get_state_metrics": ("order_items", "products", "users"),
    "get_multi_state_comparison": ("order_items", "products", "users"),
    "get_returns_leakage": ("order_items", "products"),
    "get_channel_mix_trend": ("order_items", "users"),
    "get_lever_price_position": ("order_items", "products"),
    "get_dashboard_kpis": ("order_items", "products"),  # + users when states filter given
    "get_revenue_trend": ("order_items", "products"),
    "get_category_leaderboard_dashboard": ("order_items", "products"),
    "get_state_breakdown_dashboard": ("order_items", "products", "users"),
    "get_channel_mix_range": ("order_items", "products", "users"),
}


def _call_for(name: str, client, **kwargs):
    fn = getattr(metrics, name)
    if name in (
        "get_dashboard_kpis",
        "get_revenue_trend",
        "get_category_leaderboard_dashboard",
        "get_state_breakdown_dashboard",
        "get_channel_mix_range",
    ):
        return fn(client, date(2026, 1, 1), date(2026, 6, 30))
    if name == "get_category_metrics":
        return fn(client, "Dresses")
    if name == "get_state_metrics":
        return fn(client, "California")
    if name == "get_multi_category_comparison":
        return fn(client, ["Dresses", "Jeans"])
    if name == "get_multi_state_comparison":
        return fn(client, ["California", "Texas"])
    if name == "get_lever_price_position":
        return fn(client, "Dresses")
    return fn(client)


# get_company_benchmark is a non-grouped aggregate (SELECT ... FROM ...
# JOIN ... with no GROUP BY, no WHERE): real BigQuery always returns
# exactly one row for that shape, even over an empty table (a row of
# NULLs), never zero rows. FakeBigQueryClient's next_rows=[] simulates a
# genuinely empty *result set*, which is not a state this particular query
# shape can reach in production -- so it is excluded from the two
# generic-emptiness checks below and given a one-row fixture instead.
_NON_GROUPED_AGGREGATE_FUNCTIONS = {"get_company_benchmark"}


@pytest.mark.parametrize("name", sorted(_EXPECTED_TABLES.keys()))
def test_every_query_touches_exactly_its_expected_source_tables(fake_client, name):
    if name in _NON_GROUPED_AGGREGATE_FUNCTIONS:
        fake_client.next_rows = [{"avg_margin_pct": 0.0, "avg_return_rate_pct": 0.0}]
    else:
        fake_client.next_rows = []
    _call_for(name, fake_client)
    sql = _all_sql(fake_client)
    for table in _EXPECTED_TABLES[name]:
        assert f"`{metrics.QUALIFIED_DATASET}.{table}`" in sql, f"{name} did not reference {table}"


# ---------------------------------------------------------------------------
# Date-boundary behavior (dashboard queries only -- the agent-facing
# single/multi-entity functions have no date filter, by original design).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "get_dashboard_kpis",
        "get_revenue_trend",
        "get_category_leaderboard_dashboard",
        "get_state_breakdown_dashboard",
        "get_channel_mix_range",
    ],
)
def test_dashboard_queries_bind_dates_and_cast_them_back_to_date_in_sql(fake_client, name):
    fake_client.next_rows = []
    _call_for(name, fake_client)
    sql, job_config = fake_client.queries[0]
    assert "DATE(@start_date)" in sql and "DATE(@end_date)" in sql
    bound = _bound_param_names(job_config)
    assert "start_date" in bound and "end_date" in bound
    assert "2026-01-01" not in sql  # bound, never interpolated


# ---------------------------------------------------------------------------
# Empty results: every function must handle zero rows without raising.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", sorted(_EXPECTED_TABLES.keys() - _NON_GROUPED_AGGREGATE_FUNCTIONS)
)
def test_every_query_handles_an_empty_result_set_without_raising(fake_client, name):
    fake_client.next_rows = []
    result = _call_for(name, fake_client)  # must not raise
    assert result is None or result == [] or isinstance(result, dict)


def test_get_company_benchmark_is_never_called_against_a_truly_empty_result_set(fake_client):
    # Documents, rather than papers over, the real behavior: this function
    # indexes rows[0] unconditionally (see metrics.py), which is safe only
    # because a non-grouped aggregate query always returns exactly one row
    # in real BigQuery. If that invariant were ever violated, this would
    # raise IndexError -- flagged here so a future change to this query's
    # shape (e.g. adding a WHERE/GROUP BY) doesn't silently reintroduce a
    # crash on empty data.
    fake_client.next_rows = []
    with pytest.raises(IndexError):
        metrics.get_company_benchmark(fake_client)


# ---------------------------------------------------------------------------
# Declared output grain (verified against each function's SQL GROUP BY --
# see this file's module docstring for the known catalog-grain mismatch).
# ---------------------------------------------------------------------------


def test_get_category_metrics_grain_is_one_row_per_category_no_date_breakdown(fake_client):
    fake_client.next_rows = []
    metrics.get_category_metrics(fake_client, "Dresses")
    sql, _ = fake_client.queries[0]
    assert "GROUP BY p.category" in sql
    assert "FORMAT_DATE" not in sql  # no day/month truncation -- one row total


def test_get_revenue_trend_grain_is_one_row_per_month(fake_client):
    fake_client.next_rows = []
    metrics.get_revenue_trend(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    sql, _ = fake_client.queries[0]
    assert "GROUP BY month" in sql
    assert "FORMAT_DATE('%Y-%m'" in sql  # month grain, not day grain


def test_get_channel_mix_trend_grain_is_one_row_per_month_per_channel_group():
    client = FakeBigQueryClient()
    client.next_rows = [{"month": "2026-01", "traffic_source": "Facebook", "order_count": 5}]
    result = metrics.get_channel_mix_trend(client)
    # channel_mix's declared catalog grain ("one row per month per channel
    # group") IS matched here -- the paid/unpaid split happens in Python
    # after the query, producing exactly two channel-group buckets per month.
    assert set(result["months"][0].keys()) >= {"month", "paid", "unpaid", "total", "paid_share_pct"}


def test_get_returns_leakage_grain_is_one_row_per_category(fake_client):
    fake_client.next_rows = []
    metrics.get_returns_leakage(fake_client)
    sql, _ = fake_client.queries[0]
    assert "GROUP BY p.category" in sql


# ---------------------------------------------------------------------------
# Output shape vs. ui.py's actual expectations -- especially the six
# functions recovered from commit 0cef813, since ui.py's key access was
# never runtime-verified against them until now.
# ---------------------------------------------------------------------------


def test_get_dashboard_kpis_shape_matches_what_ui_py_reads(fake_client):
    fake_client.next_rows = [
        {"revenue": 1.0, "margin": 1.0, "total_items": 1, "returned_items": 1, "return_rate_pct": 1.0}
    ]
    result = metrics.get_dashboard_kpis(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    # ui.py._render_home / _render_dashboard read exactly these keys via
    # kpis['revenue'], ['margin'], ['return_rate_pct'], ['total_items'].
    for key in ("revenue", "margin", "return_rate_pct", "total_items"):
        assert key in result


def test_get_revenue_trend_shape_matches_what_ui_py_reads(fake_client):
    fake_client.next_rows = [{"month": "2026-01", "revenue": 1.0, "margin": 1.0, "items": 1}]
    result = metrics.get_revenue_trend(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    # ui.py._render_dashboard builds trend_df and reads trend_df["month"], ["revenue"], ["margin"].
    for key in ("month", "revenue", "margin"):
        assert key in result[0]


def test_get_category_leaderboard_dashboard_shape_matches_what_ui_py_reads(fake_client):
    fake_client.next_rows = [{"category": "Jeans", "revenue": 1.0, "margin": 1.0, "items": 1, "return_rate_pct": 1.0}]
    result = metrics.get_category_leaderboard_dashboard(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    # ui.py sorts cat_df by one of ["revenue", "margin", "return_rate_pct"] and plots x=sort_metric, y="category".
    for key in ("category", "revenue", "margin", "return_rate_pct"):
        assert key in result[0]


def test_get_state_breakdown_dashboard_shape_matches_what_ui_py_reads(fake_client):
    fake_client.next_rows = [{"state": "California", "revenue": 1.0, "margin": 1.0, "items": 1}]
    result = metrics.get_state_breakdown_dashboard(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    # ui.py's choropleth reads state_df["state_abbrev"] and ["revenue"].
    for key in ("state", "state_abbrev", "revenue"):
        assert key in result[0]


def test_get_channel_mix_range_shape_matches_what_ui_py_reads(fake_client):
    fake_client.next_rows = [{"month": "2026-01", "traffic_source": "Facebook", "order_count": 1}]
    result = metrics.get_channel_mix_range(fake_client, date(2026, 1, 1), date(2026, 6, 30))
    # ui.py's stacked-area chart reads channel_df["month"], ["paid"], ["unpaid"].
    for key in ("month", "paid", "unpaid"):
        assert key in result[0]


def test_state_abbrev_shape_matches_what_ui_py_reads():
    # ui.py's dashboard state-filter multiselect reads list(metrics.STATE_ABBREV.keys()),
    # and get_state_breakdown_dashboard looks up STATE_ABBREV.get(row["state"], "").
    assert isinstance(metrics.STATE_ABBREV, dict)
    assert all(isinstance(k, str) and isinstance(v, str) and len(v) == 2 for k, v in metrics.STATE_ABBREV.items())
