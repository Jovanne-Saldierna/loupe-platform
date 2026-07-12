"""Deterministic remediation guidance for Data Quality Triage: playbooks
and impact estimates for already-detected shared.models.Incident records.

Both functions here are pure and deterministic -- no Streamlit, no LLM, no
BigQuery access. This is the one authoritative playbook-generation path,
per the Phase 4 migration constraint ("Maintain one authoritative
playbook-generation path"): explanations.py may narrate what an incident
means, but never generates its own playbook list, and no other module in
this app produces playbook text.

Adapted from the original data-quality-incident-triage-agent's
src/ui.py::_playbooks_for_incident() and _impact_estimate() (read-only
reference; that repository is not modified), rewritten to read
shared.models.Incident -- the cross-app contract -- instead of the
original app's local Incident model, since severity/check_type vocabulary
differs between the two (see checks.py's severity-collapse documentation).
"""

from __future__ import annotations

from shared.models import Incident

_PLAYBOOK_MAX = 4

# One deterministic, check-type-specific first action per implemented
# check. Ordered to match checks.py's GUARDRAILS_CATALOG.
_CHECK_TYPE_PLAYBOOKS: dict[str, str] = {
    "row_count_empty": "Confirm whether the upstream load job ran; check its most recent run status and logs.",
    "freshness_delay": "Check the scheduling system for this table's load job; confirm it is not stuck or failing silently.",
    "volume_drift": "Compare against a known-good historical load to confirm whether this is a real business shift or a pipeline issue.",
    "null_ratio": "Check the upstream source or mapping for the affected column for a recent schema or logic change.",
    "duplicate_key_ratio": "Re-run the load with deduplication enabled, or confirm the candidate key is actually meant to be unique.",
    "no_primary_key_candidate": "Confirm with the table owner whether this table is intentionally keyless before building joins against it.",
}


def suggested_playbooks_for_incident(incident: Incident) -> list[str]:
    """Deterministic next-action suggestions for one incident, gated by
    its severity and check_type. Always returns at most 4 items, most
    urgent first.
    """

    playbooks: list[str] = []

    if incident.severity == "high":
        playbooks.append(
            "Escalate to the table owner now; pause or flag downstream jobs that depend on this table if possible."
        )

    if incident.check_type in _CHECK_TYPE_PLAYBOOKS:
        playbooks.append(_CHECK_TYPE_PLAYBOOKS[incident.check_type])

    playbooks.append(
        "Confirm whether any certified metric or dashboard depends on this table before communicating impact."
    )

    if incident.affected_metrics:
        playbooks.append(
            f"Notify owners of affected metrics ({', '.join(incident.affected_metrics)}) that this table is degraded."
        )

    return playbooks[:_PLAYBOOK_MAX]


def estimate_impact(incidents: list[Incident]) -> tuple[str, str]:
    """Deterministic impact summary across a list of currently active
    incidents. Returns (level, summary_text) where level is one of
    "High", "Medium", "Low", "Minimal".

    Counts by shared.models.Severity's 3-level vocabulary (high/medium/low)
    -- the only vocabulary an Incident can carry, since checks.py's
    severity collapse has already run by the time an Incident exists.
    """

    high_count = sum(1 for incident in incidents if incident.severity == "high")
    medium_count = sum(1 for incident in incidents if incident.severity == "medium")
    low_count = sum(1 for incident in incidents if incident.severity == "low")

    if high_count >= 2:
        return "High", f"{high_count} high-severity incidents are active. Multiple sources may be at risk."
    if high_count == 1:
        return "High", "1 high-severity incident is active and needs prompt attention."
    if medium_count >= 1:
        return "Medium", f"{medium_count} medium-severity incident(s) active; monitor for escalation."
    if low_count >= 1:
        return "Low", f"{low_count} low-severity incident(s) active; no immediate action required."
    return "Minimal", "No active incidents detected."
