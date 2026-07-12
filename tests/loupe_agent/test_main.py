"""Tests for apps/loupe_agent/main.py's state assembly.

Only build_state() is tested here -- it's the one function in main.py
that does not touch Streamlit's interactive surface, matching the pattern
in tests/data_quality_triage/test_main.py. render_app()/main() invocation
is not unit tested, consistent with the rest of this codebase's approach
to Streamlit rendering code.
"""

from __future__ import annotations

import apps.loupe_agent.main as main


def test_build_state_is_honestly_unavailable_when_no_project_is_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    state = main.build_state()
    assert state["client"] is None
    assert state["data_available"] is False
    assert "GOOGLE_CLOUD_PROJECT" in state["unavailable_reason"]


def test_build_state_is_honestly_unavailable_when_client_construction_fails(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")

    def _boom(project):
        raise RuntimeError("credentials not found")

    monkeypatch.setattr(main, "get_bigquery_client", _boom)
    state = main.build_state()
    assert state["client"] is None
    assert state["data_available"] is False
    assert "credentials not found" in state["unavailable_reason"]


def test_build_state_succeeds_when_a_project_is_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    sentinel_client = object()
    monkeypatch.setattr(main, "get_bigquery_client", lambda project: sentinel_client)

    state = main.build_state()

    assert state["client"] is sentinel_client
    assert state["data_available"] is True
    assert state["unavailable_reason"] is None
