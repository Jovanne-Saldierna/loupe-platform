"""Proves two things about tests/data_quality_triage/fixtures.py's
FICTIONAL sample incident data:

1. It is realistic enough to exercise remediation.py / explanations.py /
   chat.py without errors (a generic pipeline smoke test).
2. It is never imported by anything under apps/data_quality_triage/ --
   i.e. the original app's INC-1042/INC-1043 fictional fallback data
   cannot reach a real running app through this migration.
"""

from __future__ import annotations

from pathlib import Path

import apps.data_quality_triage.chat as chat
import apps.data_quality_triage.explanations as explanations
from apps.data_quality_triage.remediation import estimate_impact, suggested_playbooks_for_incident
from tests.data_quality_triage.fixtures import (
    SAMPLE_CHECK_DEFINITIONS,
    SAMPLE_FINDING_1042,
    SAMPLE_FINDINGS,
    SAMPLE_INCIDENT_1042,
    SAMPLE_INCIDENT_1043,
    SAMPLE_INCIDENTS,
)

APPS_DIR = Path(__file__).resolve().parents[2] / "apps" / "data_quality_triage"


# ---------------------------------------------------------------------------
# 1. Fixtures are usable, realistic-shaped inputs to the real pipeline
# ---------------------------------------------------------------------------


def test_sample_incidents_produce_playbooks():
    playbooks = suggested_playbooks_for_incident(SAMPLE_INCIDENT_1042)
    assert playbooks
    assert playbooks[0].startswith("Escalate to the table owner")  # high severity


def test_sample_incidents_produce_an_impact_estimate():
    level, summary = estimate_impact(SAMPLE_INCIDENTS)
    assert level == "High"  # one high-severity incident among the two
    assert summary


def test_sample_incident_narrates_with_the_deterministic_fallback(monkeypatch):
    monkeypatch.setattr(explanations, "_anthropic_api_key", lambda: "")
    explanation = explanations.narrate_incident(SAMPLE_INCIDENT_1042, SAMPLE_FINDING_1042)
    assert explanation.incident_id == "INC-1042"
    assert explanation.used_claude is False
    assert "duplicate_key_ratio" in explanation.narrative


def test_sample_state_summarizes_for_chat():
    state = {"incidents": SAMPLE_INCIDENTS, "source_health": [], "persistence_available": False}
    summary = chat.summarize_state_for_chat(state)
    assert "fct_orders" in summary
    assert "dim_customers" in summary


def test_sample_check_definitions_are_valid_check_definitions():
    names = {c.name for c in SAMPLE_CHECK_DEFINITIONS}
    assert names == {"Row Count Drop", "Null Spike", "Duplicate Key Growth", "Freshness Delay"}


def test_sample_findings_correlate_with_sample_incidents_by_table_and_check_type():
    for incident, finding in zip(SAMPLE_INCIDENTS, SAMPLE_FINDINGS):
        assert incident.table_id == finding.table_id
        assert incident.check_type == finding.check_name


# ---------------------------------------------------------------------------
# 2. Never reachable from the running app
# ---------------------------------------------------------------------------


def test_fixtures_module_is_never_imported_by_the_app():
    # Checks actual import statements, not prose -- several apps/ module
    # docstrings legitimately *discuss* the removed sample_data fallback
    # and the retired INC-1042/INC-1043 example incident_ids (to document
    # the behavioral change), which would otherwise false-positive a
    # naive substring search. What must never appear is an import of the
    # fixtures module or the original sample_data module.
    app_source_files = sorted(APPS_DIR.glob("*.py"))
    assert app_source_files, "expected apps/data_quality_triage/*.py to exist"

    offending: list[str] = []
    for path in app_source_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                if "fixtures" in stripped or "sample_data" in stripped:
                    offending.append(f"{path.name}: {stripped}")

    assert offending == [], (
        f"apps/data_quality_triage/ files import fixture/sample data, which must never reach "
        f"the running app: {offending}"
    )


def test_no_apps_module_imports_the_tests_package():
    # A stronger, structural version of the same guarantee: nothing under
    # apps/ may import from the tests/ package at all.
    app_source_files = sorted(APPS_DIR.glob("*.py"))
    offending = [path.name for path in app_source_files if "import tests" in path.read_text(encoding="utf-8") or "from tests" in path.read_text(encoding="utf-8")]
    assert offending == []
