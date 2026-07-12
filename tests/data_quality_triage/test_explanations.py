"""Tests for apps/data_quality_triage/explanations.py.

Only the deterministic fallback paths (no Anthropic API key configured)
are unit-tested here -- exercising the real LLM call would require live
credentials, which this test suite must not require, per
docs/development.md.
"""

from __future__ import annotations

import apps.data_quality_triage.explanations as explanations
from apps.data_quality_triage.models import TableFinding
from shared.models import Incident


def _no_api_key(monkeypatch):
    monkeypatch.setattr(explanations, "_anthropic_api_key", lambda: "")


def _incident(**overrides) -> Incident:
    defaults = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="duplicate_key_ratio",
        severity="high",
        status="open",
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _finding(**overrides) -> TableFinding:
    defaults = dict(
        table_id="order_items",
        check_name="duplicate_key_ratio",
        status="fail",
        severity="high",
        observed_value=0.02,
        threshold=0.01,
        summary="order_items.order_id has a 2.00% duplicate-key ratio.",
        likely_root_cause="Upstream ingestion may have re-run without deduplication.",
    )
    defaults.update(overrides)
    return TableFinding(**defaults)


def test_narrate_incident_falls_back_without_an_api_key(monkeypatch):
    _no_api_key(monkeypatch)
    explanation = explanations.narrate_incident(_incident())
    assert explanation.used_claude is False
    assert explanation.incident_id == "inc_1"
    assert "duplicate_key_ratio" in explanation.narrative
    assert "high-severity" in explanation.narrative


def test_narrate_incident_fallback_includes_finding_detail_when_provided(monkeypatch):
    _no_api_key(monkeypatch)
    explanation = explanations.narrate_incident(_incident(), _finding())
    assert "2.00% duplicate-key ratio" in explanation.narrative
    assert "Upstream ingestion may have re-run without deduplication." in explanation.narrative


def test_narrate_incident_fallback_never_invents_a_different_severity(monkeypatch):
    _no_api_key(monkeypatch)
    explanation = explanations.narrate_incident(_incident(severity="medium"))
    assert "medium-severity" in explanation.narrative
    assert "high-severity" not in explanation.narrative


def test_narrate_incident_fallback_works_without_a_finding(monkeypatch):
    _no_api_key(monkeypatch)
    explanation = explanations.narrate_incident(_incident())
    assert explanation.narrative  # non-empty, generic detail derived from the incident alone
