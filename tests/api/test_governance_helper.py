from api.models import CatalogMetric, GovernanceHelperRequest, ReviewFinding, TrustFactor
from api.services import governance_helper


def _payload(**overrides) -> GovernanceHelperRequest:
    defaults = dict(
        question="Why did this query get this trust score?",
        metric=CatalogMetric(name="revenue", version="v1", certification_status="pending_validation", measurement_grain="order_item"),
        sql="SELECT SUM(sale_price) FROM `thelook_ecommerce.order_items`",
        review_score=80,
        summary="Query is well-formed.",
        findings=[ReviewFinding(severity="warning", category="Grain", message="Measurement grain undeclared.")],
        trust_score=62,
        trust_band="moderate",
        trust_factors=[TrustFactor(name="Source health", points=-10, reason="Table is degraded.")],
        recommended_next_steps=["Add a WHERE clause scoping the date range."],
        referenced_tables=["order_items"],
        source_health="degraded",
        active_incident_ids=["inc-42"],
        override_reason=None,
    )
    defaults.update(overrides)
    return GovernanceHelperRequest(**defaults)


def test_helper_grounds_summary_in_supplied_review_context_only(monkeypatch):
    captured = {}

    def fake_ask_dashboard(question, state_summary):
        captured["question"] = question
        captured["state_summary"] = state_summary
        return "The trust score reflects degraded source health and one active incident."

    monkeypatch.setattr(governance_helper, "ask_dashboard", fake_ask_dashboard)

    payload = _payload()
    answer = governance_helper.answer_governance_question(payload)

    assert answer == "The trust score reflects degraded source health and one active incident."
    assert captured["question"] == payload.question
    # Every real fact from the request must be present in the grounding
    # summary handed to the model -- nothing fabricated, nothing dropped.
    summary = captured["state_summary"]
    assert "revenue" in summary
    assert "62" in summary
    assert "moderate" in summary
    assert "Source health: -10 points -- Table is degraded." in summary
    assert "Grain: Measurement grain undeclared." in summary
    assert "Add a WHERE clause scoping the date range." in summary
    assert "order_items" in summary
    assert "degraded" in summary
    assert "inc-42" in summary
    # No invented incident IDs or findings beyond what was supplied.
    assert "inc-99" not in summary


def test_helper_summary_states_no_incidents_when_none_supplied(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        governance_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    governance_helper.answer_governance_question(_payload(active_incident_ids=[], findings=[], recommended_next_steps=[]))

    summary = captured["state_summary"]
    assert "Active data-quality incidents on this metric's source tables: none." in summary
    assert "Findings from the deterministic SQL review: none." in summary
    assert "Recommended next steps already surfaced by the review: none." in summary


def test_helper_includes_override_reason_when_present(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        governance_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    governance_helper.answer_governance_question(_payload(override_reason="Manually reviewed by data eng."))

    assert "Manually reviewed by data eng." in captured["state_summary"]
