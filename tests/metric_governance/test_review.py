"""Tests for apps/metric_governance/review.py."""

from __future__ import annotations

from apps.metric_governance.review import review_sql


def test_review_sql_flags_select_star_and_missing_filters():
    result = review_sql(
        "SELECT * FROM order_items JOIN products ON order_items.product_id = products.id",
        ["order_items", "products"],
    )
    assert result.score < 100
    assert any(f.category == "Projection" for f in result.findings)


def test_review_sql_rejects_unapproved_tables():
    result = review_sql(
        "SELECT product_id, SUM(sale_price) FROM unapproved_table GROUP BY product_id",
        ["order_items"],
    )
    assert any(f.category == "Approved Tables" for f in result.findings)


def test_review_sql_returns_zero_score_for_empty_input():
    result = review_sql("   ", ["order_items"])
    assert result.score == 0
    assert result.referenced_tables == []


def test_review_sql_penalizes_unsafe_join_without_on():
    result = review_sql(
        "SELECT * FROM order_items JOIN products",
        ["order_items", "products"],
    )
    assert any(f.category == "Join Logic" and f.severity == "critical" for f in result.findings)


def test_review_sql_flags_missing_where_clause():
    result = review_sql(
        "SELECT product_id, SUM(sale_price) FROM order_items GROUP BY product_id",
        ["order_items"],
    )
    assert any(f.category == "Filters" for f in result.findings)


def test_review_sql_no_findings_when_query_is_clean():
    result = review_sql(
        "SELECT product_id, SUM(sale_price) AS revenue FROM order_items "
        "WHERE status = 'Complete' GROUP BY product_id",
        ["order_items"],
    )
    assert all(f.category != "Approved Tables" for f in result.findings)
    assert all(f.category != "Projection" for f in result.findings)


def test_review_sql_returns_low_score_for_unparseable_sql():
    result = review_sql("not valid sql at all (((", ["order_items"])
    assert result.score == 25
    assert any(f.category == "Syntax" for f in result.findings)


def test_review_sql_referenced_tables_are_sorted_and_deduplicated():
    result = review_sql(
        "SELECT * FROM order_items a JOIN order_items b ON a.id = b.id",
        ["order_items"],
    )
    assert result.referenced_tables == ["order_items"]
