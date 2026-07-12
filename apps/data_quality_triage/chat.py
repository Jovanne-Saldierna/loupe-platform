"""The embedded "ask this dashboard" assistant for Data Quality Triage.

Owns exactly one responsibility: answering free-form questions grounded
ONLY in a plain-text summary of the app's current, real state -- never
inventing incidents, severities, statuses, or table health that aren't in
that summary. summarize_state_for_chat() builds that summary from real
shared.models.Incident and shared.models.SourceHealth objects, and
honestly notes when incident-lifecycle persistence isn't connected yet
(see incident_lifecycle.py) rather than silently pretending history is
available.
"""

from __future__ import annotations

import os

from shared.models import Incident, SourceHealth


def _anthropic_api_key() -> str:
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        import streamlit as st

        return str(st.secrets.get("ANTHROPIC_API_KEY", "")).strip()
    except Exception:
        return ""


def summarize_state_for_chat(state: dict) -> str:
    """Flatten the current app state into plain text for the chat
    assistant's context.

    Expected shape (all optional, all default to empty/False):
    - state["incidents"]: list[shared.models.Incident]
    - state["source_health"]: list[shared.models.SourceHealth]
    - state["persistence_available"]: bool -- whether incident_lifecycle.py
      successfully reached live persistence this run (see main.py's
      build_state()). False before Phase 6, always.
    """

    incidents: list[Incident] = state.get("incidents", [])
    source_health: list[SourceHealth] = state.get("source_health", [])
    persistence_available: bool = state.get("persistence_available", False)

    lines = [f"Incidents detected this run: {len(incidents)}"]
    if not persistence_available:
        lines.append(
            "Incident lifecycle persistence (acknowledge/investigate/resolve history) is not "
            "connected yet -- only this run's freshly detected incidents are known, not historical ones."
        )
    for incident in incidents:
        affected = f", affects: {', '.join(incident.affected_metrics)}" if incident.affected_metrics else ""
        lines.append(
            f"- {incident.table_id}: {incident.check_type} ({incident.severity}, {incident.status}){affected}"
        )
    for health in source_health:
        lines.append(f"- Source health: {health.dataset}.{health.table_id} = {health.status}")
    return "\n".join(lines)


def ask_dashboard(question: str, state_summary: str) -> str:
    """Answer a free-form question about the current triage state,
    grounded only in `state_summary`.
    """

    key = _anthropic_api_key()
    if not key:
        return (
            "Claude isn't configured in this environment (no API key found), so I can't answer live "
            "questions right now. The rest of the app still works from deterministic data-quality checks."
        )
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate
    except Exception:
        return "Claude isn't installed in this environment, so I can't answer live questions right now."

    model = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are the embedded assistant for a data-quality incident triage tool. Answer using ONLY
the state data provided below. Never invent incidents, severities, statuses, or table health that aren't in
the data. Detection is always deterministic: you explain it, you never decide whether something is broken,
and you never claim to have resolved or reclassified an incident. If the data doesn't support an answer, say
so plainly. Keep answers short and direct, 2-4 sentences, on-call-engineer tone. Do not use em dashes.

Current State:
{state_summary}""",
            ),
            ("user", "{question}"),
        ]
    )
    response = (prompt_template | model).invoke({"state_summary": state_summary, "question": question})
    content = getattr(response, "content", "")
    return content or "Claude returned no content, so rely on the deterministic incident data instead."
