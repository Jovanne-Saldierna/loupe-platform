"""Streamlit rendering for the Loupe E-Commerce Agent.

Renders `state` (built by apps/loupe_agent/main.py) and calls
apps.loupe_agent.metrics / apps.loupe_agent.scenarios / apps.loupe_agent.chat
functions directly from inside button/interaction handlers -- the same
idiomatic Streamlit pattern already established in
apps/metric_governance/ui.py and apps/data_quality_triage/ui.py. This
module never constructs a BigQuery client or calls shared.data_service
directly; it only ever calls this app's own metrics/scenarios/chat
functions, passing along state["client"].

--- Behavioral change vs. the original app.py ---
The original app.py's query functions returned pre-formatted strings
("Category: X\\nRevenue: $...") that app.py then re-parsed back into
structured data with parse_single_metrics()/parse_pipe_table() for
charting. apps.loupe_agent.metrics now returns real dicts/list[dict]
directly (see its module docstring), so that serialize-then-reparse round
trip is eliminated here: this module builds pandas DataFrames straight
from the structured results. parse_single_metrics/parse_pipe_table are not
migrated, since there is no longer any pipe-delimited text to parse.

--- Behavioral change vs. the original app.py (honest unavailability) ---
If state["data_available"] is False (no GOOGLE_CLOUD_PROJECT configured,
or BigQuery unreachable), every view renders an explicit "live data
unavailable" notice instead of attempting a query that would raise --
matching the same honest-empty-state precedent established in
apps/data_quality_triage/main.py's Phase 4 correction.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from apps.loupe_agent import metrics
from apps.loupe_agent.chat import run_agent

# ---------------------------------------------------------------------------
# ICON SLOTS -- replace each placeholder string with the real Lucide SVG
# markup. Just paste the <svg>...</svg> string as the new value for that
# key, nothing else in this file needs to change. Ported unchanged from
# the original app.py.
# ---------------------------------------------------------------------------
ICONS = {
    "logo": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/></svg>',
    "home": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    "sparkles": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/><path d="M11 3H9"/></svg>',
    "layout-dashboard": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>',
    "dollar-sign": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="2" y2="22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
    "trending-up": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/></svg>',
    "rotate-ccw": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>',
    "package": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><polyline points="3.29 7 12 12 20.71 7"/><path d="m7.5 4.27 9 5.15"/></svg>',
    "shirt": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/></svg>',
    "map-pin": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/></svg>',
    "megaphone": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 6a13 13 0 0 0 8.4-2.8A1 1 0 0 1 21 4v12a1 1 0 0 1-1.6.8A13 13 0 0 0 11 14H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z"/><path d="M6 14a12 12 0 0 0 2.4 7.2 2 2 0 0 0 3.2-2.4A8 8 0 0 1 10 14"/><path d="M8 6v8"/></svg>',
    "sliders-horizontal": '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 5H3"/><path d="M12 19H3"/><path d="M14 3v4"/><path d="M16 17v4"/><path d="M21 12h-9"/><path d="M21 19h-5"/><path d="M21 5h-7"/><path d="M8 10v4"/><path d="M8 12H3"/></svg>',
}


def icon(name: str, size: int = 18) -> str:
    return ICONS.get(name, "")


_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');

:root {
    --bg: #F7F8FC;
    --card: #FFFFFF;
    --border: #EAECF3;
    --text: #1D2433;
    --text-muted: #6B7280;
    --primary: #5B5FEF;
    --primary-hover: #4B4FD9;
    --primary-soft: #EEF0FE;
    --success-bg: #E7F7EE; --success-text: #1B7A43;
    --warn-bg: #FFF4E0; --warn-text: #B7791F;
    --risk-bg: #FDECEC; --risk-text: #C0362C;
    --radius: 16px;
    --shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06);
    --shadow-hover: 0 4px 12px rgba(16,24,40,0.08), 0 2px 4px rgba(16,24,40,0.06);
}

html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: var(--text); }
.stApp { background: var(--bg); }

h1, h2, h3, .display { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; color: var(--text); }
.mono { font-family: 'JetBrains Mono', monospace; }

section[data-testid="stSidebar"] {
    background: var(--card);
    border-right: 1px solid var(--border);
}
.brand-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0 1.2rem 0; }
.brand-mark { color: var(--primary); display: flex; }
.brand-name { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 800; font-size: 1.25rem; color: var(--text); }

div[data-testid="stSidebar"] .stButton button {
    width: 100%;
    text-align: left;
    background: transparent;
    color: var(--text-muted);
    border: none;
    border-radius: 10px;
    padding: 0.6rem 0.8rem;
    font-family: 'Inter', sans-serif;
    font-weight: 500;
    font-size: 0.92rem;
    margin-bottom: 0.15rem;
    transition: background 0.15s ease;
}
div[data-testid="stSidebar"] .stButton button:hover {
    background: var(--primary-soft);
    color: var(--primary);
}

.nav-active button {
    background: var(--primary-soft) !important;
    color: var(--primary) !important;
    font-weight: 600 !important;
}

.hero-eyebrow { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; letter-spacing: 0.1em; color: var(--primary); text-transform: uppercase; margin-bottom: 0.5rem; }
.hero-title { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 800; font-size: 2.4rem; color: var(--text); margin-bottom: 0.4rem; line-height: 1.15; }
.hero-sub { font-size: 1.02rem; color: var(--text-muted); margin-bottom: 1.6rem; max-width: 640px; }

.card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 1.25rem 1.4rem;
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.card:hover { box-shadow: var(--shadow-hover); transform: translateY(-2px); }

.kpi-icon { color: var(--primary); background: var(--primary-soft); width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; margin-bottom: 0.6rem; }
.kpi-label { font-size: 0.78rem; color: var(--text-muted); font-weight: 500; }
.kpi-value { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; font-size: 1.6rem; color: var(--text); margin-top: 0.1rem; }
.kpi-sub { font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }

.pill { display: inline-block; padding: 0.2rem 0.65rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; font-family: 'Inter', sans-serif; }
.pill-healthy { background: var(--success-bg); color: var(--success-text); }
.pill-watch { background: var(--warn-bg); color: var(--warn-text); }
.pill-risk { background: var(--risk-bg); color: var(--risk-text); }

.cta-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 1.5rem;
    transition: box-shadow 0.2s ease, transform 0.2s ease;
    cursor: pointer;
}
.cta-card:hover { box-shadow: var(--shadow-hover); transform: translateY(-2px); border-color: var(--primary); }
.cta-icon { color: var(--primary); background: var(--primary-soft); width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center; margin-bottom: 0.8rem; }
.cta-title { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; font-size: 1.05rem; margin-bottom: 0.3rem; }
.cta-desc { font-size: 0.88rem; color: var(--text-muted); }

.section-label { display: flex; align-items: center; gap: 0.5rem; font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; font-size: 1rem; color: var(--text); margin: 1.6rem 0 0.8rem 0; }
.section-label .icon-wrap { color: var(--primary); display: flex; }

.ledger-card { background: var(--card); border: 1px solid var(--border); border-left: 3px solid var(--primary); border-radius: var(--radius); padding: 1.4rem 1.6rem; margin-top: 1.4rem; box-shadow: var(--shadow); }
.ledger-eyebrow { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; letter-spacing: 0.1em; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.6rem; }
.ledger-answer { font-size: 0.97rem; line-height: 1.6; }
.ledger-answer h1, .ledger-answer h2, .ledger-answer h3 { font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; color: var(--text); margin-top: 1.1rem; }
.ledger-answer table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }
.ledger-answer th { background: var(--primary-soft); color: var(--text); text-align: left; padding: 0.6rem 0.9rem; border-bottom: 2px solid var(--primary); font-weight: 600; }
.ledger-answer td { padding: 0.6rem 0.9rem; border-bottom: 1px solid var(--border); }

div[data-testid="stTextInput"] input { border: 1.5px solid var(--border); border-radius: 10px; padding: 0.75rem 1rem; background: var(--card); }
div[data-testid="stTextInput"] input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft); }

.main .stButton button {
    background-color: var(--primary);
    color: white;
    border-radius: 10px;
    border: none;
    font-weight: 600;
    padding: 0.5rem 1.4rem;
}
.main .stButton button:hover { background-color: var(--primary-hover); color: white; }
</style>
"""


def return_rate_pill(rate) -> str:
    """Consistent status-pill classification used everywhere return rate appears."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return ""
    if r > 20:
        return '<span class="pill pill-risk">Risk</span>'
    elif r >= 10:
        return '<span class="pill pill-watch">Watch</span>'
    else:
        return '<span class="pill pill-healthy">Healthy</span>'


def kpi_card(icon_name: str, label: str, value: str, sub: str = "") -> str:
    return f"""
    <div class="card">
        <div class="kpi-icon">{icon(icon_name)}</div>
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-sub">{sub}</div>
    </div>
    """


def section_label(icon_name: str, text: str) -> str:
    return f'<div class="section-label"><span class="icon-wrap">{icon(icon_name)}</span>{text}</div>'


def scope_caption(grain: str, window: str) -> None:
    """Render a small, consistent caption stating the ACTUAL reporting
    grain and date window of the numbers displayed directly above it.

    Per the Phase 5 grain-mismatch correction: shared.metric_catalog's
    measurement_grain is a property of the metric's definition and never
    describes any one query's output shape. This caption is the UI-side
    half of that same discipline -- it states what THIS particular view
    actually shows (e.g. "one aggregate row for the selected window" vs.
    "one row per month"), not the metric's abstract definition, so a
    viewer never assumes a date range or grouping that wasn't applied.
    """

    st.caption(f"Reporting grain: {grain} · Date window: {window}")


def _unavailable_notice() -> None:
    st.warning(
        "Live data is unavailable right now (no GOOGLE_CLOUD_PROJECT configured, or BigQuery is "
        "unreachable). No fabricated or sample metrics are shown -- see the sidebar for details."
    )


# ---------------------------------------------------------------------------
# HOME VIEW
# ---------------------------------------------------------------------------


def _render_home(client) -> None:
    st.markdown('<div class="hero-eyebrow">The Look · E-Commerce Analytics</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">See your business clearly.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-sub">Loupe turns raw order, product, and traffic data into a live, queryable '
        "analytics agent, plus a full interactive dashboard, built on real e-commerce data.</div>",
        unsafe_allow_html=True,
    )

    if client is not None:
        teaser_start, teaser_end = date(2025, 7, 1), date.today()
        teaser = metrics.get_dashboard_kpis(client, teaser_start, teaser_end)
        t1, t2, t3, t4 = st.columns(4)
        with t1:
            st.markdown(kpi_card("dollar-sign", "Revenue", f"${teaser['revenue']:,.0f}"), unsafe_allow_html=True)
        with t2:
            st.markdown(kpi_card("trending-up", "Margin", f"${teaser['margin']:,.0f}"), unsafe_allow_html=True)
        with t3:
            st.markdown(
                kpi_card("rotate-ccw", "Return Rate", f"{teaser['return_rate_pct']}%", return_rate_pill(teaser["return_rate_pct"])),
                unsafe_allow_html=True,
            )
        with t4:
            st.markdown(kpi_card("package", "Items Sold", f"{teaser['total_items']:,}"), unsafe_allow_html=True)
        scope_caption(
            "one aggregate row for the window below",
            f"{teaser_start.isoformat()} to {teaser_end.isoformat()}",
        )
    else:
        _unavailable_notice()

    st.markdown('<div style="height: 1.6rem;"></div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            f"""
            <div class="cta-card">
                <div class="cta-icon">{icon('sparkles')}</div>
                <div class="cta-title">Ask the Agent</div>
                <div class="cta-desc">Ask natural-language questions about category performance, state comparisons, return-driven margin loss, and more.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Go to Agent →", key="cta_agent"):
            st.session_state.view = "Ask the Agent"
            st.rerun()
    with c2:
        st.markdown(
            f"""
            <div class="cta-card">
                <div class="cta-icon">{icon('layout-dashboard')}</div>
                <div class="cta-title">Explore the Dashboard</div>
                <div class="cta-desc">Filter by date, category, and state to see revenue trends, category performance, and channel mix live.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Go to Dashboard →", key="cta_dashboard"):
            st.session_state.view = "Dashboard"
            st.rerun()


# ---------------------------------------------------------------------------
# ASK THE AGENT VIEW
# ---------------------------------------------------------------------------


def _render_agent(client) -> None:
    st.markdown('<div class="hero-eyebrow">Ask the Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Ask a question, get a grounded answer.</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="hero-sub">Try: "How is the Dresses category performing?" · "Compare California, Texas, and New York" ·
        "What if we cut the return rate in Swim by 5 points?" · "Which categories are losing the most money to returns?"</div>
        """,
        unsafe_allow_html=True,
    )

    if client is None:
        _unavailable_notice()
        return

    question = st.text_input("Ask a question", placeholder="e.g. How is the Dresses category performing?", label_visibility="collapsed")
    submit = st.button("Ask")

    if submit and question:
        with st.spinner("Querying and reasoning..."):
            try:
                result = run_agent(client, question)
            except Exception as e:
                st.error(f"Error: {e}")
                st.stop()

        cat = result["category"]
        raw = result["raw_data"]

        if cat in ("single_category", "single_state") and raw:
            label = raw.get("category", raw.get("state", ""))
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(kpi_card("dollar-sign", label, f"${raw['revenue']:,.0f}"), unsafe_allow_html=True)
            with col2:
                st.markdown(kpi_card("trending-up", "Margin", f"${raw['margin']:,.0f}"), unsafe_allow_html=True)
            with col3:
                st.markdown(
                    kpi_card("rotate-ccw", "Return Rate", f"{raw['return_rate_pct']}%", return_rate_pill(raw["return_rate_pct"])),
                    unsafe_allow_html=True,
                )
            scope_caption(
                f"one row for this {'category' if cat == 'single_category' else 'state'}, "
                "aggregated across ALL matching order_items",
                "all-time -- no date filter is applied by this query",
            )

        elif cat in ("multi_category_comparison", "multi_state_comparison") and raw:
            df = pd.DataFrame(raw)
            if df.empty:
                st.warning("I couldn't match those to specific entries in the data. Try naming them directly.")
            else:
                import plotly.express as px

                label_col = "category" if "category" in df.columns else "state"
                cols = st.columns(len(df))
                for i, row in df.iterrows():
                    with cols[i]:
                        pill = return_rate_pill(row["return_rate_pct"])
                        st.markdown(
                            f"""<div class="card">
                                <div class="kpi-label">{row[label_col]}</div>
                                <div class="kpi-value">${row['revenue']:,.0f}</div>
                                <div class="kpi-sub">{row['return_rate_pct']}% return {pill}</div>
                            </div>""",
                            unsafe_allow_html=True,
                        )
                fig = px.bar(
                    df, x=label_col, y="margin", color_discrete_sequence=["#5B5FEF"],
                    title="Margin by " + label_col.title(), template="plotly_white",
                )
                fig.update_layout(showlegend=False, font_family="Inter")
                st.plotly_chart(fig, use_container_width=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download comparison data", csv, "comparison.csv", "text/csv")
                scope_caption(
                    f"one row per requested {label_col}, each aggregated across ALL matching order_items",
                    "all-time -- no date filter is applied by this query",
                )

        elif cat == "channel_analysis" and raw:
            months = raw.get("months", [])
            df = pd.DataFrame(months)
            if not df.empty:
                import plotly.graph_objects as go

                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(x=df["month"], y=df["paid_share_pct"], mode="lines+markers",
                               line=dict(color="#5B5FEF", width=3), name="Paid Share %")
                )
                fig.update_layout(title="Paid Channel Share Over Time", template="plotly_white",
                                   font_family="Inter", yaxis_title="Paid Share (%)")
                st.plotly_chart(fig, use_container_width=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download channel data", csv, "channel_mix.csv", "text/csv")
                scope_caption("one row per month per channel group (paid/unpaid)", "trailing 24 months from today")

        elif cat == "returns_leakage" and raw:
            df = pd.DataFrame(raw)
            if not df.empty:
                import plotly.express as px

                top10 = df.head(10).sort_values("margin_lost_to_returns", ascending=True)
                fig = px.bar(
                    top10, x="margin_lost_to_returns", y="category", orientation="h",
                    color_discrete_sequence=["#5B5FEF"], title="Top 10 Categories by Margin Lost to Returns",
                    template="plotly_white",
                )
                fig.update_layout(font_family="Inter")
                st.plotly_chart(fig, use_container_width=True)
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download returns data", csv, "returns_leakage.csv", "text/csv")
                scope_caption(
                    "one row per category, ranked by absolute margin dollars lost to returns",
                    "all-time -- no date filter is applied by this query",
                )

        import markdown as md

        answer_html = md.markdown(result["answer"], extensions=["tables"])
        st.markdown(
            f"""
            <div class="ledger-card">
                <div class="ledger-eyebrow">Agent Response</div>
                <div class="ledger-answer">{answer_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# DASHBOARD VIEW
# ---------------------------------------------------------------------------


def _render_dashboard(client) -> None:
    st.markdown('<div class="hero-eyebrow">Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-title">Live performance, filtered your way.</div>', unsafe_allow_html=True)

    if client is None:
        _unavailable_notice()
        return

    import plotly.express as px
    import plotly.graph_objects as go

    st.markdown(section_label("sliders-horizontal", "Filters"), unsafe_allow_html=True)
    f1, f2, f3 = st.columns([2, 2, 2])
    today = date.today()
    with f1:
        date_range = st.date_input("Date range", value=(date(today.year - 2, today.month, 1), today))
    with f2:
        selected_categories = st.multiselect("Category filter", metrics.ALL_CATEGORIES, default=[])
    with f3:
        selected_states = st.multiselect("State filter", list(metrics.STATE_ABBREV.keys()), default=[])

    if len(date_range) != 2:
        st.stop()
    start_date, end_date = date_range
    cats = selected_categories or None
    states = selected_states or None

    kpis = metrics.get_dashboard_kpis(client, start_date, end_date, cats, states)

    st.markdown(section_label("layout-dashboard", "Overview"), unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(kpi_card("dollar-sign", "Revenue", f"${kpis['revenue']:,.0f}"), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card("trending-up", "Margin", f"${kpis['margin']:,.0f}"), unsafe_allow_html=True)
    with k3:
        st.markdown(
            kpi_card("rotate-ccw", "Return Rate", f"{kpis['return_rate_pct']}%", return_rate_pill(kpis["return_rate_pct"])),
            unsafe_allow_html=True,
        )
    with k4:
        st.markdown(kpi_card("package", "Items Sold", f"{kpis['total_items']:,}"), unsafe_allow_html=True)
    scope_caption(
        "one aggregate row for the selected filters",
        f"{start_date.isoformat()} to {end_date.isoformat()} (inclusive)",
    )

    st.markdown(section_label("trending-up", "Revenue & Margin Trend"), unsafe_allow_html=True)
    trend = metrics.get_revenue_trend(client, start_date, end_date, cats, states)
    if trend:
        trend_df = pd.DataFrame(trend)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["revenue"], name="Revenue", line=dict(color="#5B5FEF", width=3)))
        fig.add_trace(go.Scatter(x=trend_df["month"], y=trend_df["margin"], name="Margin", line=dict(color="#8B5CF6", width=3)))
        fig.update_layout(template="plotly_white", font_family="Inter", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        scope_caption(
            "one row per month within the selected filters",
            f"{start_date.isoformat()} to {end_date.isoformat()} (inclusive)",
        )
    else:
        st.info("No data in this date range for the selected filters.")

    col_left, col_right = st.columns(2)
    cat_data: list[dict] = []

    with col_left:
        st.markdown(section_label("shirt", "Category Leaderboard"), unsafe_allow_html=True)
        sort_metric = st.selectbox("Sort by", ["revenue", "margin", "return_rate_pct"], format_func=lambda x: x.replace("_", " ").title())
        cat_data = metrics.get_category_leaderboard_dashboard(client, start_date, end_date, states)
        if cat_data:
            cat_df = pd.DataFrame(cat_data).sort_values(sort_metric, ascending=False).head(15)
            color_col = "return_rate_pct" if sort_metric == "return_rate_pct" else None
            fig = px.bar(
                cat_df.sort_values(sort_metric), x=sort_metric, y="category", orientation="h",
                color=color_col, color_continuous_scale=["#5B5FEF", "#C0362C"] if color_col else None,
                color_discrete_sequence=["#5B5FEF"] if not color_col else None, template="plotly_white",
            )
            fig.update_layout(font_family="Inter", showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
            scope_caption(
                "one row per category within the selected filters",
                f"{start_date.isoformat()} to {end_date.isoformat()} (inclusive)",
            )

    with col_right:
        st.markdown(section_label("map-pin", "Revenue by State"), unsafe_allow_html=True)
        state_data = metrics.get_state_breakdown_dashboard(client, start_date, end_date, cats)
        if state_data:
            state_df = pd.DataFrame(state_data)
            fig = px.choropleth(
                state_df, locations="state_abbrev", locationmode="USA-states",
                color="revenue", scope="usa", color_continuous_scale=["#EEF0FE", "#5B5FEF", "#3730A3"],
            )
            fig.update_layout(font_family="Inter", margin=dict(l=0, r=0, t=0, b=0))
            st.plotly_chart(fig, use_container_width=True)
            scope_caption(
                "one row per state within the selected filters",
                f"{start_date.isoformat()} to {end_date.isoformat()} (inclusive)",
            )

    st.markdown(section_label("megaphone", "Paid vs. Organic Channel Mix"), unsafe_allow_html=True)
    channel_data = metrics.get_channel_mix_range(client, start_date, end_date, cats, states)
    if channel_data:
        channel_df = pd.DataFrame(channel_data)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=channel_df["month"], y=channel_df["paid"], name="Paid", mode="lines", stackgroup="one", line=dict(color="#5B5FEF")))
        fig.add_trace(go.Scatter(x=channel_df["month"], y=channel_df["unpaid"], name="Unpaid (Organic/Search)", mode="lines", stackgroup="one", line=dict(color="#C7D2FE")))
        fig.update_layout(template="plotly_white", font_family="Inter", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        scope_caption(
            "one row per month within the selected filters (denominator: order_item count, not order count)",
            f"{start_date.isoformat()} to {end_date.isoformat()} (inclusive)",
        )

    if cat_data:
        csv = pd.DataFrame(cat_data).to_csv(index=False).encode("utf-8")
        st.download_button("Download category data", csv, "category_breakdown.csv", "text/csv")


# ---------------------------------------------------------------------------
# App shell
# ---------------------------------------------------------------------------


def render_app(state: dict) -> None:
    """Render the full app from `state` (built by apps/loupe_agent/main.py).

    Expected shape:
    - state["client"]: a BigQueryClientLike, or None if data_available is False
    - state["data_available"]: bool
    - state["unavailable_reason"]: str | None
    """

    st.markdown(_CSS, unsafe_allow_html=True)

    if "view" not in st.session_state:
        st.session_state.view = "Home"

    client = state.get("client")

    with st.sidebar:
        st.markdown(
            f"""
            <div class="brand-row">
                <span class="brand-mark">{icon('logo')}</span>
                <span class="brand-name">Loupe</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        nav_items = [("Home", "home"), ("Ask the Agent", "sparkles"), ("Dashboard", "layout-dashboard")]
        for label, icon_name in nav_items:
            is_active = st.session_state.view == label
            wrapper_class = "nav-active" if is_active else ""
            st.markdown(f'<div class="{wrapper_class}">', unsafe_allow_html=True)
            if st.button(f"{label}", key=f"nav_{label}", use_container_width=True):
                st.session_state.view = label
            st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("---")
        if not state.get("data_available", False):
            st.markdown(
                f"<div style=\"font-family:'JetBrains Mono',monospace; font-size:0.72rem; color:#C0362C;\">"
                f"LIVE DATA UNAVAILABLE<br>{state.get('unavailable_reason', '')}</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            """
            <div style="font-family:'JetBrains Mono',monospace; font-size:0.72rem; color:#9CA3AF; line-height:1.7;">
            BUILT ON<br>
            LangChain patterns<br>
            Claude (Anthropic)<br>
            Google BigQuery<br><br>
            DATASET<br>
            bigquery-public-data<br>
            .thelook_ecommerce
            </div>
            """,
            unsafe_allow_html=True,
        )

    view = st.session_state.view
    if view == "Home":
        _render_home(client)
    elif view == "Ask the Agent":
        _render_agent(client)
    elif view == "Dashboard":
        _render_dashboard(client)
