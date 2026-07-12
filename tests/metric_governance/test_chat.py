"""Tests for apps/metric_governance/chat.py."""

from __future__ import annotations

import apps.metric_governance.chat as chat
from apps.metric_governance.models import DefinitionDiff
from shared.models import MetricDefinition


def _definition(**overrides) -> MetricDefinition:
    defaults = dict(
        name="margin",
        owner="loupe-agent-team",
        description="d",
        formula="f",
        measurement_grain="one row per day",
        freshness_expectation="undeclared",
        certification_status="pending_validation",
        approved_source_tables=["order_items", "products"],
        version="v1",
    )
    defaults.update(overrides)
    return MetricDefinition(**defaults)


def test_summarize_state_for_chat_lists_each_definition():
    state = {"definitions": [_definition()], "diffs": []}
    summary = chat.summarize_state_for_chat(state)
    assert "margin" in summary
    assert "certification_status=pending_validation" in summary


def test_summarize_state_for_chat_includes_diff_recommended_use():
    diff = DefinitionDiff(
        left_name="margin",
        right_name="margin_leakage",
        matches=[],
        differences=["Grain differs."],
        recommended_use="Confirm which definition answers the question being asked.",
    )
    state = {"definitions": [], "diffs": [diff]}
    summary = chat.summarize_state_for_chat(state)
    assert "Confirm which definition answers the question being asked." in summary


def test_summarize_state_for_chat_handles_missing_keys_gracefully():
    summary = chat.summarize_state_for_chat({})
    assert "Catalogued metric definitions: 0" in summary
    assert "Definition diffs on file: 0" in summary


def test_ask_dashboard_falls_back_without_an_api_key(monkeypatch):
    monkeypatch.setattr(chat, "_anthropic_api_key", lambda: "")
    answer = chat.ask_dashboard("What's certified?", "Catalogued metric definitions: 0")
    assert "isn't configured" in answer
