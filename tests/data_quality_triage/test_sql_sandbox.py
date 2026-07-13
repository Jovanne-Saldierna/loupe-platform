import pytest

from apps.data_quality_triage.sql_sandbox import MAX_ROWS, UnsafeSandboxQueryError, validate_and_wrap


def test_select_is_allowed_and_wrapped_with_limit():
    wrapped = validate_and_wrap("SELECT * FROM order_items")
    assert "SELECT * FROM order_items" in wrapped
    assert f"LIMIT {MAX_ROWS}" in wrapped
    assert wrapped.strip().startswith("SELECT * FROM (")


def test_with_cte_is_allowed():
    sql = "WITH recent AS (SELECT * FROM order_items) SELECT * FROM recent"
    wrapped = validate_and_wrap(sql)
    assert "WITH recent AS" in wrapped
    assert f"LIMIT {MAX_ROWS}" in wrapped


def test_custom_max_rows_is_honored():
    wrapped = validate_and_wrap("SELECT 1", max_rows=5)
    assert "LIMIT 5" in wrapped


def test_a_single_trailing_semicolon_is_tolerated():
    wrapped = validate_and_wrap("SELECT * FROM order_items;")
    assert "SELECT * FROM order_items" in wrapped


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM order_items WHERE id = 1",
        "INSERT INTO order_items (id) VALUES (1)",
        "UPDATE order_items SET status = 'x'",
        "MERGE order_items USING staging ON true WHEN MATCHED THEN UPDATE SET status = 'x'",
        "CREATE TABLE evil AS SELECT * FROM order_items",
        "DROP TABLE order_items",
        "ALTER TABLE order_items ADD COLUMN evil STRING",
        "TRUNCATE TABLE order_items",
        "CALL some_procedure()",
        "EXPORT DATA OPTIONS(uri='gs://bucket/*.csv') AS SELECT * FROM order_items",
        "LOAD DATA INTO order_items FROM FILES(uris=['gs://bucket/*.csv'])",
    ],
)
def test_write_and_ddl_statements_are_rejected(sql):
    with pytest.raises(UnsafeSandboxQueryError):
        validate_and_wrap(sql)


def test_multiple_statements_are_rejected():
    with pytest.raises(UnsafeSandboxQueryError):
        validate_and_wrap("SELECT 1; DROP TABLE order_items;")


def test_semicolon_chain_without_trailing_semicolon_is_rejected():
    with pytest.raises(UnsafeSandboxQueryError):
        validate_and_wrap("SELECT 1; SELECT 2")


def test_empty_sql_is_rejected():
    with pytest.raises(UnsafeSandboxQueryError):
        validate_and_wrap("   ")


def test_unparseable_sql_is_rejected_rather_than_assumed_safe():
    with pytest.raises(UnsafeSandboxQueryError):
        validate_and_wrap("this is not sql at all ((((")
