from api.models import TriageHelperRequest
from api.services import triage_helper


def _payload(**overrides) -> TriageHelperRequest:
    defaults = dict(
        question="What happened here?",
        incident_id="inc-42",
        table_id="order_items",
        check_type="freshness",
        severity="high",
        status="open",
        created_at="2026-07-01T00:00:00Z",
        observed_value=180.0,
        expected_value=60.0,
        affected_metrics=["revenue"],
        governed_metric_names=["revenue", "margin"],
        active_incident_count=2,
        owner=None,
    )
    defaults.update(overrides)
    return TriageHelperRequest(**defaults)


def test_helper_grounds_summary_in_supplied_incident_context_only(monkeypatch):
    captured = {}

    def fake_ask_dashboard(question, state_summary):
        captured["question"] = question
        captured["state_summary"] = state_summary
        return "This table's freshness check is 120 minutes behind expectation; revenue and margin are governed on this table."

    monkeypatch.setattr(triage_helper, "ask_dashboard", fake_ask_dashboard)

    payload = _payload()
    answer = triage_helper.answer_triage_question(payload)

    assert answer.startswith("This table's freshness check")
    assert captured["question"] == payload.question
    summary = captured["state_summary"]
    assert "inc-42" in summary
    assert "order_items" in summary
    assert "freshness" in summary
    assert "high" in summary
    assert "open" in summary
    assert "180.0" in summary
    assert "60.0" in summary
    assert "Difference (observed - expected): 120.0" in summary
    assert "revenue" in summary
    assert "margin" in summary
    assert "Active incidents currently open on this table: 2" in summary
    assert "unassigned" in summary
    # No invented owner or table beyond what was supplied.
    assert "products" not in summary


def test_helper_summary_omits_missing_optional_values(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        triage_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    triage_helper.answer_triage_question(
        _payload(observed_value=None, expected_value=None, affected_metrics=[], governed_metric_names=[], active_incident_count=None)
    )

    summary = captured["state_summary"]
    assert "Observed value" not in summary
    assert "Expected value" not in summary
    assert "Difference" not in summary
    assert "Active incidents currently open" not in summary
    assert "Affected metrics named on the incident record: none recorded" in summary
    assert "Governed catalog metrics whose approved_source_tables include this table: none" in summary


def test_helper_reports_named_owner_when_present(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        triage_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    triage_helper.answer_triage_question(_payload(owner="data-eng"))

    assert "Owner: data-eng" in captured["state_summary"]


def test_model_used_reports_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert triage_helper.model_used() is None


def test_model_used_reports_model_name_with_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert triage_helper.model_used() == triage_helper.MODEL_NAME
