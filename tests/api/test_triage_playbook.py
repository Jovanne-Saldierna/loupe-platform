from api.models import TriagePlaybookRequest
from api.services import triage_playbook


def _payload(**overrides) -> TriagePlaybookRequest:
    defaults = dict(
        incident_id="inc-42",
        table_id="order_items",
        check_type="null_ratio",
        severity="high",
        status="open",
        created_at="2026-07-01T00:00:00Z",
        observed_value=0.42,
        expected_value=0.02,
        affected_metrics=["revenue"],
        governed_metric_names=["revenue", "margin"],
        downstream_assets=["Executive Revenue Dashboard"],
        active_incident_count=2,
        source_health="degraded",
        owner=None,
    )
    defaults.update(overrides)
    return TriagePlaybookRequest(**defaults)


_STRUCTURED_RESPONSE = (
    "ROOT CAUSE: An upstream ETL change likely stopped populating a required column.\n"
    "IMPACT: Revenue and margin reporting for this table may be understated until resolved.\n"
    "NEXT ACTION: Check the most recent upstream deploy or schema change for this table."
)


def test_playbook_grounds_narration_in_supplied_incident_context_only(monkeypatch):
    captured = {}

    def fake_ask_dashboard(question, state_summary):
        captured["question"] = question
        captured["state_summary"] = state_summary
        return _STRUCTURED_RESPONSE

    monkeypatch.setattr(triage_playbook, "ask_dashboard", fake_ask_dashboard)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = triage_playbook.generate_triage_playbook(_payload())

    summary = captured["state_summary"]
    assert "inc-42" in summary
    assert "order_items" in summary
    assert "null_ratio" in summary
    assert "0.42" in summary
    assert "0.02" in summary
    assert "revenue" in summary
    assert "Executive Revenue Dashboard" in summary
    assert "degraded" in summary
    # No invented facts beyond what was supplied.
    assert "products" not in summary

    assert result.likely_root_cause == "An upstream ETL change likely stopped populating a required column."
    assert result.impact_summary == "Revenue and margin reporting for this table may be understated until resolved."
    assert result.next_action == "Check the most recent upstream deploy or schema change for this table."
    assert result.model == "claude-sonnet-4-6"


def test_playbook_deterministic_fields_never_call_the_model(monkeypatch):
    # sql_checks/debugging_steps/owner_recommendation must be identical
    # regardless of what the AI call returns -- proving they are computed
    # independently, not derived from narration.
    monkeypatch.setattr(triage_playbook, "ask_dashboard", lambda question, summary: "anything")

    result = triage_playbook.generate_triage_playbook(_payload(check_type="duplicate_key_ratio", owner="data-eng"))

    assert any("duplicate" in step.lower() for step in result.debugging_steps)
    assert any("HAVING COUNT(*) > 1" in check.sql for check in result.sql_checks)
    assert result.owner_recommendation == "Owner on record: data-eng. Notify them directly."
    assert result.affected_downstream_assets == ["Executive Revenue Dashboard"]
    assert result.affected_governed_metrics == ["revenue", "margin"]


def test_playbook_recommends_escalation_when_no_owner(monkeypatch):
    monkeypatch.setattr(triage_playbook, "ask_dashboard", lambda question, summary: "anything")
    result = triage_playbook.generate_triage_playbook(_payload(owner=None))
    assert "escalate" in result.owner_recommendation.lower()


def test_playbook_falls_back_honestly_when_narration_is_unstructured(monkeypatch):
    monkeypatch.setattr(
        triage_playbook,
        "ask_dashboard",
        lambda question, summary: "Claude isn't configured in this environment (no API key found), so I can't answer.",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = triage_playbook.generate_triage_playbook(_payload())

    assert result.likely_root_cause == triage_playbook._UNKNOWN_ROOT_CAUSE
    assert "Claude isn't configured" in result.impact_summary
    assert result.next_action == triage_playbook._FALLBACK_NEXT_ACTION
    assert result.model is None


def test_playbook_parses_partial_structured_response(monkeypatch):
    monkeypatch.setattr(
        triage_playbook,
        "ask_dashboard",
        lambda question, summary: "ROOT CAUSE: Unknown -- insufficient data\nIMPACT: Minor, isolated to this table.",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = triage_playbook.generate_triage_playbook(_payload())

    assert result.likely_root_cause == "Unknown -- insufficient data"
    assert result.impact_summary == "Minor, isolated to this table."
    # NEXT ACTION line was missing entirely -- honest fallback, not invented.
    assert result.next_action == triage_playbook._FALLBACK_NEXT_ACTION
