from __future__ import annotations

from api.models import GovernanceHelperRequest
from apps.metric_governance.chat import ask_dashboard


def _summarize_review_for_helper(payload: GovernanceHelperRequest) -> str:
    """Flatten the already-computed review context into plain text for
    ask_dashboard()'s grounding prompt. Every line below is copied
    verbatim from a field the deterministic review (build_governance_review)
    already produced and the client sent back unchanged -- this function
    adds no new facts, only formatting. AI narration built from this text
    can explain the score/findings but cannot alter or re-derive them."""

    lines = [
        f"Metric: {payload.metric.name} (version={payload.metric.version}, "
        f"certification_status={payload.metric.certification_status}, "
        f"measurement_grain={payload.metric.measurement_grain})",
        f"Submitted SQL:\n{payload.sql}",
        f"Deterministic review score: {payload.review_score}/100. Summary: {payload.summary}",
        f"Trust score: {payload.trust_score} (band={payload.trust_band}).",
    ]

    if payload.override_reason:
        lines.append(f"Trust score override reason: {payload.override_reason}")

    if payload.trust_factors:
        lines.append("Trust score factors:")
        for factor in payload.trust_factors:
            lines.append(f"- {factor.name}: {factor.points} points -- {factor.reason}")
    else:
        lines.append("Trust score factors: none recorded.")

    if payload.findings:
        lines.append("Findings from the deterministic SQL review:")
        for finding in payload.findings:
            lines.append(f"- [{finding.severity}] {finding.category}: {finding.message}")
    else:
        lines.append("Findings from the deterministic SQL review: none.")

    if payload.recommended_next_steps:
        lines.append("Recommended next steps already surfaced by the review:")
        for step in payload.recommended_next_steps:
            lines.append(f"- {step}")
    else:
        lines.append("Recommended next steps already surfaced by the review: none.")

    lines.append(
        "Referenced tables in the SQL: " + (", ".join(payload.referenced_tables) or "none detected")
    )
    lines.append(f"Source health for this metric's approved tables: {payload.source_health}")

    if payload.active_incident_ids:
        lines.append(
            "Active data-quality incidents on this metric's source tables: "
            + ", ".join(payload.active_incident_ids)
        )
    else:
        lines.append("Active data-quality incidents on this metric's source tables: none.")

    if payload.downstream_assets:
        lines.append("Downstream dashboards/reports on file for this metric: " + ", ".join(payload.downstream_assets))

    if payload.change_risk:
        lines.append("Definition-change risk categories from the deterministic review:")
        for item in payload.change_risk:
            lines.append(f"- {item.category} ({item.status}): {item.detail}")

    if payload.recommendations:
        lines.append("Governance recommendations already surfaced outside this chat:")
        for rec in payload.recommendations:
            lines.append(f"- [{rec.priority}] {rec.action}: {rec.rationale}")

    if payload.completeness:
        lines.append("Governance completeness checks:")
        for check in payload.completeness:
            lines.append(f"- {'PASS' if check.passed else 'FAIL'} {check.label}: {check.detail}")

    return "\n".join(lines)


def answer_governance_question(payload: GovernanceHelperRequest) -> str:
    """Answer a Governance SQL Review question, grounded only in the
    review context the caller already has (never re-queries the
    warehouse or the catalog -- see _summarize_review_for_helper).
    Delegates to apps.metric_governance.chat.ask_dashboard, the same
    evidence-only narration boundary the Streamlit app uses, so this adds
    no new "explain from context, don't invent" contract of its own."""

    summary = _summarize_review_for_helper(payload)
    return ask_dashboard(payload.question, summary)
