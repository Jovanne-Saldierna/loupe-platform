"""Tests for apps/data_quality_triage/remediation.py."""

from __future__ import annotations

from apps.data_quality_triage.remediation import estimate_impact, suggested_playbooks_for_incident
from shared.models import Incident


def _incident(**overrides) -> Incident:
    defaults = dict(
        incident_id="inc_1",
        created_at="2026-07-11T00:00:00Z",
        dataset="thelook_ecommerce",
        table_id="order_items",
        check_type="duplicate_key_ratio",
        severity="medium",
        status="open",
        affected_metrics=[],
    )
    defaults.update(overrides)
    return Incident(**defaults)


# ---------------------------------------------------------------------------
# suggested_playbooks_for_incident
# ---------------------------------------------------------------------------


def test_high_severity_leads_with_an_escalation_playbook():
    playbooks = suggested_playbooks_for_incident(_incident(severity="high"))
    assert playbooks[0].startswith("Escalate to the table owner")


def test_medium_severity_does_not_lead_with_escalation():
    playbooks = suggested_playbooks_for_incident(_incident(severity="medium"))
    assert not playbooks[0].startswith("Escalate to the table owner")


def test_check_type_specific_playbook_is_included():
    playbooks = suggested_playbooks_for_incident(_incident(check_type="row_count_empty", severity="high"))
    assert any("upstream load job" in p for p in playbooks)


def test_unknown_check_type_still_returns_generic_playbooks():
    playbooks = suggested_playbooks_for_incident(_incident(check_type="some_future_check", severity="low"))
    assert playbooks  # non-empty: the generic "confirm dependency" playbook always applies


def test_affected_metrics_add_a_notify_owners_playbook():
    playbooks = suggested_playbooks_for_incident(_incident(affected_metrics=["revenue", "margin"]))
    assert any("revenue" in p and "margin" in p for p in playbooks)


def test_playbooks_are_capped_at_four():
    incident = _incident(severity="high", check_type="row_count_empty", affected_metrics=["revenue"])
    playbooks = suggested_playbooks_for_incident(incident)
    assert len(playbooks) <= 4


# ---------------------------------------------------------------------------
# estimate_impact
# ---------------------------------------------------------------------------


def test_estimate_impact_with_no_incidents_is_minimal():
    level, summary = estimate_impact([])
    assert level == "Minimal"
    assert "No active incidents" in summary


def test_estimate_impact_with_one_high_severity_incident():
    level, summary = estimate_impact([_incident(severity="high")])
    assert level == "High"
    assert "1 high-severity" in summary


def test_estimate_impact_with_multiple_high_severity_incidents():
    level, summary = estimate_impact([_incident(severity="high"), _incident(severity="high")])
    assert level == "High"
    assert "2 high-severity" in summary


def test_estimate_impact_with_only_medium_severity_incidents():
    level, summary = estimate_impact([_incident(severity="medium"), _incident(severity="medium")])
    assert level == "Medium"
    assert "2 medium-severity" in summary


def test_estimate_impact_with_only_low_severity_incidents():
    level, summary = estimate_impact([_incident(severity="low")])
    assert level == "Low"


def test_estimate_impact_high_severity_takes_priority_over_medium_and_low():
    incidents = [_incident(severity="low"), _incident(severity="medium"), _incident(severity="high")]
    level, _ = estimate_impact(incidents)
    assert level == "High"
