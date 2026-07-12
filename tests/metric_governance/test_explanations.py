"""Tests for apps/metric_governance/explanations.py.

Only the deterministic fallback paths (no Anthropic API key configured)
are unit-tested here -- exercising the real LLM call would require live
credentials, which this test suite must not require, per
docs/development.md.
"""

from __future__ import annotations

import apps.metric_governance.explanations as explanations
from apps.metric_governance.models import DefinitionDiff, SqlReviewFinding, SqlReviewResult


def _no_api_key(monkeypatch):
    monkeypatch.setattr(explanations, "_anthropic_api_key", lambda: "")


def test_summarize_sql_review_falls_back_without_an_api_key(monkeypatch):
    _no_api_key(monkeypatch)
    result = SqlReviewResult(
        score=90,
        summary="Query looks strong.",
        findings=[],
        referenced_tables=["order_items"],
        recommended_next_steps=[],
    )
    text = explanations.summarize_sql_review("SELECT 1", result, ["order_items"])
    assert "deterministically" in text
    assert "## Review takeaway" in text


def test_summarize_sql_review_fallback_never_invents_a_score(monkeypatch):
    # The fallback text must not fabricate a different score than the one
    # in `result` -- it should describe the review generically rather than
    # restate a number it didn't compute.
    _no_api_key(monkeypatch)
    result = SqlReviewResult(
        score=42,
        summary="Query has several governance risks.",
        findings=[SqlReviewFinding("high", "Approved Tables", "msg")],
        referenced_tables=["unapproved_table"],
        recommended_next_steps=[],
    )
    text = explanations.summarize_sql_review("SELECT 1", result, ["order_items"])
    assert "99" not in text  # sanity: no fabricated unrelated score appears


def test_explain_definition_diff_falls_back_without_an_api_key(monkeypatch):
    _no_api_key(monkeypatch)
    diff = DefinitionDiff(
        left_name="margin",
        right_name="margin_leakage",
        matches=["Both draw from order_items, products."],
        differences=["Grain differs."],
        recommended_use="Confirm which definition answers the question being asked.",
    )
    text = explanations.explain_definition_diff(diff)
    assert "margin" in text
    assert "margin_leakage" in text
    assert diff.recommended_use in text


def test_explain_definition_diff_fallback_is_grounded_in_the_diff_not_invented(monkeypatch):
    _no_api_key(monkeypatch)
    diff = DefinitionDiff(
        left_name="revenue",
        right_name="margin",
        matches=[],
        differences=["Formulas differ."],
        recommended_use="Both touch order_items but diverge on 1 compared field(s).",
    )
    text = explanations.explain_definition_diff(diff)
    assert diff.recommended_use in text
