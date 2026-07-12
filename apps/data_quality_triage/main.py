"""Streamlit entrypoint for the Data Quality Incident Triage Agent.

Owns exactly one responsibility: assembling the `state` dict ui.py renders,
from real BigQuery access routed entirely through shared.data_service and
this app's own profiling / checks / anomaly_engine modules. No fictional
sample data (the original app's INC-1042 / INC-1043 examples, or its
MetricCheck catalog examples) is loaded here -- per the Phase 4 migration
spec, that data now lives only in tests/, explicitly labeled as fixtures.

--- Behavioral change vs. the original app ---
The original app.py caught any exception from its live data path and
silently fell back to load_sample_data() -- fabricated incidents on tables
that were never actually checked:

    try:
        state = load_live_state()
    except Exception:
        state = load_sample_data()

build_state() below still catches exceptions from the live path (a real
BigQuery outage, missing credentials, or an unreachable project must not
crash the whole app), but it never fabricates a replacement. It returns an
honest data_available=False state instead, which ui.py renders as an
explicit "live data unavailable" message on every section -- the same
honest-empty-state precedent already established in
apps/metric_governance's Phase 3 correction (never silently assume health
or data that wasn't actually checked).

build_state() itself does no Streamlit caching -- it is a plain function,
directly testable without a Streamlit runtime (see
tests/data_quality_triage/test_main.py). The original app wrapped its
equivalent function in @st.cache_data(ttl=900), coupling state assembly to
Streamlit. If caching is reintroduced later, it belongs as a thin wrapper
around build_state() inside main(), not baked into build_state() itself --
keeping this function framework-independent, per the same discipline
already established in apps/metric_governance/main.py.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from apps.data_quality_triage.anomaly_engine import evaluate_profiles
from apps.data_quality_triage.checks import build_audit_event_for_incident, findings_to_incidents
from apps.data_quality_triage.persistence import persist_confirmed_incidents, read_schema_baseline
from apps.data_quality_triage.profiling import QUALIFIED_DATASET, build_table_profiles
from apps.data_quality_triage.ui import render_app
from shared.data_service import get_bigquery_client
from shared.persistence_bootstrap import resolve_persistence

st.set_page_config(
    page_title="Data Quality Incident Triage Agent",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

RULE_VERSION = "v1-migrated"


def _billing_project() -> Optional[str]:
    """The GCP project BigQuery queries are billed against -- distinct
    from QUALIFIED_DATASET's "bigquery-public-data" data project, which
    only hosts the public dataset and is not a project this app has query
    permissions to bill against.

    Read from GOOGLE_CLOUD_PROJECT, per docs/development.md's instruction
    to document required environment variable names. Returns None (never a
    guessed default project id) if unset -- build_state() treats that as a
    legitimate, honestly-reported "unavailable" reason rather than trying
    to construct a client with a fabricated project.
    """

    return os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() or None


def _audit_rows(generated_at: str, *, data_available: bool, detail: str) -> list[dict]:
    return [
        {
            "event": "Live triage run" if data_available else "Live triage run failed",
            "timestamp": generated_at,
            "source": "apps/data_quality_triage/main.py::build_state",
            "detail": detail,
        }
    ]


def build_state() -> dict:
    """Assemble the real, live state ui.py renders.

    Attempts one live pass: list every table in QUALIFIED_DATASET, profile
    each (apps/data_quality_triage/profiling.py, via
    shared.data_service.list_tables/get_table_metadata), run every
    deterministic check -- metadata-only and live ratio queries --
    against them (anomaly_engine.evaluate_profiles(), which wires in both
    check families; see checks.py's module docstring for that fix), and
    promote the non-passing findings into shared.models.Incident records
    (checks.findings_to_incidents()).

    If anything in that path fails -- no GOOGLE_CLOUD_PROJECT configured,
    missing Application Default Credentials, or a real BigQuery error --
    the exception is caught here and turned into an honest
    data_available=False state. It is never turned into fabricated sample
    incidents; see this module's docstring for the full account of that
    behavioral change vs. the original app.

    persistence_available is always False: shared/data_service.py's
    incident-lifecycle functions (wrapped by incident_lifecycle.py) are
    contract-only until Phase 6, so there is no live
    loupe_platform.incidents table to read history or source health from
    yet. This run's `incidents` are freshly detected, not fetched from
    persisted history.
    """

    generated_at = datetime.now(tz=timezone.utc).isoformat()

    persistence = resolve_persistence()
    base_state = {
        "title": "Data Quality Incident Triage Agent",
        "dataset": QUALIFIED_DATASET,
        "generated_at": generated_at,
        "persistence_mode": persistence.mode,
        "persistence_available": persistence.available,
        "persistence_unavailable_reason": persistence.safe_error,
        "source_health": [],
        # Handed to ui.py so its incident lifecycle action buttons
        # (acknowledge/investigate/mitigate/resolve/reopen) can call
        # apps.data_quality_triage.incident_lifecycle's persisted-mode
        # path with the SAME client/config resolve_persistence() already
        # validated -- ui.py never constructs its own BigQuery client or
        # PlatformConfig. persistence_client is None whenever
        # persistence_available is False, which is exactly when ui.py
        # must fall back to labeling a transition session-only.
        "persistence_client": persistence.client,
        "persistence_config": persistence.config,
        "actor": "data_quality_triage.ui",
    }

    project = _billing_project()
    if project is None:
        return {
            **base_state,
            "profiles": [],
            "findings": [],
            "incidents": [],
            "incident_persistence": [],
            "data_available": False,
            "unavailable_reason": "GOOGLE_CLOUD_PROJECT is not set, so no BigQuery client can be constructed.",
            "audit": _audit_rows(generated_at, data_available=False, detail="No GOOGLE_CLOUD_PROJECT configured."),
        }

    try:
        client = get_bigquery_client(project)
        profiles = build_table_profiles(client, QUALIFIED_DATASET)

        # Read persisted schema baselines, if persisted mode is
        # configured and reachable, so check_schema_drift() has something
        # real to compare against. Never fabricated: a table with no
        # promoted baseline yet, or persistence being unavailable, simply
        # means no entry in this dict -- evaluate_profiles() already
        # reports that as an honest not_evaluated finding, per
        # checks.check_schema_drift()'s docstring.
        schema_baselines = {}
        if persistence.available and persistence.client is not None:
            for profile in profiles:
                baseline = read_schema_baseline(
                    persistence.client,
                    dataset=QUALIFIED_DATASET,
                    table_id=profile.table_id,
                    config=persistence.config,
                )
                if baseline is not None:
                    schema_baselines[profile.table_id] = baseline

        findings = evaluate_profiles(client, QUALIFIED_DATASET, profiles, schema_baselines=schema_baselines)
        incidents = findings_to_incidents(
            findings, dataset=QUALIFIED_DATASET, created_at=generated_at, rule_version=RULE_VERSION
        )
    except Exception as exc:  # noqa: BLE001 -- any live-path failure must degrade to an honest state, never crash the app or fabricate data
        return {
            **base_state,
            "profiles": [],
            "findings": [],
            "incidents": [],
            "incident_persistence": [],
            "data_available": False,
            "unavailable_reason": repr(exc),
            "audit": _audit_rows(generated_at, data_available=False, detail=repr(exc)),
        }

    # Persist every confirmed (non-pass, non-not_evaluated) incident this
    # run detected, atomically, one at a time, via
    # shared.incident_persistence.create_incident() +
    # shared.audit_persistence.write_event_idempotent() -- never the
    # streaming shared.audit.write_event() path, per Phase 6D's "no
    # governed action uses the streaming audit path" requirement. If
    # persistence is unavailable, `incident_persistence` is an empty list
    # and ui.py must label these findings "not persisted" -- the findings
    # themselves are still shown honestly; nothing is ever substituted
    # with sample/fabricated incidents.
    incident_persistence: list = []
    if persistence.available and persistence.client is not None and incidents:
        def _build_event(incident):
            finding = next((f for f in findings if f.table_id == incident.table_id and f.check_name == incident.check_type), None)
            return build_audit_event_for_incident(
                incident,
                finding,
                event_id=f"incident_created.{incident.incident_id}",
                timestamp=generated_at,
                actor="data_quality_triage.main",
            )

        incident_persistence = persist_confirmed_incidents(
            persistence.client, incidents, actor="data_quality_triage.main", build_audit_event=_build_event
        )

    return {
        **base_state,
        "profiles": profiles,
        "findings": findings,
        "incidents": incidents,
        "incident_persistence": incident_persistence,
        "data_available": True,
        "unavailable_reason": None,
        "audit": _audit_rows(
            generated_at,
            data_available=True,
            detail=f"{len(profiles)} table(s) profiled, {len(findings)} finding(s), {len(incidents)} incident(s) detected.",
        ),
    }


def main() -> None:
    state = build_state()
    render_app(state)


if __name__ == "__main__":
    main()
