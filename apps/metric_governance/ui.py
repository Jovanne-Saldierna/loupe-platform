"""Streamlit rendering for Metric Governance Copilot.

Owns exactly one responsibility: presentation. Every number, finding,
score, or diff shown here was already computed by review.py,
remediation.py, definition_diff.py, or shared.trust_scoring before this
module ever sees it -- this file imports those functions and calls them
from inside button/interaction handlers (the idiomatic Streamlit pattern),
but never computes a score or a finding itself. explanations.py and
chat.py are the only LLM calls made from here, and both are handed
already-computed structured evidence to narrate, never asked to invent it.

main.py is responsible for assembling the `state` dict this module reads
(from shared.metric_catalog, shared.data_service, and
definition_diff.find_definition_diff_pairs) -- ui.py never imports
shared.metric_catalog or shared.data_service directly for anything other
than the type hints it needs.

The Definition Diff page renders two distinct things, and keeps them
visually and semantically separate on purpose:

- state["diffs"]: pairs main.py already decided ARE alternate versions
  or explicitly curated comparisons of the SAME metric (see
  definition_diff.find_definition_diff_pairs()). Framed as a possible
  definition mismatch worth reconciling.
- a user-driven "Compare Any Two Metrics" section built entirely in this
  file, which lets someone pick any two catalog entries and calls
  definition_diff.compare_definitions() directly. This is explicitly
  labeled a cross-metric, informational comparison -- it never implies
  the two metrics are supposed to be the same thing.
"""

from __future__ import annotations

import base64

import pandas as pd
import streamlit as st

from apps.metric_governance.chat import ask_dashboard, summarize_state_for_chat
from apps.metric_governance.definition_diff import compare_definitions
from apps.metric_governance.explanations import explain_definition_diff, summarize_sql_review
from apps.metric_governance.remediation import suggested_playbooks_for_review, trust_score_inputs_from_review
from apps.metric_governance.review import review_sql
from shared.trust_scoring import compute_trust_score

SECTIONS = [
    ("Overview", ":material/dashboard:"),
    ("Catalog", ":material/database:"),
    ("Definition Diff", ":material/compare_arrows:"),
    ("SQL Review", ":material/terminal:"),
    ("Lineage", ":material/account_tree:"),
    ("Audit Trail", ":material/receipt_long:"),
]

ICONS = {
    "layout-dashboard": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>',
    "database": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
    "git-compare": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/></svg>',
    "terminal": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/></svg>',
    "workflow": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/></svg>',
    "scroll-text": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 12h-5"/><path d="M15 8h-5"/><path d="M19 17V5a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h1"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5"/></svg>',
    "shield-check": '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></svg>',
    "sparkles": '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/></svg>',
}

BG = "#0B0F17"
FG = "#FBFBFB"
SURFACE = "#141A24"
SURFACE_ELEV = "#1B2230"
HAIRLINE = "rgba(255,255,255,0.10)"
CARD = "rgba(27,34,48,0.55)"
PRIMARY = "#5FC9A8"
PRIMARY_FG = "#0B1F1A"
MUTED = "#9AA7BD"
ACCENT = "#7C7FEA"
GREEN_TINT = "rgba(95,201,168,0.08)"
AMBER_TINT = "rgba(224,195,107,0.08)"


def icon(name: str) -> str:
    return ICONS.get(name, "")


def _avatar_uri(svg: str) -> str:
    """Encode a raw SVG as a data URI, since st.chat_message's avatar
    param accepts a URL string (a data URI qualifies) but not raw HTML."""
    b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{b64}"


LOUPE_AVATAR = _avatar_uri(f"""
<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
  <circle cx="20" cy="20" r="20" fill="{PRIMARY}"/>
  <circle cx="17" cy="17" r="7" fill="none" stroke="{PRIMARY_FG}" stroke-width="2.5"/>
  <line x1="22" y1="22" x2="28" y2="28" stroke="{PRIMARY_FG}" stroke-width="2.5" stroke-linecap="round"/>
</svg>
""")

USER_AVATAR = _avatar_uri(f"""
<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
  <circle cx="20" cy="20" r="20" fill="{ACCENT}"/>
  <circle cx="20" cy="16" r="6" fill="{FG}"/>
  <path d="M8 33c1-7 6-11 12-11s11 4 12 11" fill="{FG}"/>
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

        .stApp {{
            background-color: {BG};
            background-image:
                radial-gradient(60rem 40rem at 10% -10%, rgba(95,201,168,0.10), transparent 60%),
                radial-gradient(50rem 40rem at 100% 0%, rgba(124,127,234,0.10), transparent 60%);
            background-attachment: fixed;
            color: {FG};
        }}
        .block-container {{ padding-top: 1.6rem; padding-bottom: 2rem; max-width: 100%; padding-left: 2.6rem; padding-right: 2.6rem; }}

        .brand-row {{ display:flex; align-items:center; justify-content:space-between; margin-bottom: 1.2rem; flex-wrap: wrap; gap: .8rem; }}
        .brand-kicker {{ font-size:11px; font-weight:600; color: {MUTED}; text-transform:uppercase; letter-spacing:.14em; margin-bottom:3px; }}
        .brand-title {{ font-size:1.3rem; font-weight:800; color: {FG}; line-height:1.1; letter-spacing:-0.01em; }}
        .brand-sub {{ font-size:.84rem; color: {MUTED}; margin-top:2px; }}

        div[data-testid="stHorizontalBlock"] div.stButton > button {{
            border-radius: 999px !important;
            border: 1px solid {HAIRLINE} !important;
            background: rgba(20,26,36,0.55) !important;
            color: {MUTED} !important;
            font-weight: 600 !important;
            padding: 0.55rem 1.1rem !important;
            font-size: 0.87rem !important;
            transition: all .2s ease;
            backdrop-filter: blur(10px);
        }}
        div[data-testid="stHorizontalBlock"] div.stButton > button:hover {{
            border-color: rgba(255,255,255,0.25) !important;
            color: {FG} !important;
        }}
        .nav-active button {{
            background: {PRIMARY} !important;
            color: {PRIMARY_FG} !important;
            border-color: {PRIMARY} !important;
            font-weight: 700 !important;
        }}

        button[kind="primary"] {{
            border-radius: 999px !important;
            background: {PRIMARY} !important;
            color: {PRIMARY_FG} !important;
            font-weight: 700 !important;
            border: none !important;
            box-shadow: 0 8px 24px -8px rgba(95,201,168,0.45) !important;
        }}

        .hero-shell {{
            background: rgba(20,26,36,0.55);
            backdrop-filter: blur(16px) saturate(150%);
            border: 1px solid {HAIRLINE};
            border-radius: 20px;
            padding: 2.2rem 2.4rem;
            margin: 1rem 0 1.6rem 0;
            box-shadow: 0 10px 40px -20px rgba(0,0,0,.6);
        }}
        .hero-pill {{
            display:inline-flex; align-items:center; gap:6px; padding:5px 12px; border-radius:999px;
            border:1px solid {HAIRLINE}; background: rgba(20,26,36,0.6);
            font-size:11px; font-weight:600; color: {MUTED}; margin-right:8px; margin-bottom:10px;
        }}
        .hero-pill svg {{ color: {PRIMARY}; }}
        .hero-title {{ font-size:clamp(1.9rem,2.6vw,2.7rem); font-weight:800; margin-top:.6rem; line-height:1.08; letter-spacing:-0.02em; color: {FG}; }}
        .hero-gradient {{ background: linear-gradient(90deg, {PRIMARY} 0%, {ACCENT} 100%); -webkit-background-clip: text; background-clip: text; color: transparent; }}
        .hero-copy {{ margin-top:.9rem; font-size:.98rem; line-height:1.6; color: {MUTED}; max-width:68ch; }}

        .section-kicker {{ font-size:11px; font-weight:700; color: {MUTED}; text-transform:uppercase; letter-spacing:.12em; }}
        .section-title {{ font-size:1.35rem; font-weight:800; color: {FG}; margin-top:.3rem; letter-spacing:-0.01em; }}
        .section-copy {{ color: {MUTED}; font-size:.9rem; margin-top:.3rem; line-height:1.5; }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: {CARD} !important;
            backdrop-filter: blur(16px) saturate(150%);
            border: 1px solid {HAIRLINE} !important;
            border-radius: 16px !important;
            box-shadow: 0 1px 0 0 rgba(255,255,255,0.05) inset, 0 10px 40px -20px rgba(0,0,0,.6);
        }}

        .metric-label {{ font-size:.74rem; font-weight:700; color: {MUTED}; text-transform:uppercase; letter-spacing:.08em; }}
        .metric-value {{ font-size:1.9rem; font-weight:800; color: {FG}; line-height:1.1; margin-top:6px; }}
        .metric-sub {{ color: {MUTED}; font-size:.84rem; margin-top:6px; line-height:1.4; opacity:.85; }}

        .icon-badge {{
            display:inline-flex; align-items:center; justify-content:center;
            width:38px; height:38px; border-radius:12px;
            border:1px solid {HAIRLINE}; background: rgba(27,34,48,0.6);
            color: {PRIMARY}; margin-bottom: .7rem;
        }}

        .tech-chip {{
            display:inline-flex; align-items:center; border-radius:6px; padding:4px 8px;
            font-size:11px; font-weight:500; border:1px solid {HAIRLINE};
            background: rgba(20,26,36,0.5); color: {MUTED}; margin-right:6px; margin-top:6px;
        }}
        .chip-ok {{ color: {PRIMARY}; border-color: rgba(95,201,168,0.3); background: rgba(95,201,168,0.1); }}
        .chip-warn {{ color: #E0C36B; border-color: rgba(224,195,107,0.3); background: rgba(224,195,107,0.1); }}
        .chip-risk {{ color: #E08A8A; border-color: rgba(224,138,138,0.3); background: rgba(224,138,138,0.1); }}

        .glance-row {{ display:flex; justify-content:space-between; align-items:center; padding:.55rem 0; border-bottom:1px solid {HAIRLINE}; }}
        .glance-row:last-child {{ border-bottom:none; }}
        .glance-name {{ font-weight:600; color:{FG}; font-size:.92rem; }}
        .glance-meta {{ color:{MUTED}; font-size:.82rem; }}

        div[data-baseweb="select"] > div {{ border-radius:10px !important; border-color: {HAIRLINE} !important; background: rgba(20,26,36,0.5) !important; color: {FG} !important; }}
        [data-testid="stTextArea"] textarea {{
            background: {SURFACE} !important; border-color: {HAIRLINE} !important;
            color: {FG} !important; font-family:'JetBrains Mono',monospace !important; border-radius:12px !important;
        }}
        .stCodeBlock, code, pre {{ font-family:'JetBrains Mono',monospace !important; }}
        .stDataFrame {{ border-radius:12px; overflow:hidden; border:1px solid {HAIRLINE}; }}

        .ask-panel {{ background: {SURFACE}; border:1px solid {HAIRLINE}; border-radius:16px; padding:1.2rem; height:100%; }}
        .ask-title {{ font-weight:800; font-size:1.05rem; color:{FG}; }}
        .ask-status {{ font-size:.78rem; color:{PRIMARY}; margin-top:2px; margin-bottom:1rem; display:flex; align-items:center; gap:5px; }}
        .ask-status-dot {{ width:6px; height:6px; border-radius:50%; background:{PRIMARY}; display:inline-block; }}

        [data-testid="stChatMessage"] {{ background: {SURFACE_ELEV} !important; border:1px solid {HAIRLINE} !important; border-radius: 14px !important; margin-bottom: 8px !important; }}
        [data-testid="stChatMessage"] p {{ color: {FG} !important; }}
        div[data-testid="stChatInput"] textarea {{
            background: {SURFACE_ELEV} !important;
            color: {FG} !important;
            border: 1px solid {HAIRLINE} !important;
            border-radius: 16px !important;
        }}
        div[data-testid="stChatInput"] {{ background: transparent !important; }}
        div[data-testid="stChatInput"] > div {{ background: transparent !important; border: none !important; }}

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
                <div class="brand-kicker">Truth Layer</div>
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


def _hero() -> None:
    pills = [("shield-check", "Semantic layer truth"), ("terminal", "Deterministic SQL review"), ("sparkles", "Claude-assisted narration")]
    pills_html = "".join(f'<span class="hero-pill">{icon(name)} {label}</span>' for name, label in pills)
    st.markdown(
        f"""
        <div class="hero-shell">
            {pills_html}
            <div class="hero-title">Metric Governance <span class="hero-gradient">Copilot</span> for teams that need to trust a number before it reaches leadership.</div>
            <div class="hero-copy">Compare KPI definitions, validate SQL against approved logic, and make lineage and auditability visible before a number reaches leadership.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _card(label: str, value: str, sub: str = "") -> None:
    with st.container(border=True):
        st.markdown(f'<div class="metric-label">{label}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value">{value}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-sub">{sub}</div>', unsafe_allow_html=True)


def _list_card(title: str, items: list[str], tint: str = None) -> None:
    bg_style = f"background:{tint};" if tint else ""
    with st.container(border=True):
        if tint:
            st.markdown(f'<div style="margin:-1rem; padding:1rem; border-radius:12px; {bg_style}">', unsafe_allow_html=True)
        st.markdown(f'<div style="font-weight:800; font-size:1rem; margin-bottom:10px; color:{FG};">{title}</div>', unsafe_allow_html=True)
        for item in items:
            st.markdown(f'<div style="color:{MUTED}; margin-bottom:4px;">• {item}</div>', unsafe_allow_html=True)
        if tint:
            st.markdown('</div>', unsafe_allow_html=True)


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


def _render_ask_panel(state: dict) -> None:
    st.markdown('<div class="ask-panel">', unsafe_allow_html=True)
    st.markdown('<div class="ask-title">Hi, I\'m Loupe 🔍</div>', unsafe_allow_html=True)
    st.markdown('<div class="ask-status"><span class="ask-status-dot"></span>Ready, ask me anything</div>', unsafe_allow_html=True)

    if "ask_history" not in st.session_state:
        st.session_state.ask_history = []

    if not st.session_state.ask_history:
        with st.chat_message("assistant", avatar=LOUPE_AVATAR):
            st.markdown("I know every catalogued metric and definition diff in this workspace. Ask me what's safe to trust and I'll tell you straight.")

    for role, content in st.session_state.ask_history:
        avatar = LOUPE_AVATAR if role == "assistant" else USER_AVATAR
        with st.chat_message(role, avatar=avatar):
            st.markdown(content)

    question = st.chat_input("Ask me anything about what's going on...")
    if question:
        st.session_state.ask_history.append(("user", question))
        answer = ask_dashboard(question, summarize_state_for_chat(state))
        st.session_state.ask_history.append(("assistant", answer))
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


def render_app(state: dict) -> None:
    """Render the full app from `state`.

    `state` is assembled by main.py and is expected to contain:
    - title, dataset: display strings
    - definitions: list[shared.models.MetricDefinition] -- real catalog
      entries from shared.metric_catalog, never fictional sample data
    - diffs: list[apps.metric_governance.models.DefinitionDiff] -- from
      definition_diff.find_definition_diff_pairs() + compare_definitions()
      (alternate versions / explicit comparisons only, may be empty)
    - catalog_tables: list[str] -- union of all definitions'
      approved_source_tables, used as the SQL Review page's approved list
    - audit: list[dict] -- placeholder rows until shared/audit.py is
      wired to a live loupe_platform.audit_events table (Phase 6)
    """

    _inject_styles()
    _brand_and_nav(state.get("title", "Metric Governance Copilot"), state.get("dataset", ""))
    _hero()

    page = st.session_state.get("active_page", "Overview")
    main_col, ask_col = st.columns([2.4, 1])

    with main_col:
        if page == "Overview":
            defs = state.get("definitions", [])
            diffs = state.get("diffs", [])
            _section_head("layout-dashboard", "Governance Snapshot", "The state of the catalog right now.")

            c1, c2, c3 = st.columns(3)
            with c1: _card("Catalogued Metrics", f"{len(defs)}", "Definitions in shared.metric_catalog")
            with c2: _card("Definition Diffs", f"{len(diffs)}", "Where similar metrics diverge")
            with c3: _card("Approved Tables", f"{len(state.get('catalog_tables', []))}", "Allowed sources for governed SQL")

            if defs:
                st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:6px; color:{FG};">Metrics at a Glance</div>', unsafe_allow_html=True)
                    for d in defs:
                        st.markdown(
                            f"""<div class="glance-row"><span class="glance-name">{d.name}</span>
                            <span class="glance-meta">{d.owner} &middot; {d.measurement_grain} &middot; {d.certification_status}</span></div>""",
                            unsafe_allow_html=True,
                        )

            st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)
            st.markdown('<div class="section-kicker">Trust / Confidence Posture</div>', unsafe_allow_html=True)
            st.markdown('<div class="section-copy">Use this workspace to decide whether a query is safe enough to trust, needs steward review, or should be blocked until the definition is reconciled.</div>', unsafe_allow_html=True)
            st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
            cc1, cc2, cc3 = st.columns(3)
            with cc1: _card("High Confidence", "high_trust", "Matches approved grain, sources, and intent")
            with cc2: _card("Moderate Confidence", "review_required", "Small governance gaps or ambiguous references")
            with cc3: _card("Needs Review", "do_not_rely", "Material mismatch, active incident, or high-severity finding")

            st.markdown("<div style='height:1.4rem'></div>", unsafe_allow_html=True)
            st.markdown('<div class="section-kicker">Governance Posture</div>', unsafe_allow_html=True)
            st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
            left, right = st.columns(2)
            with left:
                _list_card("Source of Truth", [
                    "Catalogued definitions in shared.metric_catalog are the only version that should reach leadership.",
                    "Metric grain, freshness, and source tables are documented up front.",
                    "SQL is reviewed against the catalog before reuse.",
                ])
            with right:
                _list_card("Trust Controls", [
                    "Definition Diff shows where alternate versions or curated comparisons of the same metric intentionally differ.",
                    "Lineage makes the path from raw tables to dashboards explicit.",
                    "Claude can narrate the deterministic review when the key is present.",
                ])

        elif page == "Catalog":
            _section_head("database", "Metric Catalog", "Each metric records owner, grain, sources, formula, and certification status so the team can tell whether a query is truly the same number. All entries are shown with their real certification_status -- nothing here is presented as certified until it has been formally reviewed.")

            if state.get("catalog_unavailable"):
                st.error(
                    "The persisted metric catalog is unavailable: "
                    + (state.get("catalog_unavailable_reason") or "no reason given")
                    + ". Catalog data is not shown -- this is never silently replaced with constants.",
                    icon="⚠️",
                )

            definition_trust = state.get("definition_trust", {})

            for definition in state.get("definitions", []):
                with st.container(border=True):
                    left, right = st.columns([3, 1])
                    with left:
                        st.markdown(f"#### {definition.name}")
                        st.write(f"**Owner:** {definition.owner}")
                        st.write(f"**Certification status:** {definition.certification_status}")
                        st.write(f"**Measurement grain:** {definition.measurement_grain}")
                        st.write(f"**Freshness expectation:** {definition.freshness_expectation}")
                        st.write(f"**Version:** {definition.version}")
                        st.write(f"**Description:** {definition.description}")
                    with right:
                        with st.container(border=True):
                            st.write("**Source tables**")
                            for table in definition.approved_source_tables:
                                st.markdown(f"- {table}")
                            st.write("**Dashboards**")
                            for dash in definition.downstream_dashboards:
                                st.markdown(f"- {dash}")
                    st.markdown("**Formula**")
                    st.code(definition.formula, language="text")

                    dt = definition_trust.get(definition.name)
                    if dt is not None:
                        st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
                        st.markdown(f'<div style="font-weight:800; color:{FG};">Trust score: {dt.trust.score} ({dt.trust.band}, v{dt.trust.scoring_version})</div>', unsafe_allow_html=True)
                        if dt.trust.override_reason:
                            st.warning(dt.trust.override_reason)
                        for factor in dt.trust.factors:
                            sign = "+" if factor.points >= 0 else ""
                            st.markdown(f"- **{factor.name}** ({sign}{factor.points}): {factor.reason}")
                        if dt.evidence.table_health:
                            st.markdown("**Source evidence**")
                            for health in dt.evidence.table_health:
                                st.markdown(f"- {health.table_id}: {health.status} ({len(health.active_incident_ids)} active incident(s))")
                        if dt.evidence.active_incidents:
                            st.markdown("**Active incidents affecting this score**")
                            for incident in dt.evidence.active_incidents:
                                st.markdown(f"- `{incident.incident_id}` ({incident.severity}, {incident.status}): {incident.check_type} on {incident.table_id}")

                    client = state.get("client")
                    if client is not None:
                        with st.expander(f"Certify a new version of {definition.name}"):
                            with st.form(key=f"certify_form_{definition.name}"):
                                new_version = st.text_input("New version id", key=f"certify_version_{definition.name}")
                                reviewer = st.text_input("Reviewer identity", key=f"certify_reviewer_{definition.name}")
                                created_by = st.text_input("Created-by identity", value=definition.owner, key=f"certify_created_by_{definition.name}")
                                validation_evidence = st.text_area("Validation evidence", key=f"certify_evidence_{definition.name}")
                                change_reason = st.text_input("Change reason", key=f"certify_reason_{definition.name}")
                                submitted = st.form_submit_button("Certify")
                            if submitted:
                                from datetime import datetime, timezone

                                from apps.metric_governance.persistence import certify_definition

                                reviewed_at = datetime.now(tz=timezone.utc).isoformat()
                                try:
                                    result = certify_definition(
                                        client,
                                        name=definition.name,
                                        new_version=new_version,
                                        expected_current_version=definition.version,
                                        description=definition.description,
                                        formula=definition.formula,
                                        measurement_grain=definition.measurement_grain,
                                        freshness_expectation=definition.freshness_expectation,
                                        approved_source_tables=definition.approved_source_tables,
                                        created_by=created_by,
                                        reviewer=reviewer,
                                        validation_evidence=validation_evidence,
                                        reviewed_at=reviewed_at,
                                        change_reason=change_reason,
                                        event_id=f"metric_certified.{definition.name}.{new_version}",
                                        require_separation_of_duties=state.get("strict_separation_of_duties", False),
                                    )
                                    st.success(f"Certified {result.name} v{result.version} (reviewer: {result.reviewer}, created_by: {result.created_by}).")
                                except Exception as exc:  # noqa: BLE001 -- surface the real governance refusal reason to the human reviewer
                                    st.error(f"Certification refused: {exc}")
                st.markdown("<div style='height:0.8rem'></div>", unsafe_allow_html=True)

        elif page == "Definition Diff":
            _section_head("git-compare", "Definition Reconciliation", "This is the part leaders care about: the same metric, defined twice, does not always mean the same number. Pairs shown below are alternate versions of one metric or explicitly curated comparisons -- never two different metrics paired just because they happen to share source tables.")
            diffs = state.get("diffs", [])
            if not diffs:
                st.info(
                    "No alternate versions or curated comparisons are registered yet. Every metric in "
                    "the catalog currently exists as exactly one version, and no comparison relationship "
                    "has been explicitly declared -- this is the honest state, not a placeholder bug. "
                    "See definition_diff.find_definition_diff_pairs() for how a pair would qualify."
                )
            for diff in diffs:
                with st.container(border=True):
                    st.markdown(f"#### {diff.left_name} vs {diff.right_name}")
                    left, right = st.columns(2)
                    with left:
                        _list_card("Where They Match", diff.matches, tint=GREEN_TINT)
                    with right:
                        _list_card("Where They Differ", diff.differences, tint=AMBER_TINT)
                    st.markdown(f"**Recommended use:** {diff.recommended_use}")
                    st.markdown("**Why this matters**")
                    st.write(explain_definition_diff(diff))
                st.markdown("<div style='height:0.8rem'></div>", unsafe_allow_html=True)

            st.markdown("<div style='height:1.4rem'></div>", unsafe_allow_html=True)
            st.markdown("<hr class='divider'>", unsafe_allow_html=True)
            _section_head(
                "git-compare",
                "Compare Any Two Metrics",
                "Pick any two catalog entries to see a field-by-field comparison. This is a cross-metric, "
                "informational comparison, not a claim that these are alternate definitions of the same "
                "metric -- unlike the reconciliation pairs above, nothing here implies a mismatch to fix.",
            )
            defs = state.get("definitions", [])
            names = [d.name for d in defs]
            if len(names) < 2:
                st.info("At least two catalogued metrics are needed to run a comparison.")
            else:
                by_name = {d.name: d for d in defs}
                pick_left, pick_right = st.columns(2)
                with pick_left:
                    left_name = st.selectbox("First metric", names, key="cross_metric_left")
                with pick_right:
                    right_default_index = 1 if len(names) > 1 else 0
                    right_name = st.selectbox("Second metric", names, index=right_default_index, key="cross_metric_right")
                if left_name == right_name:
                    st.warning("Choose two different metrics to compare.")
                else:
                    cross_diff = compare_definitions(by_name[left_name], by_name[right_name])
                    st.caption(f"Cross-metric comparison: {cross_diff.left_name} vs {cross_diff.right_name} (informational only)")
                    with st.container(border=True):
                        cleft, cright = st.columns(2)
                        with cleft:
                            _list_card("Where They Match", cross_diff.matches, tint=GREEN_TINT)
                        with cright:
                            _list_card("Where They Differ", cross_diff.differences, tint=AMBER_TINT)
                        st.markdown(f"**Observation:** {cross_diff.recommended_use}")

        elif page == "SQL Review":
            _section_head("terminal", "Governed SQL Review", "Paste a query and the copilot will score it against the approved catalog, then explain what should change before the query is used broadly.")
            sql = st.text_area("Paste SQL", height=220, placeholder="SELECT ...", label_visibility="collapsed")
            if st.button("Review SQL", type="primary"):
                approved_tables = state.get("catalog_tables", [])
                result = review_sql(sql, approved_tables)

                # This page reviews freeform SQL that isn't necessarily tied
                # to one specific catalog metric or table, so `definition`
                # and `source_health` are intentionally None here.
                #
                # IMPORTANT: passing source_health=None is a deliberate,
                # explicit "unknown/unavailable" signal, never an implicit
                # "assume healthy." shared.trust_scoring._score_source_health
                # scores None at 0 points (_SOURCE_UNKNOWN_POINTS), the same
                # as an explicitly-degraded call would score worse and a
                # healthy call would score +30 -- so this page can never
                # silently award trust points for source health it never
                # actually checked. This is proven by
                # tests/shared/test_trust_scoring.py's
                # test_missing_source_health_scores_zero_not_healthy. The
                # reason it's None at all, rather than a real SourceHealth
                # lookup, is that Governance has no live incident-persistence
                # connection yet (shared/data_service.py's incident functions
                # are contract-only until Phase 6) -- once that's wired up,
                # this should become a real shared.data_service.derive_source_health()
                # call per referenced table, not stay None indefinitely.
                trust_inputs = trust_score_inputs_from_review(result, approved_tables)
                trust = compute_trust_score(definition=None, source_health=None, **trust_inputs)

                st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
                st.warning(
                    "Source health: unknown / unavailable. Live incident data is not yet connected for "
                    "this app, so this review awards zero trust points for source health rather than "
                    "assuming the underlying tables are healthy.",
                    icon="⚠️",
                )
                _card("Governance Score", f"{result.score}", result.summary)
                st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
                a1, a2, a3 = st.columns(3)
                with a1: _card("Referenced Tables", f"{len(result.referenced_tables)}", ", ".join(result.referenced_tables) or "None detected")
                with a2: _card("Findings", f"{len(result.findings)}", "Rule-based checks on the query")
                with a3: _card("Trust Score", f"{trust.score}", f"{trust.band} (v{trust.scoring_version})")

                if trust.override_reason:
                    st.warning(trust.override_reason)

                st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Trust Score Factors</div>', unsafe_allow_html=True)
                    for factor in trust.factors:
                        sign = "+" if factor.points >= 0 else ""
                        st.markdown(f"- **{factor.name}** ({sign}{factor.points}): {factor.reason}")

                st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Findings</div>', unsafe_allow_html=True)
                    if result.findings:
                        for finding in result.findings:
                            st.markdown(f'{_severity_badge(finding.severity)} **{finding.category}:** {finding.message}', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span style="color:{MUTED};">No findings.</span>', unsafe_allow_html=True)

                st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Referenced Tables</div>', unsafe_allow_html=True)
                    st.write(", ".join(result.referenced_tables) or "None detected")

                st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Suggested Next Actions</div>', unsafe_allow_html=True)
                    for step in suggested_playbooks_for_review(result.score, result.findings):
                        st.markdown(f"- {step}")

                st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(f'<div style="font-weight:800; margin-bottom:8px; color:{FG};">Assistant Note</div>', unsafe_allow_html=True)
                    st.markdown(summarize_sql_review(sql, result, approved_tables, trust))

                if trust.band == "high_trust":
                    st.success("Looks aligned with the approved metric catalog.")
                elif trust.band == "review_required":
                    st.warning("Needs a small amount of governance cleanup.")
                else:
                    st.error("Needs material review before use.")

        elif page == "Lineage":
            _section_head("workflow", "Metric Lineage", "Trace each catalogued number from raw tables through the final dashboards that consume it.")
            lineage = [
                {
                    "metric": d.name, "owner": d.owner, "measurement_grain": d.measurement_grain,
                    "sources": ", ".join(d.approved_source_tables), "dashboards": ", ".join(d.downstream_dashboards),
                }
                for d in state.get("definitions", [])
            ]
            st.dataframe(pd.DataFrame(lineage), use_container_width=True, hide_index=True)
            st.info("Metric lineage is the narrative that tells leadership why this number is safe to use.")

        else:
            _section_head("scroll-text", "Audit Trail", "Every governance decision should leave a trace that shows what was checked, when, and why the result was accepted.")
            st.dataframe(state.get("audit", []), use_container_width=True, hide_index=True)
            st.caption("Placeholder rows: real audit events (SQL review submitted, definition diff generated, trust score calculated) will be written via shared/audit.py once loupe_platform.audit_events exists (Phase 6).")

    with ask_col:
        _render_ask_panel(state)
