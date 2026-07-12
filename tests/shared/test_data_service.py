"""Tests for shared/data_service.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shared.data_service import (
    ConcurrentModificationError,
    IncidentNotFoundError,
    QuerySafetyConfig,
    TableMetadata,
    UnsafeQueryError,
    apply_incident_transition,
    derive_source_health,
    get_bigquery_client,
    get_incident,
    get_table_metadata,
    list_active_incidents_for_table,
    list_tables,
    run_query,
)
from shared.incidents import InvalidTransitionError
from shared.models import Incident
from tests.shared.conftest import FakeTable


def _incident_row(**overrides) -> dict:
    row = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="null_spike",
        severity="high",
        status="open",
        observed_value=None,
        expected_value=None,
        sql_template=None,
        query_hash=None,
        affected_metrics=[],
        affected_dashboards=[],
        playbook=None,
        owner=None,
        acknowledged_at=None,
        resolved_at=None,
        resolution_notes=None,
        rule_version=None,
        recurrence_of_incident_id=None,
    )
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# get_bigquery_client (item 1: mocked, no real credentials/network)
# ---------------------------------------------------------------------------


def test_get_bigquery_client_passes_project_and_location():
    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        get_bigquery_client("ai-weekend-agent-501502", location="US")

    mock_client_cls.assert_called_once_with(
        project="ai-weekend-agent-501502", location="US"
    )


def test_get_bigquery_client_defaults_location_to_none():
    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        get_bigquery_client("ai-weekend-agent-501502")

    mock_client_cls.assert_called_once_with(
        project="ai-weekend-agent-501502", location=None
    )


def test_get_bigquery_client_never_passes_a_credentials_argument():
    # No credential file is loaded or required: omitting `credentials=`
    # entirely is what lets Application Default Credentials resolve on
    # their own, per docs/development.md's credential strategy.
    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        get_bigquery_client("some-project")

    _, kwargs = mock_client_cls.call_args
    assert "credentials" not in kwargs


def test_get_bigquery_client_returns_the_constructed_client():
    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        sentinel_client = MagicMock()
        mock_client_cls.return_value = sentinel_client
        result = get_bigquery_client("some-project")

    assert result is sentinel_client


# ---------------------------------------------------------------------------
# run_query safety contract (item 3)
# ---------------------------------------------------------------------------


def test_run_query_returns_rows_as_dicts(fake_client):
    fake_client.next_rows = [{"a": 1}, {"a": 2}]
    rows = run_query(fake_client, "SELECT a FROM t")
    assert rows == [{"a": 1}, {"a": 2}]


def test_run_query_builds_scalar_and_array_parameters(fake_client):
    fake_client.next_rows = []
    run_query(
        fake_client,
        "SELECT * FROM t WHERE a=@a AND b=@b AND c=@c AND d IN UNNEST(@d)",
        {"a": "x", "b": 3, "c": 1.5, "d": ["one", "two"]},
    )
    sql, job_config = fake_client.queries[0]
    param_names = {p.name for p in job_config.query_parameters}
    assert param_names == {"a", "b", "c", "d"}


def test_run_query_rejects_empty_array_parameter(fake_client):
    with pytest.raises(ValueError):
        run_query(fake_client, "SELECT * FROM t WHERE a IN UNNEST(@a)", {"a": []})


def test_run_query_never_interpolates_parameter_values_into_sql_text(fake_client):
    fake_client.next_rows = []
    malicious_value = "x'; DROP TABLE t; --"
    original_sql = "SELECT * FROM t WHERE a = @a"

    run_query(fake_client, original_sql, {"a": malicious_value})

    sql, job_config = fake_client.queries[0]
    # The SQL text reaching the client is byte-for-byte what the caller
    # supplied -- the parameter value never gets concatenated into it.
    assert sql == original_sql
    assert malicious_value not in sql
    (param,) = job_config.query_parameters
    assert param.name == "a"
    assert param.value == malicious_value


def test_run_query_applies_maximum_bytes_billed_from_safety_config(fake_client):
    fake_client.next_rows = []
    custom_safety = QuerySafetyConfig(maximum_bytes_billed=1_000, timeout_seconds=5.0)
    run_query(fake_client, "SELECT 1", safety=custom_safety)
    _, job_config = fake_client.queries[0]
    assert job_config.maximum_bytes_billed == 1_000


def test_run_query_default_safety_config_applies_a_byte_ceiling(fake_client):
    fake_client.next_rows = []
    run_query(fake_client, "SELECT 1")
    _, job_config = fake_client.queries[0]
    assert job_config.maximum_bytes_billed == 500_000_000


def test_run_query_passes_timeout_from_safety_config_to_result(fake_client):
    fake_client.next_rows = []
    custom_safety = QuerySafetyConfig(timeout_seconds=7.5)
    run_query(fake_client, "SELECT 1", safety=custom_safety)
    assert fake_client.last_job.received_timeout == 7.5


def test_run_query_default_safety_config_applies_a_30_second_timeout(fake_client):
    fake_client.next_rows = []
    run_query(fake_client, "SELECT 1")
    assert fake_client.last_job.received_timeout == 30.0


def test_run_query_propagates_query_exceptions_without_swallowing_them(fake_client):
    fake_client.query_exception = RuntimeError("BigQuery is unavailable")
    with pytest.raises(RuntimeError, match="BigQuery is unavailable"):
        run_query(fake_client, "SELECT 1")


def test_run_query_returns_an_empty_list_for_empty_results(fake_client):
    fake_client.next_rows = []
    assert run_query(fake_client, "SELECT 1 WHERE FALSE") == []


def test_run_query_returned_structure_is_deterministic_list_of_dicts(fake_client):
    fake_client.next_rows = [
        {"incident_id": "inc_2", "severity": "low"},
        {"incident_id": "inc_1", "severity": "high"},
    ]
    rows = run_query(fake_client, "SELECT * FROM t")
    # Order and shape match exactly what the client's row iterator
    # produced -- no re-sorting, re-keying, or dropped fields.
    assert rows == [
        {"incident_id": "inc_2", "severity": "low"},
        {"incident_id": "inc_1", "severity": "high"},
    ]
    assert all(isinstance(row, dict) for row in rows)


@pytest.mark.parametrize(
    "safe_sql",
    [
        "SELECT 1",
        "SELECT a, b FROM t WHERE a = @a",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT 1 AS a UNION ALL SELECT 2 AS a",
        "SELECT 1 AS a INTERSECT DISTINCT SELECT 1 AS a",
        "SELECT 1 AS a EXCEPT DISTINCT SELECT 2 AS a",
    ],
)
def test_run_query_allows_read_only_statements(fake_client, safe_sql):
    fake_client.next_rows = []
    run_query(fake_client, safe_sql)  # must not raise
    assert len(fake_client.queries) == 1


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "DELETE FROM t WHERE a = 1",
        "UPDATE t SET a = 1",
        "INSERT INTO t (a) VALUES (1)",
        "DROP TABLE t",
        "CREATE TABLE t (a INT64)",
        "MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET a = s.a",
        "SELECT * FROM t; DROP TABLE t;",
    ],
)
def test_run_query_rejects_non_read_only_statements(fake_client, unsafe_sql):
    with pytest.raises(UnsafeQueryError):
        run_query(fake_client, unsafe_sql)
    # The unsafe statement must never reach the client at all.
    assert fake_client.queries == []


def test_run_query_rejects_unparseable_sql_rather_than_assuming_it_is_safe(fake_client):
    with pytest.raises(UnsafeQueryError):
        run_query(fake_client, "this is not sql at all ??? (((")
    assert fake_client.queries == []


def test_run_query_rejects_empty_sql(fake_client):
    with pytest.raises(UnsafeQueryError):
        run_query(fake_client, "")
    assert fake_client.queries == []


# ---------------------------------------------------------------------------
# list_tables / get_table_metadata (Phase 4: metadata API, not query jobs --
# used by Data Quality Triage's table-profiling step so it never touches a
# bigquery.Client directly)
# ---------------------------------------------------------------------------


def test_list_tables_returns_table_ids_for_the_dataset(fake_client):
    fake_client.table_ids_by_dataset["bigquery-public-data.thelook_ecommerce"] = [
        "order_items",
        "orders",
    ]
    assert list_tables(fake_client, "bigquery-public-data.thelook_ecommerce") == [
        "order_items",
        "orders",
    ]


def test_list_tables_returns_empty_list_for_a_dataset_with_no_tables(fake_client):
    assert list_tables(fake_client, "bigquery-public-data.thelook_ecommerce") == []


def test_get_table_metadata_extracts_row_count_modified_at_and_columns(fake_client):
    from datetime import datetime, timezone

    modified = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    fake_client.tables["bigquery-public-data.thelook_ecommerce.order_items"] = FakeTable(
        table_id="order_items",
        num_rows=181_594,
        modified=modified,
        columns=["id", "order_id", "user_id", "status", "created_at"],
    )

    metadata = get_table_metadata(fake_client, "bigquery-public-data.thelook_ecommerce", "order_items")

    assert metadata == TableMetadata(
        table_id="order_items",
        row_count=181_594,
        modified_at=modified.isoformat(),
        columns=["id", "order_id", "user_id", "status", "created_at"],
        column_types={
            "id": "STRING",
            "order_id": "STRING",
            "user_id": "STRING",
            "status": "STRING",
            "created_at": "STRING",
        },
    )


def test_get_table_metadata_extracts_declared_column_types_from_the_schema(fake_client):
    fake_client.tables["bigquery-public-data.thelook_ecommerce.order_items"] = FakeTable(
        table_id="order_items",
        num_rows=10,
        modified=None,
        columns=["id", "sale_price", "created_at"],
        column_types={"id": "INTEGER", "sale_price": "FLOAT", "created_at": "TIMESTAMP"},
    )
    metadata = get_table_metadata(fake_client, "bigquery-public-data.thelook_ecommerce", "order_items")
    assert metadata.column_types == {"id": "INTEGER", "sale_price": "FLOAT", "created_at": "TIMESTAMP"}


def test_get_table_metadata_handles_missing_modified_time_as_none(fake_client):
    fake_client.tables["bigquery-public-data.thelook_ecommerce.orders"] = FakeTable(
        table_id="orders", num_rows=0, modified=None, columns=[]
    )
    metadata = get_table_metadata(fake_client, "bigquery-public-data.thelook_ecommerce", "orders")
    assert metadata.modified_at is None
    assert metadata.row_count == 0
    assert metadata.columns == []
    assert metadata.column_types == {}


def test_get_table_metadata_coerces_null_row_count_to_zero(fake_client):
    # BigQuery can report num_rows=None for freshly created/empty tables --
    # this must never surface as None to callers doing numeric comparisons.
    table = FakeTable(table_id="orders", num_rows=0, modified=None, columns=["id"])
    table.num_rows = None
    fake_client.tables["bigquery-public-data.thelook_ecommerce.orders"] = table
    metadata = get_table_metadata(fake_client, "bigquery-public-data.thelook_ecommerce", "orders")
    assert metadata.row_count == 0


# ---------------------------------------------------------------------------
# get_incident
# ---------------------------------------------------------------------------


def test_get_incident_returns_none_when_not_found(fake_client):
    fake_client.next_rows = []
    assert get_incident(fake_client, "does_not_exist") is None


def test_get_incident_returns_the_matching_incident(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="acknowledged")]
    incident = get_incident(fake_client, "inc_1")
    assert incident is not None
    assert incident.incident_id == "inc_1"
    assert incident.status == "acknowledged"


# ---------------------------------------------------------------------------
# list_active_incidents_for_table / derive_source_health
# (item 5: source-health aggregation)
# ---------------------------------------------------------------------------


def test_list_active_incidents_excludes_detected_and_resolved(fake_client):
    fake_client.next_rows = [
        _incident_row(incident_id="inc_detected", status="detected"),
        _incident_row(incident_id="inc_open", status="open"),
        _incident_row(incident_id="inc_resolved", status="resolved"),
    ]
    active = list_active_incidents_for_table(fake_client, "thelook_ecommerce", "order_items")
    active_ids = {incident.incident_id for incident in active}
    assert active_ids == {"inc_open"}


def test_source_health_is_healthy_when_only_a_detected_incident_exists(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="detected", severity="high")]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "healthy"
    assert health.active_incident_ids == []


def test_source_health_is_healthy_when_only_a_resolved_incident_exists(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="resolved", severity="high")]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "healthy"
    assert health.active_incident_ids == []


def test_source_health_is_degraded_with_an_active_low_severity_incident(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="open", severity="low")]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "degraded"
    assert health.active_incident_ids == ["inc_1"]


def test_source_health_is_critical_with_an_active_high_severity_incident(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="acknowledged", severity="high")]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "critical"


def test_source_health_stays_healthy_when_the_only_high_severity_incident_is_resolved(fake_client):
    # A resolved high-severity incident must not force "critical" -- only
    # active incidents count, regardless of their severity.
    fake_client.next_rows = [
        _incident_row(incident_id="inc_1", status="resolved", severity="high"),
        _incident_row(incident_id="inc_2", status="detected", severity="high"),
    ]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "healthy"


def test_source_health_is_degraded_specifically_for_a_mitigated_incident(fake_client):
    # "mitigated" is still active per ACTIVE_INCIDENT_STATUSES -- its
    # penalty may be reduced elsewhere (trust scoring), but source health
    # must still reflect that the underlying incident is not closed.
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="mitigated", severity="low")]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "degraded"
    assert health.active_incident_ids == ["inc_1"]


def test_source_health_is_critical_with_multiple_active_incidents_of_mixed_severity(fake_client):
    # Several active incidents at once, only one of which is high
    # severity -- the highest active severity present must win.
    fake_client.next_rows = [
        _incident_row(incident_id="inc_low", status="open", severity="low"),
        _incident_row(incident_id="inc_medium", status="investigating", severity="medium"),
        _incident_row(incident_id="inc_high", status="acknowledged", severity="high"),
    ]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "critical"
    assert set(health.active_incident_ids) == {"inc_low", "inc_medium", "inc_high"}


def test_source_health_is_degraded_with_multiple_active_incidents_none_high_severity(fake_client):
    fake_client.next_rows = [
        _incident_row(incident_id="inc_low", status="open", severity="low"),
        _incident_row(incident_id="inc_medium", status="mitigated", severity="medium"),
    ]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "degraded"
    assert set(health.active_incident_ids) == {"inc_low", "inc_medium"}


def test_source_health_reflects_only_active_incidents_when_mixed_with_resolved(fake_client):
    # Resolved incidents mixed in with active ones must not change the
    # result beyond what the active incidents alone would produce.
    fake_client.next_rows = [
        _incident_row(incident_id="inc_resolved_high", status="resolved", severity="high"),
        _incident_row(incident_id="inc_active_low", status="open", severity="low"),
    ]
    health = derive_source_health(fake_client, "thelook_ecommerce", "order_items")
    assert health.status == "degraded"
    assert health.active_incident_ids == ["inc_active_low"]


def test_source_health_is_healthy_for_an_unknown_table_with_no_incident_rows(fake_client):
    fake_client.next_rows = []
    health = derive_source_health(fake_client, "thelook_ecommerce", "table_that_does_not_exist")
    assert health.status == "healthy"
    assert health.active_incident_ids == []


# ---------------------------------------------------------------------------
# apply_incident_transition (item 4: re-fetches persisted status)
# ---------------------------------------------------------------------------


def test_apply_incident_transition_returns_updated_status(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="open")]
    updated = apply_incident_transition(fake_client, "inc_1", "acknowledged")
    assert updated.status == "acknowledged"


def test_apply_incident_transition_reads_persisted_status_not_a_stale_object(fake_client):
    # Even if a caller believes (incorrectly) that the incident is still
    # "open", the function must validate against what get_incident()
    # currently returns -- here, "acknowledged" -- not any assumption the
    # caller brings in.
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="acknowledged")]
    updated = apply_incident_transition(fake_client, "inc_1", "investigating")
    assert updated.status == "investigating"


def test_apply_incident_transition_rejects_invalid_transition(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="detected")]
    with pytest.raises(InvalidTransitionError):
        apply_incident_transition(fake_client, "inc_1", "resolved")


def test_apply_incident_transition_sets_resolution_notes(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="investigating")]
    updated = apply_incident_transition(
        fake_client, "inc_1", "resolved", resolution_notes="Backfill completed."
    )
    assert updated.status == "resolved"
    assert updated.resolution_notes == "Backfill completed."


def test_apply_incident_transition_raises_when_incident_not_found(fake_client):
    fake_client.next_rows = []
    with pytest.raises(IncidentNotFoundError):
        apply_incident_transition(fake_client, "does_not_exist", "acknowledged")


def test_apply_incident_transition_succeeds_when_expected_status_matches(fake_client):
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="open")]
    updated = apply_incident_transition(
        fake_client, "inc_1", "acknowledged", expected_current_status="open"
    )
    assert updated.status == "acknowledged"


def test_apply_incident_transition_raises_concurrent_modification_on_mismatch(fake_client):
    # Simulates a second writer having already moved the incident past
    # what this caller last saw ("open") before this call runs.
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="acknowledged")]
    with pytest.raises(ConcurrentModificationError):
        apply_incident_transition(
            fake_client, "inc_1", "investigating", expected_current_status="open"
        )


def test_apply_incident_transition_without_expected_status_skips_the_concurrency_check(fake_client):
    # expected_current_status is opt-in: omitting it falls back to
    # validating the freshly-read status alone, per the documented
    # concurrency limitation.
    fake_client.next_rows = [_incident_row(incident_id="inc_1", status="acknowledged")]
    updated = apply_incident_transition(fake_client, "inc_1", "investigating")
    assert updated.status == "investigating"
