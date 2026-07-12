"""Tests for apps/metric_governance/definition_diff.py.

The "Finance ARR" / "Product ARR" fixtures below are FICTIONAL example
data, kept only to exercise compare_definitions()'s generic field-by-field
comparison logic against a deliberately clear-cut case (same metric name,
different grain and source tables). They are not thelook_ecommerce data,
are never registered in shared/metric_catalog.py, and are never loaded by
the running application -- per the approved Phase 3 decision, production
Catalog/Definition Diff pages read only real metrics from
shared.metric_catalog.

find_comparable_pairs()'s original table-overlap heuristic (any two
metrics sharing >=2 approved source tables) has been removed: it wrongly
paired revenue, margin, and margin_leakage -- three distinct business
metrics -- as if they were alternate definitions of one metric. The tests
below prove the replacement (find_alternate_version_pairs +
find_explicit_comparison_pairs, combined via find_definition_diff_pairs)
no longer does that, while still correctly pairing genuine alternate
versions of the same metric.
"""

from __future__ import annotations

from apps.metric_governance.definition_diff import (
    compare_definitions,
    find_alternate_version_pairs,
    find_definition_diff_pairs,
    find_explicit_comparison_pairs,
)
from shared.metric_catalog import list_definitions
from shared.models import MetricDefinition


def _fictional_arr_finance() -> MetricDefinition:
    # FICTIONAL fixture data -- see module docstring.
    return MetricDefinition(
        name="ARR",
        owner="Finance",
        description="Fictional example: annual recurring revenue at account grain.",
        formula="SUM(amount) FROM fct_invoices GROUP BY account_id, month",
        measurement_grain="Account month",
        freshness_expectation="Daily by 8 AM UTC",
        certification_status="certified",
        approved_source_tables=["fct_invoices", "dim_accounts"],
        version="v1",
    )


def _fictional_arr_product() -> MetricDefinition:
    # FICTIONAL fixture data -- see module docstring.
    return MetricDefinition(
        name="ARR",
        owner="Product Analytics",
        description="Fictional example: annual recurring revenue at subscription grain.",
        formula="SUM(mrr) FROM fct_subscriptions GROUP BY subscription_id, month",
        measurement_grain="Subscription month",
        freshness_expectation="Daily by 9 AM UTC",
        certification_status="pending_validation",
        approved_source_tables=["fct_subscriptions", "dim_plans"],
        version="v1",
    )


def _versioned(name: str, version: str, **overrides) -> MetricDefinition:
    defaults = dict(
        name=name,
        owner="team",
        description="d",
        formula="f",
        measurement_grain="one row per day",
        freshness_expectation="undeclared",
        certification_status="pending_validation",
        approved_source_tables=["order_items"],
        version=version,
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


# ---------------------------------------------------------------------------
# compare_definitions -- generic field-by-field comparison utility
# (unchanged: still allowed to compare any two definitions handed to it)
# ---------------------------------------------------------------------------


def test_compare_definitions_with_no_shared_tables_recommends_treating_as_unrelated():
    diff = compare_definitions(_fictional_arr_finance(), _fictional_arr_product())
    assert "no approved source tables" in diff.differences[0]
    assert "unrelated" in diff.recommended_use


def test_compare_definitions_reports_grain_difference():
    diff = compare_definitions(_fictional_arr_finance(), _fictional_arr_product())
    assert any("Measurement grain differs" in d for d in diff.differences)


def test_compare_definitions_identical_definitions_have_no_differences():
    left = _fictional_arr_finance()
    right = _fictional_arr_finance()
    diff = compare_definitions(left, right)
    assert diff.differences == []
    assert "treat them as the same" in diff.recommended_use


def test_compare_definitions_output_names_match_input_order():
    diff = compare_definitions(_fictional_arr_finance(), _fictional_arr_product())
    assert diff.left_name == "ARR"
    assert diff.right_name == "ARR"


# ---------------------------------------------------------------------------
# find_alternate_version_pairs -- same metric identity, different version
# ---------------------------------------------------------------------------


def test_two_versions_of_the_same_metric_are_paired():
    v1 = _versioned("margin", "v1")
    v2 = _versioned("margin", "v2")
    pairs = find_alternate_version_pairs([v1, v2])
    assert len(pairs) == 1
    left, right = pairs[0]
    assert {left.version, right.version} == {"v1", "v2"}
    assert left.name == right.name == "margin"


def test_same_name_and_same_version_is_not_paired_as_a_version_pair():
    # Same name AND same version is a duplicate registration, not a
    # version pair -- it must not be surfaced as if it were one.
    dup_a = _versioned("margin", "v1")
    dup_b = _versioned("margin", "v1", owner="other-team")
    assert find_alternate_version_pairs([dup_a, dup_b]) == []


def test_different_metrics_sharing_identical_tables_are_not_automatically_paired():
    # This is the exact scenario the correction targets: revenue, margin,
    # and margin_leakage all reference order_items+products but are three
    # distinct business metrics. Sharing tables must NOT make them a
    # version pair.
    revenue = _versioned("revenue", "v1", approved_source_tables=["order_items", "products", "orders"])
    margin = _versioned("margin", "v1", approved_source_tables=["order_items", "products"])
    margin_leakage = _versioned("margin_leakage", "v1", approved_source_tables=["order_items", "products"])
    assert find_alternate_version_pairs([revenue, margin, margin_leakage]) == []


def test_find_alternate_version_pairs_against_the_real_catalog_is_empty_today():
    # The real catalog currently has exactly one version of each of its
    # five metrics -- this proves that honestly, rather than manufacturing
    # pairs from something else (like shared tables).
    assert find_alternate_version_pairs(list_definitions()) == []


def test_find_alternate_version_pairs_is_deterministically_ordered():
    v2 = _versioned("margin", "v2")
    v1 = _versioned("margin", "v1")
    pairs = find_alternate_version_pairs([v2, v1])  # deliberately reversed input
    left, right = pairs[0]
    assert (left.version, right.version) == ("v1", "v2")


# ---------------------------------------------------------------------------
# find_explicit_comparison_pairs -- curated relationships
# ---------------------------------------------------------------------------


def test_explicit_comparison_pairs_resolves_declared_names():
    a = _versioned("metric_a", "v1")
    b = _versioned("metric_b", "v1")
    pairs = find_explicit_comparison_pairs([a, b], explicit_pairs=(("metric_a", "metric_b"),))
    assert pairs == [(a, b)]


def test_explicit_comparison_pairs_skips_unresolvable_names():
    a = _versioned("metric_a", "v1")
    pairs = find_explicit_comparison_pairs([a], explicit_pairs=(("metric_a", "metric_missing"),))
    assert pairs == []


def test_real_catalog_has_no_explicit_comparisons_registered_today():
    # revenue/margin/margin_leakage are distinct metrics and must not be
    # curated into an explicit comparison relationship just because the
    # old heuristic used to pair them.
    assert find_explicit_comparison_pairs(list_definitions()) == []


# ---------------------------------------------------------------------------
# find_definition_diff_pairs -- combined, what main.py actually calls
# ---------------------------------------------------------------------------


def test_find_definition_diff_pairs_against_real_catalog_is_empty_today():
    # The honest "no alternate versions available yet" UI state is backed
    # by this being an empty list, not by the UI silently hiding pairs.
    assert find_definition_diff_pairs(list_definitions()) == []


def test_find_definition_diff_pairs_never_pairs_revenue_margin_or_margin_leakage():
    pair_names = {
        tuple(sorted((left.name, right.name)))
        for left, right in find_definition_diff_pairs(list_definitions())
    }
    assert ("margin", "margin_leakage") not in pair_names
    assert ("margin", "revenue") not in pair_names
    assert ("margin_leakage", "revenue") not in pair_names


def test_find_definition_diff_pairs_includes_version_pairs_and_excludes_unrelated_metrics():
    v1 = _versioned("margin", "v1")
    v2 = _versioned("margin", "v2")
    unrelated_a = _versioned("metric_a", "v1")
    unrelated_b = _versioned("metric_b", "v1")
    pairs = find_definition_diff_pairs([v1, v2, unrelated_a, unrelated_b])
    assert len(pairs) == 1
    left, right = pairs[0]
    assert left.name == right.name == "margin"
    assert {left.version, right.version} == {"v1", "v2"}


def test_find_definition_diff_pairs_deduplicates():
    v1 = _versioned("margin", "v1")
    v2 = _versioned("margin", "v2")
    pairs = find_definition_diff_pairs([v1, v2, v1, v2])
    assert len(pairs) == 1
