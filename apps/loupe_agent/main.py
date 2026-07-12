"""Streamlit entrypoint for the Loupe E-Commerce Agent.

Owns exactly one responsibility: constructing the BigQuery client
render_app() needs and handing it off, via shared.data_service, exactly
like apps/data_quality_triage/main.py's build_state()/_billing_project()
pattern. No bigquery.Client is constructed here or anywhere else in
apps/loupe_agent/ -- see apps/loupe_agent/metrics.py's module docstring.

build_state() only proves the client can be *constructed* (a real project
is configured); it does not run a query, since Loupe's queries are
on-demand per user interaction (a chat question, a dashboard filter
change), not a single upfront profiling pass like Triage's. If
GOOGLE_CLOUD_PROJECT is unset, state["client"] is None and
state["data_available"] is False -- ui.py renders an explicit "live data
unavailable" notice rather than attempting a query that would raise. This
is a plain function, directly testable without a Streamlit runtime (see
tests/loupe_agent/test_main.py), matching the same discipline established
in the other two apps' main.py files.
"""

from __future__ import annotations

import os
from typing import Optional

import streamlit as st

from apps.loupe_agent.ui import render_app
from shared.data_service import get_bigquery_client

st.set_page_config(page_title="Loupe — E-Commerce Analytics", page_icon="🔍", layout="wide")


def _billing_project() -> Optional[str]:
    """The GCP project BigQuery queries are billed against -- see
    apps/data_quality_triage/main.py::_billing_project()'s docstring for
    why this is read from GOOGLE_CLOUD_PROJECT rather than a hard-coded
    project id."""

    return os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None


def build_state() -> dict:
    """Construct the real BigQuery client render_app() needs, or an
    honest data_available=False state if no project is configured or
    client construction fails."""

    project = _billing_project()
    if project is None:
        return {
            "client": None,
            "data_available": False,
            "unavailable_reason": "GOOGLE_CLOUD_PROJECT is not set, so no BigQuery client can be constructed.",
        }

    try:
        client = get_bigquery_client(project)
    except Exception as exc:  # noqa: BLE001 -- any client-construction failure must degrade to an honest state, never crash the app
        return {"client": None, "data_available": False, "unavailable_reason": repr(exc)}

    return {"client": client, "data_available": True, "unavailable_reason": None}


def main() -> None:
    state = build_state()
    render_app(state)


if __name__ == "__main__":
    main()
