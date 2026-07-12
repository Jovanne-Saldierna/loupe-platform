"""Tests for apps/data_quality_triage/main.py's state assembly.

Only build_state() is tested here -- it's the one function in main.py that
does not touch Streamlit, so it can run in a plain pytest process without a
Streamlit runtime, matching the pattern in
tests/metric_governance/test_main.py. main()/render_app() invocation is
not unit tested, consistent with the rest of this codebase's approach to
Streamlit rendering code.
"""

from __future__ import annotations

from datetime import datetime, timezone

import apps.data_quality_triage.main as main
from apps.data_quality_triage.profiling import QUALIFIED_DATASET
from tests.shared.conftest import FakeBigQueryClient, FakeTable


def _client_with_one_healthy_table() -> FakeBigQueryClient:
    client = FakeBigQueryClient()
    client.table_ids_by_dataset[QUALIFIED_DATASET] = ["order_items"]
    client.tables[f"{QUALIFIED_DATASET}.order_items"] = FakeTable(
        table_id="order_items",
        num_rows=181_594,
        modified=datetime.now(tz=timezone.utc),
        columns=["id", "order_id", "user_id", "status", "created_at"],
    )
    client.next_rows = [{"ratio": 0.0}]
    return client


def test_build_state_is_honestly_unavailable_when_no_project_is_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    state = main.build_state()
    assert state["data_available"] is False
    assert "GOOGLE_CLOUD_PROJECT" in state["unavailable_reason"]
    assert state["profiles"] == []
    assert state["findings"] == []
    assert state["incidents"] == []


def test_build_state_never_fabricates_sample_incidents_when_unavailable(monkeypatch):
    # Behavioral change vs. the original app: no INC-1042/INC-1043-style
    # fictional incidents ever appear here, regardless of why live data
    # couldn't be reached.
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    state = main.build_state()
    assert state["incidents"] == []


def test_build_state_is_honestly_unavailable_when_the_live_path_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")

    def _boom(project):
        raise RuntimeError("BigQuery is unreachable")

    monkeypatch.setattr(main, "get_bigquery_client", _boom)
    state = main.build_state()
    assert state["data_available"] is False
    assert "BigQuery is unreachable" in state["unavailable_reason"]
    assert state["incidents"] == []


def test_build_state_succeeds_against_a_clean_live_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    client = _client_with_one_healthy_table()
    monkeypatch.setattr(main, "get_bigquery_client", lambda project: client)

    state = main.build_state()

    assert state["data_available"] is True
    assert state["unavailable_reason"] is None
    assert len(state["profiles"]) == 1
    assert state["profiles"][0].table_id == "order_items"
    assert state["incidents"] == []  # a clean table produces zero incidents


def test_build_state_promotes_findings_into_incidents_for_an_unhealthy_table(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    client = FakeBigQueryClient()
    client.table_ids_by_dataset[QUALIFIED_DATASET] = ["dim_customers"]
    client.tables[f"{QUALIFIED_DATASET}.dim_customers"] = FakeTable(
        table_id="dim_customers", num_rows=0, modified=None, columns=["id", "email"]
    )
    client.next_rows = [{"ratio": 0.0}]
    monkeypatch.setattr(main, "get_bigquery_client", lambda project: client)

    state = main.build_state()

    assert state["data_available"] is True
    assert len(state["incidents"]) == 1
    incident = state["incidents"][0]
    assert incident.check_type == "row_count_empty"
    assert incident.status == "open"
    assert incident.dataset == QUALIFIED_DATASET


def test_build_state_persistence_available_is_always_false():
    # Phase 6 placeholder -- see incident_lifecycle.py's module docstring.
    state = main.build_state()
    assert state["persistence_available"] is False


def test_build_state_audit_reflects_whether_data_was_available(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    state = main.build_state()
    assert state["audit"][0]["event"] == "Live triage run failed"
