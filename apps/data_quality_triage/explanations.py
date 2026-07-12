"""LLM narration of already-detected Data Quality incidents.

Owns exactly one responsibility: turning a shared.models.Incident (plus,
optionally, the TableFinding that produced it, for richer narrative
detail) into calm, readable prose. Per docs/data-quality-triage.md: "AI
does not decide whether data is broken. The check result, threshold, and
deterministic rule make that decision." Every function here is handed an
already-detected, already-classified Incident and is only asked to
describe it -- narrate_incident() never sets severity, status, or
check_type, and never constructs or mutates an Incident itself. This is
the concrete implementation of the Phase 4 constraint "Claude may explain
an incident but cannot create, classify, or resolve one."

If no Anthropic API key is configured, or the langchain_anthropic package
is unavailable, narrate_incident() degrades to a deterministic, templated
fallback string built only from the structured evidence itself -- never a
placeholder that implies the LLM ran when it did not, matching the pattern
established in apps/metric_governance/explanations.py.
"""

from __future__ import annotations

import os
from typing import Optional

from apps.data_quality_triage.models import IncidentExplanation, TableFinding
from shared.models import Incident


def _anthropic_api_key() -> str:
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        import streamlit as st

        return str(st.secrets.get("ANTHROPIC_API_KEY", "")).strip()
    except Exception:
        return ""


def _format_incident(incident: Incident, finding: Optional[TableFinding]) -> str:
    lines = [
        f"Table: {incident.dataset}.{incident.table_id}",
        f"Check: {incident.check_type}",
        f"Severity: {incident.severity}",
        f"Status: {incident.status}",
    ]
    if incident.observed_value is not None:
        lines.append(f"Observed value: {incident.observed_value}")
    if incident.expected_value is not None:
        lines.append(f"Expected/threshold value: {incident.expected_value}")
    if incident.affected_metrics:
        lines.append(f"Affected metrics: {', '.join(incident.affected_metrics)}")
    else:
        lines.append("Affected metrics: None recorded")
    if finding is not None:
        lines.append(f"Summary: {finding.summary}")
        lines.append(f"Likely root cause: {finding.likely_root_cause}")
    return "\n".join(lines)


def _fallback_narrative(incident: Incident, finding: Optional[TableFinding]) -> str:
    detail = (
        finding.summary
        if finding is not None
        else f"The {incident.check_type} check failed on {incident.table_id}."
    )
    root_cause = f" Likely root cause: {finding.likely_root_cause}" if finding is not None else ""
    return (
        f"## Incident on {incident.table_id}\n\n"
        f"A {incident.severity}-severity {incident.check_type} incident is currently {incident.status}. "
        f"{detail}{root_cause}"
    )


def narrate_incident(incident: Incident, finding: Optional[TableFinding] = None) -> IncidentExplanation:
    """Narrate one already-detected incident. Grounded strictly in
    `incident` (and `finding`, if provided) -- never re-derives or
    overrides severity, status, or check_type.
    """

    key = _anthropic_api_key()
    if not key:
        return IncidentExplanation(
            incident_id=incident.incident_id,
            narrative=_fallback_narrative(incident, finding),
            used_claude=False,
        )

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate
    except Exception:
        return IncidentExplanation(
            incident_id=incident.incident_id,
            narrative=_fallback_narrative(incident, finding),
            used_claude=False,
        )

    formatted = _format_incident(incident, finding)
    prompt = f"""
You are a data reliability assistant. Explain the incident below in 2-4 sentences
for an on-call engineer or analytics steward. Use ONLY the facts given below. Do
not invent a cause, a fix, or a severity that isn't already stated -- the
severity, status, and check result are already decided; you are only explaining
them, never reclassifying or resolving the incident.

{formatted}
""".strip()

    model = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You explain already-detected, deterministic data-quality incidents. Be concise and "
                "evidence-based. Never state a severity, status, or check result that was not given to "
                "you, and never claim to have resolved, reclassified, or created an incident.",
            ),
            ("user", prompt),
        ]
    )
    response = (prompt_template | model).invoke({})
    content = getattr(response, "content", "")
    return IncidentExplanation(
        incident_id=incident.incident_id,
        narrative=content or _fallback_narrative(incident, finding),
        used_claude=bool(content),
    )
