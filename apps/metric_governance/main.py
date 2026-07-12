"""Streamlit entrypoint for Metric Governance Copilot.

Owns exactly one responsibility: assembling the `state` dict ui.py renders
from real, shared sources -- shared.metric_catalog for definitions, and
this app's own definition_diff module for computed comparisons. No
fictional sample data (the previous Finance-vs-Product ARR example) is
loaded here; per the approved Phase 3 decision that data now lives only
in tests/, explicitly labeled fictional.

This module intentionally does no rendering itself (that's ui.py's job)
and no scoring/finding computation itself (that's review.py,
remediation.py, and shared.trust_scoring's job) -- it only assembles.
"""

from __future__ import annotations

import streamlit as st

from apps.metric_governance.definition_diff import compare_definitions, find_definition_diff_pairs
from apps.metric_governance.persistence import read_catalog, trust_score_for_definition
from apps.metric_governance.ui import render_app
from shared.metric_catalog import list_definitions
from shared.persistence_bootstrap import resolve_persistence

st.set_page_config(
    page_title="Metric Governance Copilot",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)


def build_state() -> dict:
    """Assemble the real, live state ui.py renders.

    catalog_tables is the union of every catalogued definition's
    approved_source_tables -- this is what review.py checks freeform SQL
    against on the SQL Review page.

    `diffs` is deliberately narrow: only alternate versions of the same
    metric, or explicitly curated comparisons (see
    definition_diff.find_definition_diff_pairs()'s docstring). Today's
    real catalog has exactly one version of each of its five metrics and
    no curated comparisons registered, so `diffs` is expected to be empty
    -- ui.py's Definition Diff page shows that honestly rather than
    manufacturing pairs from incidental shared source tables.

    `audit` is a placeholder until shared/audit.py is wired to a live
    loupe_platform.audit_events table (see docs/development.md and
    shared/data_service.py's module docstring on Phase 6 persistence).
    """

    persistence = resolve_persistence()

    catalog_unavailable = False
    catalog_unavailable_reason = None
    definition_trust: dict[str, object] = {}

    if persistence.mode == "persisted":
        if persistence.available and persistence.client is not None:
            catalog_read = read_catalog(persistence.client)
            definitions = catalog_read.definitions
            catalog_unavailable = catalog_read.catalog_unavailable
            catalog_unavailable_reason = catalog_read.safe_error
            for definition in definitions:
                definition_trust[definition.name] = trust_score_for_definition(persistence.client, definition)
            audit_source = "shared/metric_catalog_persistence.py (persisted mode)"
        else:
            definitions = []
            catalog_unavailable = True
            catalog_unavailable_reason = persistence.safe_error
            audit_source = "shared/metric_catalog_persistence.py (persisted mode, unavailable)"
    else:
        # Explicit constants/demo mode -- never an automatic fallback for
        # an unavailable persisted read; see shared/config.py's
        # PersistenceMode docstring.
        definitions = list_definitions()
        audit_source = "shared/metric_catalog.py (constants mode)"

    diffs = [compare_definitions(left, right) for left, right in find_definition_diff_pairs(definitions)]
    catalog_tables = sorted({table for definition in definitions for table in definition.approved_source_tables})

    return {
        "title": "Metric Governance Copilot",
        "dataset": "bigquery-public-data.thelook_ecommerce",
        "definitions": definitions,
        "diffs": diffs,
        "catalog_tables": catalog_tables,
        "persistence_mode": persistence.mode,
        "persistence_available": persistence.available,
        "catalog_unavailable": catalog_unavailable,
        "catalog_unavailable_reason": catalog_unavailable_reason,
        "definition_trust": definition_trust,
        "client": persistence.client if persistence.mode == "persisted" and persistence.available else None,
        "strict_separation_of_duties": bool(
            persistence.config.strict_separation_of_duties if persistence.config is not None else False
        ),
        "audit": [
            {
                "event": "Loaded metric catalog",
                "source": audit_source,
                "model": "None",
            }
        ],
    }


def main() -> None:
    state = build_state()
    render_app(state)


if __name__ == "__main__":
    main()
