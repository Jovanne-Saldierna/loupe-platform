"""Tests for shared/metric_hashing.py -- the canonical content-hash used
to distinguish MetricVersion rows whose semantic MEANING changed from
rows that merely changed review/approval metadata (Phase 6, amendment 6).
"""

from __future__ import annotations

from shared.metric_hashing import compute_content_hash


def _base_kwargs(**overrides):
    defaults = dict(
        name="revenue",
        description="Total booked revenue.",
        formula="SUM(order_items.sale_price)",
        measurement_grain="order_item",
        freshness_expectation="undeclared",
        approved_source_tables=["order_items", "orders"],
        required_filters=[],
        downstream_dashboards=["KPI summary"],
    )
    defaults.update(overrides)
    return defaults


def test_identical_content_hashes_identically():
    a = compute_content_hash(**_base_kwargs())
    b = compute_content_hash(**_base_kwargs())
    assert a == b


def test_source_table_order_does_not_affect_the_hash():
    a = compute_content_hash(**_base_kwargs(approved_source_tables=["order_items", "orders"]))
    b = compute_content_hash(**_base_kwargs(approved_source_tables=["orders", "order_items"]))
    assert a == b


def test_a_formula_change_changes_the_hash():
    a = compute_content_hash(**_base_kwargs())
    b = compute_content_hash(**_base_kwargs(formula="SUM(order_items.sale_price) - SUM(returns.amount)"))
    assert a != b


def test_a_measurement_grain_change_changes_the_hash():
    a = compute_content_hash(**_base_kwargs())
    b = compute_content_hash(**_base_kwargs(measurement_grain="order"))
    assert a != b


def test_a_downstream_dashboard_change_changes_the_hash():
    # Per amendment 6: downstream assets are treated as governed lineage
    # on this platform, so a change here IS a meaning change.
    a = compute_content_hash(**_base_kwargs())
    b = compute_content_hash(**_base_kwargs(downstream_dashboards=["KPI summary", "Revenue trend"]))
    assert a != b


def test_a_name_change_changes_the_hash():
    a = compute_content_hash(**_base_kwargs())
    b = compute_content_hash(**_base_kwargs(name="revenue_v2"))
    assert a != b


def test_owner_reviewer_and_certification_metadata_are_not_hash_inputs():
    # compute_content_hash() does not even accept owner/reviewer/
    # certification_status as parameters -- this test documents that
    # omission is deliberate, not an oversight: a MetricVersion's content
    # hash must stay identical across a certification-only change.
    import inspect

    signature = inspect.signature(compute_content_hash)
    assert "owner" not in signature.parameters
    assert "reviewer" not in signature.parameters
    assert "certification_status" not in signature.parameters
    assert "created_by" not in signature.parameters


def test_missing_optional_list_fields_default_to_empty():
    result = compute_content_hash(
        name="x",
        description="d",
        formula="f",
        measurement_grain="g",
        freshness_expectation="u",
        approved_source_tables=["t"],
    )
    assert isinstance(result, str) and len(result) == 64  # sha256 hexdigest length
