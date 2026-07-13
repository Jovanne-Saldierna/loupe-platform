from __future__ import annotations

import os
import re

from api.models import SqlCheck, TriagePlaybookRequest, TriagePlaybookResponse
from apps.data_quality_triage.chat import ask_dashboard
from apps.data_quality_triage.sql_checks import suggested_debugging_steps, suggested_sql_checks

# See api/services/triage_helper.py's own MODEL_NAME/model_used() for why
# this check is duplicated here rather than imported from
# apps.data_quality_triage.chat (whose _anthropic_api_key() is private).
MODEL_NAME = "claude-sonnet-4-6"


def model_used() -> str | None:
    return MODEL_NAME if os.getenv("ANTHROPIC_API_KEY", "").strip() else None


_UNKNOWN_ROOT_CAUSE = "Unknown -- the supplied incident context isn't enough to identify a likely root cause."
_NOT_CONFIGURED_IMPACT = (
    "Claude isn't configured in this environment (no API key found), so an AI-narrated impact summary "
    "isn't available. Rely on the deterministic incident fields and debugging steps below."
)
_FALLBACK_NEXT_ACTION = "Review the debugging steps and suggested SQL below, then investigate the affected table directly."

_PLAYBOOK_QUESTION = (
    "Based only on the incident data above, respond in exactly this three-line format with no other "
    "commentary, no markdown, and no extra lines:\n"
    "ROOT CAUSE: <your best-guess likely root cause in 1-2 sentences, grounded only in the data above, "
    "or exactly 'Unknown -- insufficient data' if the data doesn't support a guess>\n"
    "IMPACT: <1-2 sentence summary of the business/operational impact, grounded only in the data above>\n"
    "NEXT ACTION: <one concise, concrete next action a data engineer should take right now>"
)

_LINE_PATTERN = re.compile(r"^(ROOT CAUSE|IMPACT|NEXT ACTION):\s*(.*)$", re.IGNORECASE)


def _summarize_incident_for_playbook(payload: TriagePlaybookRequest) -> str:
    """Flatten the incident + lineage context already on the Triage screen
    into plain text for ask_dashboard()'s grounding prompt. Every line is
    copied verbatim from a field the deterministic warehouse-health build
    already produced and the client sent back unchanged -- this function
    adds no new facts, only formatting. Mirrors
    api/services/triage_helper.py's _summarize_incident_for_helper(), plus
    the lineage/downstream-asset facts a playbook additionally needs."""

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
    if payload.source_health:
        lines.append(f"Source health for this table: {payload.source_health}")

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
    lines.append(
        "Downstream dashboards/reports fed by those governed metrics: "
        + (", ".join(payload.downstream_assets) if payload.downstream_assets else "none on file")
    )

    return "\n".join(lines)


def _parse_narration(raw: str) -> tuple[str, str, str]:
    """Parse the strict ROOT CAUSE / IMPACT / NEXT ACTION format the
    playbook prompt asks for. If the model (or the no-API-key fallback
    text) doesn't match that format, degrade honestly: the raw text is
    kept as the impact summary (so nothing the model actually said is
    thrown away), and root cause / next action fall back to explicit
    "unknown" / "see debugging steps" text -- never a fabricated guess."""

    root_cause = None
    impact = None
    next_action = None
    for line in raw.splitlines():
        match = _LINE_PATTERN.match(line.strip())
        if not match:
            continue
        label, value = match.group(1).upper(), match.group(2).strip()
        if label == "ROOT CAUSE":
            root_cause = value or None
        elif label == "IMPACT":
            impact = value or None
        elif label == "NEXT ACTION":
            next_action = value or None

    if root_cause is None and impact is None and next_action is None:
        # Nothing matched the expected format at all -- most likely the
        # deterministic "Claude isn't configured/installed" fallback text
        # from ask_dashboard(), or an unexpected free-form reply. Surface
        # the raw text as the impact summary so it's never silently
        # discarded, and say plainly that the other two are unknown.
        return _UNKNOWN_ROOT_CAUSE, raw.strip() or _NOT_CONFIGURED_IMPACT, _FALLBACK_NEXT_ACTION

    return (
        root_cause or _UNKNOWN_ROOT_CAUSE,
        impact or _NOT_CONFIGURED_IMPACT,
        next_action or _FALLBACK_NEXT_ACTION,
    )


def generate_triage_playbook(payload: TriagePlaybookRequest) -> TriagePlaybookResponse:
    """Generate a grounded triage playbook for one incident.

    Architecture split (per the product thesis: "AI does not decide
    whether data is broken"):
      - debugging_steps, sql_checks, owner_recommendation are computed
        entirely deterministically from check_type/table_id/owner (see
        apps.data_quality_triage.sql_checks) -- no AI call, no chance of
        drifting from the actual incident.
      - likely_root_cause, impact_summary, next_action are AI narration,
        grounded ONLY in the incident/lineage context already sent by the
        client (see _summarize_incident_for_playbook), via the same
        ask_dashboard() evidence-only boundary apps.data_quality_triage.chat
        already enforces for the helper panel. If Claude is unavailable,
        this narration degrades to the explicit "unknown"/"not configured"
        text above -- never a fabricated guess.
    """

    debugging_steps = suggested_debugging_steps(payload.check_type, payload.table_id)
    sql_checks = [
        SqlCheck(title=check.title, purpose=check.purpose, sql=check.sql)
        for check in suggested_sql_checks(payload.check_type, payload.table_id)
    ]
    owner_recommendation = (
        f"Owner on record: {payload.owner}. Notify them directly."
        if payload.owner
        else "No owner recorded for this table -- escalate to the data platform on-call rotation."
    )

    summary = _summarize_incident_for_playbook(payload)
    raw_narration = ask_dashboard(_PLAYBOOK_QUESTION, summary)
    root_cause, impact_summary, next_action = _parse_narration(raw_narration)

    return TriagePlaybookResponse(
        likely_root_cause=root_cause,
        impact_summary=impact_summary,
        affected_downstream_assets=payload.downstream_assets,
        affected_governed_metrics=payload.governed_metric_names,
        debugging_steps=debugging_steps,
        sql_checks=sql_checks,
        owner_recommendation=owner_recommendation,
        next_action=next_action,
        model=model_used(),
    )
