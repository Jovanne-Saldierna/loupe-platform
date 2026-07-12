"""Streamlit rendering for the Data Quality Incident Triage Agent.

Owns exactly one responsibility: presentation. Every profile, finding,
incident, playbook, or impact estimate shown here was already computed by
profiling.py, checks.py, anomaly_engine.py, or remediation.py before this
module ever sees it. explanations.py and chat.py are the only LLM calls
made from here, and both are handed already-computed structured evidence
to narrate -- this file never decides whether a table is healthy, never
classifies or creates an incident, and never calls checks.py's or
anomaly_engine.py's detection functions itself (that's main.py's job, once
per app load, before render_app() is called).

main.py is responsible for assembling the `state` dict this module reads.
ui.py never imports shared.data_service or shared.metric_catalog directly.
"""

from __future__ import annotations

import base64

import pandas as pd
import streamlit as st

from apps.data_quality_triage.chat import ask_dashboard, summarize_state_for_chat
from apps.data_quality_triage.explanations import narrate_incident
from apps.data_quality_triage.incident_lifecycle import (
    LivePersistenceUnavailableError,
    acknowledge_incident,
    begin_investigation,
    mark_mitigated,
    next_allowed_statuses,
    reopen_incident,
    resolve_incident,
)
from apps.data_quality_triage.models import TableFinding
from apps.data_quality_triage.remediation import estimate_impact, suggested_playbooks_for_incident
from shared.data_service import ConcurrentModificationError, IncidentNotFoundError
from shared.incidents import InvalidTransitionError
from shared.models import Incident
from shared.persistence_transactions import ConcurrentModificationError as PersistedConcurrentModificationError
from shared.persistence_transactions import PayloadConflictError

# Every lifecycle action button below routes through one of these five
# functions -- the SAME UI-facing lifecycle service path
# tools/phase6e_ops/live_integration_validation.py calls (see that
# script's run_validation(), step 4). There is no second, ui.py-local
# implementation of a status transition anywhere in this file.
_LIFECYCLE_ACTIONS = {
    "acknowledged": (acknowledge_incident, "Acknowledge"),
    "investigating": (begin_investigation, "Begin investigating"),
    "mitigated": (mark_mitigated, "Mark mitigated"),
    "resolved": (resolve_incident, "Resolve"),
    "open": (reopen_incident, "Reopen"),
}
_LIFECYCLE_KNOWN_ERRORS = (
    InvalidTransitionError,
    IncidentNotFoundError,
    ConcurrentModificationError,
    PersistedConcurrentModificationError,
    PayloadConflictError,
    LivePersistenceUnavailableError,
)

SECTIONS = [
    ("Overview", ":material/dashboard:"),
    ("Incidents", ":material/report:"),
    ("Checks", ":material/checklist:"),
    ("Lineage", ":material/account_tree:"),
    ("Audit Trail", ":material/receipt_long:"),
]

ICONS = {
    "layout-dashboard": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>',
    "alert-triangle": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/></svg>',
    "list-checks": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>',
    "workflow": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/></svg>',
    "scroll-text": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 12h-5"/><path d="M15 8h-5"/><path d="M19 17V5a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5"/></svg>',
    "shield-check": '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></svg>',
    "activity": '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
}

# Warm/editorial palette -- deliberately distinct from Governance's dark
# teal/indigo palette, matching the original app's own visual identity
# (per the migration's read-only inspection of its src/ui.py).
BG = "#FAF6EF"
FG = "#241F1A"
SURFACE = "#FFFFFF"
SURFACE_ELEV = "#F3ECDD"
HAIRLINE = "rgba(36,31,26,0.12)"
CARD = "rgba(255,255,255,0.75)"
PRIMARY = "#C1592B"
PRIMARY_FG = "#FFF7EE"
MUTED = "#7A6F60"
ACCENT = "#3C6E5A"
RISK = "#B23B3B"
WARN = "#C08A20"
OK = "#3C6E5A"


def icon(name: str) -> str:
    return ICONS.get(name, "")


def _avatar_uri(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{b64}"


TRIAGE_AVATAR = _avatar_uri(f"""
<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
  <circle cx="20" cy="20" r="20" fill="{PRIMARY}"/>
  <path d="M20 10 L30 28 H10 Z" fill="none" stroke="{PRIMARY_FG}" stroke-width="2.5" stroke-linejoin="round"/>
  <line x1="20" y1="18" x2="20" y2="22" stroke="{PRIMARY_FG}" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="20" cy="25" r="0.8" fill="{PRIMARY_FG}"/>
</svg>
""")

USER_AVATAR = _avatar_uri(f"""
<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
  <circle cx="20" cy="20" r="20" fill="{ACCENT}"/>
  <circle cx="20" cy="16" r="6" fill="{PRIMARY_FG}"/>
  <path d="M8 33c1-7 6-11 12-11s11 4 12 11" fill="{PRIMARY_FG}"/>
</svg>
""")


def _inject_styles() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

        html, body, [class*="css"], .stApp, .stApp * {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }}
        .stApp {{ background-color: {BG}; color: {FG}; }}
        .block-container {{ padding-top: 1.6rem; padding-bottom: 2rem; max-width: 100%; padding-left: 2.6rem; padding-right: 2.6rem; }}

        .brand-row {{ display:flex; align-items:center; justify-content:space-between; margin-bottom: 1.2rem; flex-wrap: wrap; gap: .8rem; }}
        .brand-kicker {{ font-size:11px; font-weight:700; color: {MUTED}; text-transform:uppercase; letter-spacing:.14em; margin-bottom:3px; }}
        .brand-title {{ font-size:1.3rem; font-weight:800; color: {FG}; line-height:1.1; letter-spacing:-0.01em; }}
        .brand-sub {{ font-size:.84rem; color: {MUTED}; margin-top:2px; }}

        div[data-testid="stHorizontalBlock"] div.stButton > button {{
            border-radius: 999px !important; border: 1px solid {HAIRLINE} !important;
            background: {SURFACE} !important; color: {MUTED} !important; font-weight: 600 !important;
            padding: 0.55rem 1.1rem !important; font-size: 0.87rem !important;
        }}
        .nav-active button {{ background: {PRIMARY} !important; color: {PRIMARY_FG} !important; border-color: {PRIMARY} !important; font-weight: 700 !important; }}
        button[kind="primary"] {{ border-radius: 999px !important; background: {PRIMARY} !important; color: {PRIMARY_FG} !important; font-weight: 700 !important; border: none !important; }}

        .hero-shell {{ background: {SURFACE}; border: 1px solid {HAIRLINE}; border-radius: 20px; padding: 2.2rem 2.4rem; margin: 1rem 0 1.6rem 0; }}
        .hero-pill {{ display:inline-flex; align-items:center; gap:6px; padding:5px 12px; border-radius:999px; border:1px solid {HAIRLINE}; background: {SURFACE_ELEV}; font-size:11px; font-weight:600; color: {MUTED}; margin-right:8px; margin-bottom:10px; }}
        .hero-pill svg {{ color: {PRIMARY}; }}
        .hero-title {{ font-size:clamp(1.9rem,2.6vw,2.7rem); font-weight:800; margin-top:.6rem; line-height:1.08; letter-spacing:-0.02em; color: {FG}; }}
        .hero-gradient {{ color: {PRIMARY}; }}
        .hero-copy {{ margin-top:.9rem; font-size:.98rem; line-height:1.6; color: {MUTED}; max-width:70ch; }}
        .hero-note {{ margin-top:.6rem; font-size:.86rem; line-height:1.5; color: {ACCENT}; font-weight:600; max-width:70ch; }}

        .section-kicker {{ font-size:11px; font-weight:700; color: {MUTED}; text-transform:uppercase; letter-spacing:.12em; }}
        .section-title {{ font-size:1.35rem; font-weight:800; color: {FG}; margin-top:.3rem; letter-spacing:-0.01em; }}
        .section-copy {{ color: {MUTED}; font-size:.9rem; margin-top:.3rem; line-height:1.5; }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{ background: {CARD} !important; border: 1px solid {HAIRLINE} !important; border-radius: 16px !important; }}

        .metric-label {{ font-size:.74rem; font-weight:700; color: {MUTED}; text-transform:uppercase; letter-spacing:.08em; }}
        .metric-value {{ font-size:1.9rem; font-weight:800; color: {FG}; line-height:1.1; margin-top:6px; }}
        .metric-sub {{ color: {MUTED}; font-size:.84rem; margin-top:6px; line-height:1.4; }}

        .icon-badge {{ display:inline-flex; align-items:center; justify-content:center; width:38px; height:38px; border-radius:12px; border:1px solid {HAIRLINE}; background: {SURFACE_ELEV}; color: {PRIMARY}; margin-bottom: .7rem; }}

        .tech-chip {{ display:inline-flex; align-items:center; border-radius:6px; padding:4px 8px; font-size:11px; font-weight:600; border:1px solid {HAIRLINE}; background: {SURFACE_ELEV}; color: {MUTED}; margin-right:6px; margin-top:6px; }}
        .chip-ok {{ color: {OK}; border-color: rgba(60,110,90,0.3); background: rgba(60,110,90,0.08); }}
        .chip-warn {{ color: {WARN}; border-color: rgba(192,138,32,0.35); background: rgba(192,138,32,0.10); }}
        .chip-risk {{ color: {RISK}; border-color: rgba(178,59,59,0.35); background: rgba(178,59,59,0.10); }}

        .ask-panel {{ background: {SURFACE}; border:1px solid {HAIRLINE}; border-radius:16px; padding:1.2rem; height:100%; }}
        .ask-title {{ font-weight:800; font-size:1.05rem; color:{FG}; }}
        .ask-status {{ font-size:.78rem; color:{PRIMARY}; margin-top:2px; margin-bottom:1rem; display:flex; align-items:center; gap:5px; }}
        .ask-status-dot {{ width:6px; height:6px; border-radius:50%; background:{PRIMARY}; display:inline-block; }}

        [data-testid="stChatMessage"] {{ background: {SURFACE_ELEV} !important; border:1px solid {HAIRLINE} !important; border-radius: 14px !important; margin-bottom: 8px !important; }}
        .stDataFrame {{ border-radius:12px; overflow:hidden; border:1px solid {HAIRLINE}; }}
        hr.divider {{ border:none; height:1px; background: {HAIRLINE}; margin:2rem 0; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _brand_and_nav(title: str, dataset: str) -> None:
    st.markdown(
        f"""
        <div class="brand-row">
            <div>
                <div class="brand-kicker">Reliability Layer</div>
                <div class="brand-title">{title}</div>
                <div class="brand-sub">{dataset}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "active_page" not in st.session_state:
        st.session_state.active_page = "Overview"

    cols = st.columns(len(SECTIONS))
    for col, (label, mat_icon) in zip(cols, SECTIONS):
        with col:
            is_active = st.session_state.active_page == label
            wrapper_class = "nav-active" if is_active else ""
            st.markdown(f'<div class="{wrapper_class}">', unsafe_allow_html=True)
            if st.button(f"{mat_icon} {label}", key=f"nav_{label}", use_container_width=True):
                st.session_state.active_page = label
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


def _hero(data_available: bool, unavailable_reason: str | None) -> None:
    pills = [
        ("shield-check", "Deterministic detection"),
        ("activity", "Live BigQuery checks"),
    ]
    pills_html = "".join(f'<span class="hero-pill">{icon(name)} {label}</span>' for name, label in pills)
    note_html = ""
    if not data_available:
        note_html = (
            '<div class="hero-note">Live data is currently unavailable'
            + (f": {unavailable_reason}" if unavailable_reason else ".")
            + " No incidents are being fabricated to fill the gap -- see the Overview page for details.</div>"
        )
    st.markdown(
        f"""
        <div class="hero-shell">
            {pills_html}
            <div class="hero-title">Data Quality Incident <span class="hero-gradient">Triage</span> for tables leadership already depends on.</div>
            <div class="hero-copy">Detection stays deterministic. Claude explains what happened and what to check next, it never decides whether something is actually broken.</div>
            {note_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _card(label: str, value: str, sub: str = "") -> None:
    with st.container(border=True):
        st.markdown(f'<div class="metric-label">{label}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value">{value}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-sub">{sub}</div>', unsafe_allow_html=True)


def _section_head(icon_name: str, title: str, copy: str) -> None:
    st.markdown(
        f"""<div class="icon-badge">{icon(icon_name)}</div><div class="section-title">{title}</div><div class="section-copy">{copy}</div>""",
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:1.1rem'></div>", unsafe_allow_html=True)


def _severity_badge(severity: str) -> str:
    sev = severity.lower()
    cls = "chip-risk" if sev in ("high", "critical") else "chip-warn" if sev == "medium" else "chip-ok"
    return f'<span class="tech-chip {cls}">{severity.upper()}</span>'


def _finding_for(incident: Incident, findings: list[TableFinding]) -> TableFinding | None:
    """Best-effort correlation between an already-promoted Incident and the
    TableFinding that produced it, purely for richer narrative detail in
    the UI -- matched by table_id + check_type/check_name, both of which
    checks.py's build_incident_from_finding() carries over unchanged."""

    for finding in findings:
        if finding.table_id == incident.table_id and finding.check_name == incident.check_type:
            return finding
    return None


def _lifecycle_overrides() -> dict:
    return st.session_state.setdefault("lifecycle_status_overrides", {})


def _effective_status(incident: Incident) -> str:
    """The status this session should currently display for `incident` --
    either its freshly-detected status, or a locally-recorded lifecycle
    action's outcome (persisted or session-only) from earlier this
    session. Never re-derived from a background poll -- a rerun only
    happens after an explicit user action, per Streamlit's normal model."""

    override = _lifecycle_overrides().get(incident.incident_id)
    return override["status"] if override is not None else incident.status


def _apply_lifecycle_action(state: dict, incident: Incident, target_status: str, *, resolution_notes: str | None = None) -> None:
    """Apply one lifecycle transition button-click.

    If persisted mode is available (state["persistence_available"] and a
    real state["persistence_client"] resolve_persistence() already
    validated), calls the matching apps.data_quality_triage.incident_lifecycle
    function with mode="persisted" -- which itself calls
    shared.incident_persistence.record_incident_transition(). Session
    state (and therefore what re-renders after this rerun) is updated
    ONLY after that call succeeds; on failure, the prior displayed status
    is left untouched and an honest, safe error message is shown instead
    -- never a status change that didn't actually happen.

    Otherwise (persistence not available/configured), the transition is
    validated locally and recorded as session-only -- explicitly labeled
    as such wherever it's displayed, never presented as if it were
    persisted.
    """

    incident_id = incident.incident_id
    func, _ = _LIFECYCLE_ACTIONS[target_status]
    kwargs: dict = {"expected_current_status": _effective_status(incident)}
    if target_status == "resolved":
        kwargs["resolution_notes"] = resolution_notes or ""

    client = state.get("persistence_client")
    if state.get("persistence_available") and client is not None:
        kwargs["mode"] = "persisted"
        kwargs["actor"] = state.get("actor", "data_quality_triage.ui")
        kwargs["config"] = state.get("persistence_config")
    else:
        kwargs["mode"] = "constants"

    try:
        outcome = func(client, incident_id, **kwargs)
    except _LIFECYCLE_KNOWN_ERRORS:
        st.session_state[f"lifecycle_error_{incident_id}"] = (
            "Could not record this transition -- the incident's displayed "
            "status has not changed. Refresh the incident and try again."
        )
        return

    _lifecycle_overrides()[incident_id] = {"status": outcome.status, "persisted": outcome.persisted}
    st.session_state.pop(f"lifecycle_error_{incident_id}", None)


def _render_lifecycle_actions(state: dict, incident: Incident) -> None:
    effective_status = _effective_status(incident)
    override = _lifecycle_overrides().get(incident.incident_id)

    if override is not None:
        badge = "Persisted" if override["persisted"] else "Session-only (not persisted)"
        st.caption(f"Status: {effective_status} -- {badge}")
    else:
        st.caption(f"Status: {effective_status}")

    error = st.session_state.get(f"lifecycle_error_{incident.incident_id}")
    if error:
        st.error(error, icon="⚠️")

    targets = next_allowed_statuses(effective_status)
    if not targets:
        return

    cols = st.columns(len(targets))
    for col, target_status in zip(cols, targets):
        _, label = _LIFECYCLE_ACTIONS[target_status]
        with col:
            if target_status == "resolved":
                notes_key = f"resolution_notes_{incident.incident_id}"
                notes = st.text_input("Resolution notes", key=notes_key, label_visibility="collapsed", placeholder="Resolution notes")
                if st.button(label, key=f"lifecycle_{target_status}_{incident.incident_id}"):
                    _apply_lifecycle_action(state, incident, target_status, resolution_notes=notes)
                    st.rerun()
            else:
                if st.button(label, key=f"lifecycle_{target_status}_{incident.incident_id}"):
                    _apply_lifecycle_action(state, incident, target_status)
                    st.rerun()


def _render_overview(state: dict) -> None:
    _section_head(
        "layout-dashboard",
        "Reliability Overview",
        "A snapshot of this run's deterministic checks across the monitored dataset.",
    )

    incidents: list[Incident] = state.get("incidents", [])
    profiles = state.get("profiles", [])
    data_available = state.get("data_available", False)

    if not data_available:
        st.error(
            "Live BigQuery data is currently unavailable, so this run has no real check results. "
            "This page shows that honestly rather than falling back to fictional sample incidents. "
            + (state.get("unavailable_reason") or ""),
            icon="⚠️",
        )
        return

    level, impact_summary = estimate_impact(incidents)

    c1, c2, c3 = st.columns(3)
    with c1:
        _card("Tables Monitored", f"{len(profiles)}", state.get("dataset", ""))
    with c2:
        _card("Active Incidents", f"{len(incidents)}", "Detected this run")
    with c3:
        _card("Impact", level, impact_summary)

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Severity Mix</div>', unsafe_allow_html=True)
        if incidents:
            counts = {"high": 0, "medium": 0, "low": 0}
            for incident in incidents:
                counts[incident.severity] = counts.get(incident.severity, 0) + 1
            st.bar_chart(counts)
        else:
            st.markdown(f'<span style="color:{MUTED};">No incidents detected this run.</span>', unsafe_allow_html=True)

    if not state.get("persistence_available", False):
        st.info(
            "Incident lifecycle persistence (acknowledge / investigate / resolve, and historical "
            "incidents) is not connected yet -- only this run's freshly detected incidents are shown. "
            "See the Audit Trail page.",
            icon="ℹ️",
        )


def _render_incidents(state: dict) -> None:
    _section_head(
        "alert-triangle",
        "Incidents",
        "Every incident below came from a deterministic check result -- a threshold, a ratio, or a metadata fact. Claude may explain one, never classify or resolve it.",
    )

    if not state.get("data_available", False):
        st.warning("Live BigQuery data is currently unavailable, so there are no incidents to show for this run.")
        return

    incidents: list[Incident] = state.get("incidents", [])
    findings: list[TableFinding] = state.get("findings", [])
    persistence_outcomes = {o.incident_id: o for o in state.get("incident_persistence", [])}

    if not incidents:
        st.success("No active incidents detected this run.")
        return

    for incident in incidents:
        finding = _finding_for(incident, findings)
        outcome = persistence_outcomes.get(incident.incident_id)
        with st.container(border=True):
            header_cols = st.columns([3, 1])
            with header_cols[0]:
                st.markdown(
                    f'<div style="font-weight:800; font-size:1.05rem; color:{FG};">{incident.table_id} &middot; {incident.check_type}</div>',
                    unsafe_allow_html=True,
                )
            with header_cols[1]:
                st.markdown(_severity_badge(incident.severity), unsafe_allow_html=True)

            if outcome is not None and outcome.persisted:
                st.caption("Persisted to loupe_platform.incidents." + ("" if outcome.error is None else f" ({outcome.error})"))
            else:
                st.caption("Not persisted -- incident persistence is not connected in this run.")

            _render_lifecycle_actions(state, incident)

            if finding is not None:
                st.markdown(f"**Summary:** {finding.summary}")
                st.markdown(f"**Likely root cause:** {finding.likely_root_cause}")

            if incident.affected_metrics:
                st.markdown(f"**Affected metrics:** {', '.join(incident.affected_metrics)}")

            st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
            st.markdown("**Suggested next actions:**")
            for step in suggested_playbooks_for_incident(incident):
                st.markdown(f"- {step}")

            with st.expander("Ask Claude to explain this incident"):
                cache_key = f"explanation_{incident.incident_id}"
                if st.button("Explain", key=f"explain_{incident.incident_id}"):
                    st.session_state[cache_key] = narrate_incident(incident, finding)
                explanation = st.session_state.get(cache_key)
                if explanation is not None:
                    st.markdown(explanation.narrative)
                    if not explanation.used_claude:
                        st.caption("Deterministic fallback narration (no Claude API key configured).")


def _render_checks(state: dict) -> None:
    from apps.data_quality_triage.checks import GUARDRAILS_CATALOG

    _section_head(
        "list-checks",
        "Guardrails",
        "The deterministic checks this app runs today. Schema drift and query-exception detection are documented gaps, not yet implemented -- see the migration report.",
    )
    rows = [
        {"Check": c.name, "Description": c.description, "Threshold": c.threshold, "Severity": c.severity}
        for c in GUARDRAILS_CATALOG
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_lineage(state: dict) -> None:
    _section_head(
        "workflow",
        "Table Lineage",
        "Row counts, freshness, and the candidate columns each check runs against, straight from BigQuery's metadata API.",
    )
    profiles = state.get("profiles", [])
    if not profiles:
        st.info("No table profiles available for this run.")
        return
    rows = [
        {
            "Table": p.table_id,
            "Rows": p.row_count,
            "Last Modified": p.last_modified or "unknown",
            "Freshness (min)": round(p.freshness_minutes, 1) if p.freshness_minutes is not None else "unknown",
            "Primary Candidate": p.primary_candidate or "none",
            "Nullable Candidates": ", ".join(p.nullable_candidates) or "none",
            "Temporal Candidates": ", ".join(p.temporal_candidates) or "none",
        }
        for p in profiles
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_audit_trail(state: dict) -> None:
    _section_head(
        "scroll-text",
        "Audit Trail",
        "Every triage run should leave a trace of what was checked, when, and whether live data was reachable.",
    )
    st.dataframe(state.get("audit", []), use_container_width=True, hide_index=True)
    st.caption(
        "Lifecycle actions use durable transition history when persistence is available; "
        "otherwise the interface labels them session-only."
    )


def _render_ask_panel(state: dict) -> None:
    st.markdown('<div class="ask-panel">', unsafe_allow_html=True)
    st.markdown('<div class="ask-title">Hi, I\'m the Triage assistant 🛡️</div>', unsafe_allow_html=True)
    st.markdown('<div class="ask-status"><span class="ask-status-dot"></span>Ready, ask me anything</div>', unsafe_allow_html=True)

    if "ask_history" not in st.session_state:
        st.session_state.ask_history = []

    if not st.session_state.ask_history:
        with st.chat_message("assistant", avatar=TRIAGE_AVATAR):
            st.markdown(
                "I know this run's detected incidents and table health. Ask me what's degraded and I'll "
                "tell you straight -- detection itself is already decided before I ever see it."
            )

    for role, content in st.session_state.ask_history:
        avatar = TRIAGE_AVATAR if role == "assistant" else USER_AVATAR
        with st.chat_message(role, avatar=avatar):
            st.markdown(content)

    question = st.chat_input("Ask me what's going on...")
    if question:
        st.session_state.ask_history.append(("user", question))
        answer = ask_dashboard(question, summarize_state_for_chat(state))
        st.session_state.ask_history.append(("assistant", answer))
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_app(state: dict) -> None:
    """Render the full app from `state`.

    `state` is assembled by main.py and is expected to contain:
    - title, dataset, generated_at: display strings
    - profiles: list[apps.data_quality_triage.profiling.TableProfile]
    - findings: list[apps.data_quality_triage.models.TableFinding]
    - incidents: list[shared.models.Incident] -- the promoted, cross-app
      contract records this run detected
    - data_available: bool -- whether live BigQuery data was reachable
      this run. When False, every section shows an honest "unavailable"
      state instead of fabricated content -- there is no fictional
      sample-data fallback anywhere in this module.
    - unavailable_reason: Optional[str]
    - persistence_available: bool -- always False until Phase 6
    - audit: list[dict]
    """

    _inject_styles()
    _brand_and_nav(state.get("title", "Data Quality Incident Triage Agent"), state.get("dataset", ""))
    _hero(state.get("data_available", False), state.get("unavailable_reason"))

    page = st.session_state.get("active_page", "Overview")
    main_col, ask_col = st.columns([2.4, 1])

    with main_col:
        if page == "Overview":
            _render_overview(state)
        elif page == "Incidents":
            _render_incidents(state)
        elif page == "Checks":
            _render_checks(state)
        elif page == "Lineage":
            _render_lineage(state)
        else:
            _render_audit_trail(state)

    with ask_col:
        _render_ask_panel(state)
