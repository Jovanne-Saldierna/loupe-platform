from api.models import (
    CatalogMetric,
    ChangeRiskItem,
    CompletenessCheckItem,
    GovernanceHelperRequest,
    GovernanceRecommendation,
    ReviewFinding,
    TrustFactor,
)
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


def test_helper_summary_includes_downstream_assets_change_risk_and_recommendations(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        governance_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    governance_helper.answer_governance_question(_payload(
        downstream_assets=["loupe_agent dashboard: KPI summary, revenue trend"],
        change_risk=[ChangeRiskItem(category="Calculation drift", status="risk", detail="Avoid SELECT * in governed metric SQL.")],
        recommendations=[GovernanceRecommendation(action="Needs review", rationale="Trust score is 62.", priority="required")],
    ))

    summary = captured["state_summary"]
    assert "loupe_agent dashboard: KPI summary, revenue trend" in summary
    assert "Calculation drift (risk): Avoid SELECT * in governed metric SQL." in summary
    assert "[required] Needs review: Trust score is 62." in summary


def test_helper_summary_omits_new_sections_when_not_supplied(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        governance_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    governance_helper.answer_governance_question(_payload())

    summary = captured["state_summary"]
    assert "Downstream dashboards/reports on file" not in summary
    assert "Definition-change risk categories" not in summary
    assert "Governance recommendations already surfaced" not in summary
    assert "Governance completeness checks" not in summary


def test_helper_summary_includes_completeness_checks(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        governance_helper,
        "ask_dashboard",
        lambda question, state_summary: captured.setdefault("state_summary", state_summary) or "ok",
    )

    governance_helper.answer_governance_question(_payload(completeness=[
        CompletenessCheckItem(label="Has owner", passed=True, detail="Owner on file: Analytics."),
        CompletenessCheckItem(label="Has certified definition", passed=False, detail="Certification status is \"proposed\"."),
    ]))

    summary = captured["state_summary"]
    assert "Governance completeness checks:" in summary
    assert "- PASS Has owner: Owner on file: Analytics." in summary
    assert "- FAIL Has certified definition: Certification status is \"proposed\"." in summary
