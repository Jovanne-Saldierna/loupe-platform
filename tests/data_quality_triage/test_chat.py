"""Tests for apps/data_quality_triage/chat.py."""

from __future__ import annotations

import apps.data_quality_triage.chat as chat
from shared.models import Incident, SourceHealth


def _incident(**overrides) -> Incident:
    defaults = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="null_ratio",
        severity="medium",
        status="open",
        affected_metrics=["revenue"],
    )
    defaults.update(overrides)
    return Incident(**defaults)


def test_summarize_state_for_chat_lists_each_incident():
    state = {"incidents": [_incident()], "source_health": [], "persistence_available": True}
    summary = chat.summarize_state_for_chat(state)
    assert "order_items" in summary
    assert "null_ratio" in summary
    assert "revenue" in summary


def test_summarize_state_for_chat_includes_source_health():
    health = SourceHealth(dataset="thelook_ecommerce", table_id="order_items", status="degraded")
    state = {"incidents": [], "source_health": [health], "persistence_available": True}
    summary = chat.summarize_state_for_chat(state)
    assert "order_items = degraded" in summary


def test_summarize_state_for_chat_notes_when_persistence_is_unavailable():
    summary = chat.summarize_state_for_chat({"incidents": [], "source_health": [], "persistence_available": False})
    assert "not connected yet" in summary


def test_summarize_state_for_chat_handles_missing_keys_gracefully():
    summary = chat.summarize_state_for_chat({})
    assert "Incidents detected this run: 0" in summary
    assert "not connected yet" in summary  # defaults persistence_available to False


def test_ask_dashboard_falls_back_without_an_api_key(monkeypatch):
    monkeypatch.setattr(chat, "_anthropic_api_key", lambda: "")
    answer = chat.ask_dashboard("Any incidents?", "Incidents detected this run: 0")
    assert "isn't configured" in answer
