"""Tests for apps/metric_governance/main.py's state assembly.

Only build_state() is tested here -- it's the one function in main.py
that does not touch Streamlit, so it can run in a plain pytest process
without a Streamlit runtime. main()/render_app() invocation is not unit
tested, consistent with the rest of this codebase's approach to
Streamlit rendering code.
"""

from __future__ import annotations

from apps.metric_governance.main import build_state


def test_build_state_uses_the_real_catalog():
    state = build_state()
    names = {definition.name for definition in state["definitions"]}
    assert names == {"revenue", "margin", "return_rate", "margin_leakage", "channel_mix"}


def test_build_state_contains_no_fictional_arr_sample_data():
    state = build_state()
    names = {definition.name for definition in state["definitions"]}
    assert "ARR" not in names
    owners = {definition.owner for definition in state["definitions"]}
    assert "Finance" not in owners
    assert "Product Analytics" not in owners


def test_build_state_every_definition_is_pending_validation_not_certified():
    state = build_state()
    for definition in state["definitions"]:
        assert definition.certification_status == "pending_validation"


def test_build_state_catalog_tables_is_the_union_of_approved_source_tables():
    state = build_state()
    expected = sorted(
        {table for definition in state["definitions"] for table in definition.approved_source_tables}
    )
    assert state["catalog_tables"] == expected
    assert "order_items" in state["catalog_tables"]


def test_build_state_diffs_are_empty_since_no_alternate_versions_exist_yet():
    # Per the corrected Phase 3 semantics, diffs only ever contain
    # alternate versions of the SAME metric or explicit curated
    # comparisons -- never pairs inferred from shared source tables. The
    # real catalog has exactly one version of each of its five metrics
    # and no curated comparisons registered, so this must honestly be
    # empty, not populated with e.g. margin vs margin_leakage (which are
    # two different metrics, not two versions of one metric).
    state = build_state()
    assert state["diffs"] == []


def test_build_state_never_pairs_distinct_metrics_that_share_tables():
    state = build_state()
    diff_names = {(d.left_name, d.right_name) for d in state["diffs"]}
    assert ("margin", "margin_leakage") not in diff_names
    assert ("margin", "revenue") not in diff_names
