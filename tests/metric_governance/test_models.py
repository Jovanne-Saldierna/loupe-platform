"""Tests for apps/metric_governance/models.py."""

from __future__ import annotations

from apps.metric_governance.models import DefinitionDiff, SqlReviewFinding, SqlReviewResult


def test_definition_diff_constructs_with_all_fields():
    diff = DefinitionDiff(
        left_name="margin",
        right_name="margin_leakage",
        matches=["Both draw from order_items, products."],
        differences=["Grain differs."],
        recommended_use="Confirm which definition answers the question being asked.",
    )
    assert diff.left_name == "margin"
    assert diff.matches == ["Both draw from order_items, products."]


def test_sql_review_finding_and_result_construct():
    finding = SqlReviewFinding(severity="high", category="Approved Tables", message="msg")
    result = SqlReviewResult(
        score=70,
        summary="Query needs governance review.",
        findings=[finding],
        referenced_tables=["order_items"],
        recommended_next_steps=["Confirm grain"],
    )
    assert result.findings[0].severity == "high"
    assert result.referenced_tables == ["order_items"]
