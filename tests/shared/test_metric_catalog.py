"""Tests for shared/metric_catalog.py.

The central assertion here is the one the migration spec called out
explicitly: every extracted-from-Loupe definition must be
"pending_validation", never silently "certified".
"""

from __future__ import annotations

from shared.metric_catalog import definitions_referencing_table, get_definition, list_definitions

EXPECTED_METRIC_NAMES = {"revenue", "margin", "return_rate", "margin_leakage", "channel_mix"}


def test_all_five_loupe_metrics_are_catalogued():
    names = {definition.name for definition in list_definitions()}
    assert names == EXPECTED_METRIC_NAMES


def test_no_extracted_definition_is_silently_certified():
    for definition in list_definitions():
        assert definition.certification_status == "pending_validation", (
            f"{definition.name} must not be auto-certified just because it's currently in production use"
        )


def test_get_definition_returns_none_for_unknown_metric():
    assert get_definition("does_not_exist") is None


def test_get_definition_returns_the_matching_definition():
    definition = get_definition("return_rate")
    assert definition is not None
    assert definition.name == "return_rate"
    assert "order_items" in definition.approved_source_tables


def test_definitions_referencing_table_orders_returns_only_revenue():
    matches = {d.name for d in definitions_referencing_table("orders")}
    assert matches == {"revenue"}


def test_definitions_referencing_table_users_returns_only_channel_mix():
    # Corrected during the grain-mismatch review: channel_mix's
    # approved_source_tables was ["order_items", "events"], which did not
    # match what get_channel_mix_trend()/get_channel_mix_range() actually
    # query (order_items joined to users, never an events table). Fixed to
    # ["order_items", "users"] -- see shared/metric_catalog.py's inline
    # comment on the channel_mix registration.
    matches = {d.name for d in definitions_referencing_table("users")}
    assert matches == {"channel_mix"}


def test_definitions_referencing_table_events_now_returns_nothing():
    # No catalogued metric actually queries an `events` table -- see the
    # comment above.
    assert definitions_referencing_table("events") == []


def test_definitions_referencing_table_order_items_returns_all_five():
    matches = {d.name for d in definitions_referencing_table("order_items")}
    assert matches == EXPECTED_METRIC_NAMES


def test_definitions_referencing_unrelated_table_returns_empty():
    assert definitions_referencing_table("distribution_centers") == []


# ---------------------------------------------------------------------------
# Table identifier normalization (Phase 4 correction item 4): the lookup
# must not silently miss impacted metrics just because the caller passed a
# differently-qualified, equally valid BigQuery identifier form.
# ---------------------------------------------------------------------------


def test_definitions_referencing_table_matches_a_fully_qualified_project_dataset_table():
    matches = {d.name for d in definitions_referencing_table("bigquery-public-data.thelook_ecommerce.order_items")}
    assert matches == EXPECTED_METRIC_NAMES


def test_definitions_referencing_table_matches_a_dataset_qualified_table():
    matches = {d.name for d in definitions_referencing_table("thelook_ecommerce.order_items")}
    assert matches == EXPECTED_METRIC_NAMES


def test_definitions_referencing_table_matches_a_backtick_quoted_identifier():
    matches = {d.name for d in definitions_referencing_table("`bigquery-public-data.thelook_ecommerce.order_items`")}
    assert matches == EXPECTED_METRIC_NAMES


def test_definitions_referencing_table_bare_qualified_and_dataset_qualified_forms_agree():
    bare = {d.name for d in definitions_referencing_table("orders")}
    dataset_qualified = {d.name for d in definitions_referencing_table("thelook_ecommerce.orders")}
    project_qualified = {d.name for d in definitions_referencing_table("bigquery-public-data.thelook_ecommerce.orders")}
    assert bare == dataset_qualified == project_qualified == {"revenue"}


def test_definitions_referencing_table_qualified_unrelated_table_still_returns_empty():
    assert definitions_referencing_table("bigquery-public-data.thelook_ecommerce.distribution_centers") == []
