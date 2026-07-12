"""Tests for the pure, non-Streamlit-rendering helpers in
apps/loupe_agent/ui.py. Full view rendering is not unit tested, consistent
with the rest of this codebase's approach to Streamlit rendering code (see
tests/loupe_agent/test_main.py's docstring)."""

from __future__ import annotations

from apps.loupe_agent import ui


def test_return_rate_pill_classifies_risk_watch_healthy():
    assert "pill-risk" in ui.return_rate_pill(25)
    assert "pill-watch" in ui.return_rate_pill(15)
    assert "pill-healthy" in ui.return_rate_pill(5)


def test_return_rate_pill_boundary_values_match_the_original_thresholds():
    # Original: `if r > 20: risk elif r >= 10: watch else: healthy`
    assert "pill-risk" in ui.return_rate_pill(20.01)
    assert "pill-watch" in ui.return_rate_pill(20)
    assert "pill-watch" in ui.return_rate_pill(10)
    assert "pill-healthy" in ui.return_rate_pill(9.99)


def test_return_rate_pill_handles_non_numeric_input_gracefully():
    assert ui.return_rate_pill(None) == ""
    assert ui.return_rate_pill("n/a") == ""


def test_kpi_card_includes_label_and_value():
    html = ui.kpi_card("dollar-sign", "Revenue", "$1,000")
    assert "Revenue" in html
    assert "$1,000" in html


def test_icon_returns_empty_string_for_unknown_name():
    assert ui.icon("not-a-real-icon") == ""


def test_scope_caption_does_not_raise_outside_a_streamlit_runtime():
    # Per the Phase 5 grain-mismatch correction: every dashboard/agent
    # result view calls scope_caption() to state its actual reporting
    # grain and date window. It must be safe to call from a bare script
    # context (as pytest runs it) -- Streamlit logs a warning but must not
    # raise.
    ui.scope_caption("one row per month", "2026-01-01 to 2026-06-30")


def test_icon_returns_svg_markup_for_known_name():
    assert "<svg" in ui.icon("logo")
