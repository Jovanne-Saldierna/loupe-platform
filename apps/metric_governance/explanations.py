"""LLM narration of already-computed, deterministic governance evidence.

Owns exactly one responsibility: turning a SqlReviewResult (+ trust score)
or a DefinitionDiff into calm, executive-readable prose. Per the platform
rule stated across docs/*.md, "AI may explain structured evidence but may
not invent metrics, numbers, health decisions, or unsupported
conclusions" -- every function here is handed a fully-computed, already
deterministic result and is only asked to describe it. None of them
compute a score, a finding, or a diff themselves; review.py,
remediation.py, and definition_diff.py already did that before this
module is ever called.

If no Anthropic API key is configured, or the langchain_anthropic package
is unavailable, every function here degrades to a deterministic,
templated fallback string built only from the structured evidence itself
-- never a placeholder that implies the LLM ran when it did not.
"""

from __future__ import annotations

import os

from apps.metric_governance.models import DefinitionDiff, SqlReviewResult
from shared.models import TrustScoreResult


def _anthropic_api_key() -> str:
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        import streamlit as st

        return str(st.secrets.get("ANTHROPIC_API_KEY", "")).strip()
    except Exception:
        return ""


def _fallback_explanation(title: str, detail: str) -> str:
    return f"## {title}\n\n{detail}"


def _format_review_result(result: SqlReviewResult, trust: TrustScoreResult | None) -> str:
    lines = [f"Governance score: {result.score}", f"Summary: {result.summary}"]
    if trust is not None:
        lines.append(f"Trust score: {trust.score} ({trust.band}, scoring_version={trust.scoring_version})")
        if trust.override_reason:
            lines.append(f"Trust band override: {trust.override_reason}")
    if result.findings:
        lines.append("Findings:")
        for finding in result.findings:
            lines.append(f"- [{finding.severity.upper()}] {finding.category}: {finding.message}")
    if result.referenced_tables:
        lines.append(f"Referenced tables: {', '.join(result.referenced_tables)}")
    else:
        lines.append("Referenced tables: None")
    if result.recommended_next_steps:
        lines.append("Recommended next steps:")
        for step in result.recommended_next_steps:
            lines.append(f"- {step}")
    return "\n".join(lines)


def summarize_sql_review(
    sql: str,
    result: SqlReviewResult,
    approved_tables: list[str],
    trust: TrustScoreResult | None = None,
) -> str:
    """Narrate a completed SQL review result. Grounded strictly in
    `result` (and `trust`, if provided) -- never re-derives or overrides
    the score, findings, or trust band.
    """

    formatted_result = _format_review_result(result, trust)
    prompt = f"""
You are an analytics governance assistant for a single company.
Summarize the SQL review results below in a concise, executive-friendly way.
Keep the tone calm and specific. Do not invent facts, scores, or findings
beyond what is given below.

SQL:
{sql}

Approved tables:
{', '.join(sorted(set(approved_tables))) or 'None'}

Review result:
{formatted_result}

Return Markdown with:
## Review takeaway
## Main risks
## Recommended action
""".strip()

    key = _anthropic_api_key()
    if not key:
        return _fallback_explanation(
            "Review takeaway",
            "This query was reviewed deterministically against the approved catalog. The current "
            "result score and findings already show whether it is safe to use.",
        )

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate
    except Exception:
        return _fallback_explanation(
            "Review takeaway",
            "Claude is not installed in this environment, so the deterministic governance result is "
            "the source of truth.",
        )

    model = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You summarize governed analytics reviews for leadership and analytics teams. "
                "Be concise and evidence-based. Never state a score, finding, or table that was "
                "not given to you.",
            ),
            ("user", prompt),
        ]
    )
    response = (prompt_template | model).invoke({})
    return getattr(response, "content", "") or _fallback_explanation(
        "Review takeaway",
        "Claude returned no content, so rely on the deterministic governance review.",
    )


def explain_definition_diff(diff: DefinitionDiff) -> str:
    """Narrate why a DefinitionDiff matters, grounded strictly in its own
    matches/differences/recommended_use fields.

    Replaces the previous explain_metric_difference(), which returned a
    single canned sentence regardless of the actual diff content. This
    version is handed the real, already-computed DefinitionDiff (from
    definition_diff.compare_definitions()) and may only describe it.
    """

    matches_text = "\n".join(f"- {m}" for m in diff.matches) or "- None recorded."
    differences_text = "\n".join(f"- {d}" for d in diff.differences) or "- None recorded."

    key = _anthropic_api_key()
    if not key:
        return _fallback_explanation(
            "Why this matters",
            f"{diff.left_name} and {diff.right_name} were compared on grain, source tables, "
            f"certification status, ownership, freshness, and formula. {diff.recommended_use}",
        )

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.prompts import ChatPromptTemplate
    except Exception:
        return _fallback_explanation(
            "Why this matters",
            "Claude is not installed in this environment, so the deterministic diff result above is "
            "the source of truth.",
        )

    prompt = f"""
You are an analytics governance assistant. Explain, in 2-4 sentences, why the
comparison below matters to someone deciding which metric to trust. Use ONLY
the matches and differences listed. Do not invent additional similarities or
differences, and do not restate the recommended use verbatim -- explain the
reasoning behind it.

Comparing: {diff.left_name} vs {diff.right_name}

Where they match:
{matches_text}

Where they differ:
{differences_text}

Recommended use (already decided, for context only):
{diff.recommended_use}
""".strip()

    model = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
    prompt_template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You explain governed metric-definition comparisons. Be concise and evidence-based. "
                "Never state a similarity or difference that was not given to you.",
            ),
            ("user", prompt),
        ]
    )
    response = (prompt_template | model).invoke({})
    return getattr(response, "content", "") or _fallback_explanation(
        "Why this matters",
        "Claude returned no content, so rely on the deterministic comparison above.",
    )
