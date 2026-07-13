"""The embedded "ask me anything" assistant for the Governance dashboard.

Owns exactly one responsibility: answering free-form questions grounded
ONLY in a plain-text summary of the app's current, real state -- never
inventing metric definitions, scores, or findings that aren't in that
summary. summarize_state_for_chat() builds that summary from real data
(shared.metric_catalog definitions and this app's computed DefinitionDiff
objects); per the approved Phase 3 decision, no fictional ARR sample data
is ever summarized here.
"""

from __future__ import annotations

import os

from apps.metric_governance.models import DefinitionDiff
from shared.models import MetricDefinition


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

    Expected shape (all optional, all default to empty):
    - state["definitions"]: list[shared.models.MetricDefinition]
    - state["diffs"]: list[apps.metric_governance.models.DefinitionDiff]
    """

    definitions: list[MetricDefinition] = state.get("definitions", [])
    diffs: list[DefinitionDiff] = state.get("diffs", [])

    lines = [
        f"Catalogued metric definitions: {len(definitions)}",
        f"Definition diffs on file: {len(diffs)}",
    ]
    for definition in definitions:
        lines.append(
            f"- {definition.name} (owner={definition.owner}, measurement_grain={definition.measurement_grain}, "
            f"certification_status={definition.certification_status}, "
            f"source_tables={', '.join(definition.approved_source_tables)})"
        )
    for diff in diffs:
        lines.append(f"- Diff: {diff.left_name} vs {diff.right_name}. Recommended use: {diff.recommended_use}")
    return "\n".join(lines)


def ask_dashboard(question: str, state_summary: str) -> str:
    """Answer a free-form question about the current governance state,
    grounded only in `state_summary`.
    """

    key = _anthropic_api_key()
    if not key:
        return (
            "Claude isn't configured in this environment (no API key found), so I can't answer live "
            "questions right now. The rest of the app still works from deterministic governance checks."
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
                """You are the embedded assistant for a metric governance and SQL review tool. Answer using ONLY
the state data provided below. Never invent metric definitions, scores, findings, or incidents that aren't in
the data. If the data doesn't support an answer, say so plainly rather than guessing. The trust score and
findings below are deterministic outputs of the governance review -- you explain them, you never recompute or
override them. When asked whether something is safe for executive reporting, base that judgment only on the
trust score, findings, and source health provided, and name what would need to change if it isn't safe yet.
End with concise, actionable next steps grounded in the data above when the question calls for them. Keep
answers short and direct, 2-4 sentences, executive-ready tone. Do not use em dashes.

Current State:
{state_summary}""",
            ),
            ("user", "{question}"),
        ]
    )
    response = (prompt_template | model).invoke({"state_summary": state_summary, "question": question})
    content = getattr(response, "content", "")
    return content or "Claude returned no content, so rely on the deterministic governance review instead."
