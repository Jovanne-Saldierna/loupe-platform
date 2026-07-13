from api.models import TriageSqlSandboxRequest
from api.services import triage_sql_sandbox


def _payload(**overrides) -> TriageSqlSandboxRequest:
    defaults = dict(incident_id="inc-1", sql="SELECT * FROM order_items", check_title="Spot-check recent rows")
    defaults.update(overrides)
    return TriageSqlSandboxRequest(**defaults)


def test_select_query_executes_and_returns_result_shape(monkeypatch):
    monkeypatch.setattr(triage_sql_sandbox, "get_client", lambda: object())
    monkeypatch.setattr(triage_sql_sandbox, "_try_dry_run_bytes", lambda client, sql: 12345)
    monkeypatch.setattr(
        triage_sql_sandbox,
        "run_query",
        lambda client, sql: [{"id": 1, "created_at": "2026-07-01"}, {"id": 2, "created_at": "2026-07-02"}],
    )

    result = triage_sql_sandbox.run_sandbox_query(_payload())

    assert result.status == "success"
    assert result.columns == ["id", "created_at"]
    assert result.rows == [{"id": 1, "created_at": "2026-07-01"}, {"id": 2, "created_at": "2026-07-02"}]
    assert result.row_count == 2
    assert result.bytes_processed == 12345
    assert result.row_limit == triage_sql_sandbox.MAX_ROWS
    assert result.error is None


def test_empty_result_set_still_reports_success(monkeypatch):
    monkeypatch.setattr(triage_sql_sandbox, "get_client", lambda: object())
    monkeypatch.setattr(triage_sql_sandbox, "_try_dry_run_bytes", lambda client, sql: None)
    monkeypatch.setattr(triage_sql_sandbox, "run_query", lambda client, sql: [])

    result = triage_sql_sandbox.run_sandbox_query(_payload(sql="SELECT * FROM order_items WHERE 1=0"))

    assert result.status == "success"
    assert result.columns == []
    assert result.rows == []
    assert result.row_count == 0
    assert result.bytes_processed is None


def test_unsafe_sql_is_rejected_before_any_client_is_constructed(monkeypatch):
    def fail_if_called():
        raise AssertionError("get_client() must not be called for a rejected query")

    monkeypatch.setattr(triage_sql_sandbox, "get_client", fail_if_called)

    result = triage_sql_sandbox.run_sandbox_query(_payload(sql="DROP TABLE order_items"))

    assert result.status == "rejected"
    assert result.error is not None
    assert "read-only" in result.error.lower() or "not allowed" in result.error.lower()
    assert result.rows == []
    assert result.row_count == 0


def test_multiple_statements_are_rejected_via_the_endpoint_path(monkeypatch):
    def fail_if_called():
        raise AssertionError("get_client() must not be called for a rejected query")

    monkeypatch.setattr(triage_sql_sandbox, "get_client", fail_if_called)

    result = triage_sql_sandbox.run_sandbox_query(_payload(sql="SELECT 1; DROP TABLE order_items;"))

    assert result.status == "rejected"


def test_bigquery_execution_failure_is_reported_as_error_not_rejected(monkeypatch):
    monkeypatch.setattr(triage_sql_sandbox, "get_client", lambda: object())
    monkeypatch.setattr(triage_sql_sandbox, "_try_dry_run_bytes", lambda client, sql: None)

    def boom(client, sql):
        raise RuntimeError("Table order_items_typo not found")

    monkeypatch.setattr(triage_sql_sandbox, "run_query", boom)

    result = triage_sql_sandbox.run_sandbox_query(_payload(sql="SELECT * FROM order_items_typo"))

    assert result.status == "error"
    assert "not found" in result.error.lower()
    assert result.rows == []


def test_client_construction_failure_is_reported_as_error(monkeypatch):
    def boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(triage_sql_sandbox, "get_client", boom)

    result = triage_sql_sandbox.run_sandbox_query(_payload())

    assert result.status == "error"
    assert result.error is not None


def test_non_json_native_cell_values_are_stringified(monkeypatch):
    import datetime
    from decimal import Decimal

    monkeypatch.setattr(triage_sql_sandbox, "get_client", lambda: object())
    monkeypatch.setattr(triage_sql_sandbox, "_try_dry_run_bytes", lambda client, sql: None)
    monkeypatch.setattr(
        triage_sql_sandbox,
        "run_query",
        lambda client, sql: [{"amount": Decimal("12.50"), "day": datetime.date(2026, 7, 1), "count": 3, "ok": True, "name": None}],
    )

    result = triage_sql_sandbox.run_sandbox_query(_payload())

    row = result.rows[0]
    assert row["amount"] == "12.50"
    assert row["day"] == "2026-07-01"
    assert row["count"] == 3
    assert row["ok"] is True
    assert row["name"] is None
