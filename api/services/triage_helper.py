from __future__ import annotations

import os

from api.models import TriageHelperRequest
from apps.data_quality_triage.chat import ask_dashboard

# Mirrors apps.data_quality_triage.chat's own model name and
# ANTHROPIC_API_KEY check (that module's _anthropic_api_key() is private
# and Streamlit-secrets-aware, so this is a lightweight, env-only
# duplicate of the same check -- not a second source of truth for
# WHETHER a key is configured, just for reporting to the caller which
# model, if any, actually produced the answer). Never invented: if no key
# is configured, model_used() returns None and the route surfaces that
# honestly rather than naming a model that didn't run.
MODEL_NAME = "claude-sonnet-4-6"


def model_used() -> str | None:
    return MODEL_NAME if os.getenv("ANTHROPIC_API_KEY", "").strip() else None


def _summarize_incident_for_helper(payload: TriageHelperRequest) -> str:
    """Flatten the currently selected incident into plain text for
    ask_dashboard()'s grounding prompt. Every line below is copied
    verbatim from a field the deterministic warehouse-health build
    (build_warehouse_health) already produced and the client sent back
    unchanged -- this function adds no new facts, only formatting. AI
    narration built from this text can explain the incident but cannot
    alter detection, severity, or status."""

    lines = [
        f"Incident: {payload.incident_id}",
        f"Source table: {payload.table_id}",
        f"Check type: {payload.check_type}",
        f"Severity: {payload.severity}",
        f"Status: {payload.status}",
        f"Detected at: {payload.created_at}",
        f"Owner: {payload.owner or 'unassigned'}",
    ]

    if payload.active_incident_count is not None:
        lines.append(f"Active incidents currently open on this table: {payload.active_incident_count}")

    if payload.observed_value is not None:
        lines.append(f"Observed value: {payload.observed_value}")
    if payload.expected_value is not None:
        lines.append(f"Expected value: {payload.expected_value}")
    if payload.observed_value is not None and payload.expected_value is not None:
        lines.append(f"Difference (observed - expected): {payload.observed_value - payload.expected_value}")

    lines.append(
        "Affected metrics named on the incident record: "
        + (", ".join(payload.affected_metrics) if payload.affected_metrics else "none recorded")
    )
    lines.append(
        "Governed catalog metrics whose approved_source_tables include this table: "
        + (", ".join(payload.governed_metric_names) if payload.governed_metric_names else "none")
    )

    return "\n".join(lines)


def answer_triage_question(payload: TriageHelperRequest) -> str:
    """Answer a Triage Source Health question, grounded only in the
    incident context the caller already has (never re-queries the
    warehouse -- see _summarize_incident_for_helper). Delegates to
    apps.data_quality_triage.chat.ask_dashboard, the same evidence-only
    narration boundary the Streamlit app uses."""

    summary = _summarize_incident_for_helper(payload)
    return ask_dashboard(payload.question, summary)
