"""Tests for apps/data_quality_triage/checks.py."""

from __future__ import annotations

import pytest

from apps.data_quality_triage.checks import (
    GUARDRAILS_CATALOG,
    build_audit_event_for_incident,
    build_incident_from_finding,
    check_empty_table,
    check_missing_primary_candidate,
    check_schema_drift,
    check_stale_freshness,
    classify_ratio_severity,
    classify_volume_drift_severity,
    collapse_severity,
    findings_to_incidents,
    run_metadata_checks,
    status_for_severity,
)
from apps.data_quality_triage.models import SchemaSnapshot, TableFinding
from apps.data_quality_triage.profiling import TableProfile
from shared.incidents import ACTIVE_INCIDENT_STATUSES


def _profile(**overrides) -> TableProfile:
    defaults = dict(
        table_id="order_items",
        row_count=181_594,
        last_modified="2026-07-11T09:00:00+00:00",
        freshness_minutes=15.0,
        primary_candidate="order_id",
        nullable_candidates=["status"],
        temporal_candidates=["created_at"],
    )
    defaults.update(overrides)
    return TableProfile(**defaults)


# ---------------------------------------------------------------------------
# classify_ratio_severity / classify_volume_drift_severity / status_for_severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (0.0, "low"),
        (0.0005, "low"),
        (0.001, "medium"),
        (0.005, "medium"),
        (0.01, "high"),
        (0.03, "high"),
        (0.05, "critical"),
        (0.2, "critical"),
    ],
)
def test_classify_ratio_severity_against_duplicate_thresholds(ratio, expected):
    assert (
        classify_ratio_severity(ratio, medium=0.001, high=0.01, critical=0.05) == expected
    )


def test_classify_volume_drift_severity_returns_none_within_normal_band():
    assert classify_volume_drift_severity(1.0) is None
    assert classify_volume_drift_severity(0.6) is None
    assert classify_volume_drift_severity(1.4) is None


@pytest.mark.parametrize("ratio", [0.5, 0.3, 1.5, 2.5])
def test_classify_volume_drift_severity_returns_high_at_the_flagged_boundary(ratio):
    assert classify_volume_drift_severity(ratio) == "high"


@pytest.mark.parametrize("ratio", [0.2, 0.05, 3.0, 10.0])
def test_classify_volume_drift_severity_returns_critical_at_the_extreme_boundary(ratio):
    assert classify_volume_drift_severity(ratio) == "critical"


def test_status_for_severity_mapping():
    assert status_for_severity("low") == "pass"
    assert status_for_severity("medium") == "warn"
    assert status_for_severity("high") == "fail"
    assert status_for_severity("critical") == "fail"


# ---------------------------------------------------------------------------
# Metadata-only checks
# ---------------------------------------------------------------------------


def test_check_empty_table_fires_when_row_count_is_zero():
    finding = check_empty_table(_profile(row_count=0))
    assert finding is not None
    assert finding.check_name == "row_count_empty"
    assert finding.severity == "critical"
    assert finding.status == "fail"


def test_check_empty_table_passes_when_rows_exist():
    assert check_empty_table(_profile(row_count=1)) is None


def test_check_stale_freshness_fires_past_the_threshold():
    finding = check_stale_freshness(_profile(freshness_minutes=3000), stale_after_minutes=2880)
    assert finding is not None
    assert finding.check_name == "freshness_delay"
    assert finding.severity == "high"


def test_check_stale_freshness_passes_within_the_threshold():
    assert check_stale_freshness(_profile(freshness_minutes=10), stale_after_minutes=2880) is None


def test_check_stale_freshness_passes_when_freshness_is_unknown():
    # No modified time reported at all -- must not be treated as "infinitely
    # stale."
    assert check_stale_freshness(_profile(freshness_minutes=None)) is None


def test_check_missing_primary_candidate_fires_when_none_found():
    finding = check_missing_primary_candidate(_profile(primary_candidate=None))
    assert finding is not None
    assert finding.check_name == "no_primary_key_candidate"
    assert finding.severity == "low"
    assert finding.status == "warn"


def test_check_missing_primary_candidate_passes_when_a_candidate_exists():
    assert check_missing_primary_candidate(_profile(primary_candidate="order_id")) is None


def test_run_metadata_checks_returns_only_fired_findings_plus_schema_drift():
    profile = _profile(row_count=0, freshness_minutes=None, primary_candidate=None)
    findings = run_metadata_checks(profile)
    check_names = {f.check_name for f in findings}
    # freshness is unknown (None), so freshness_delay must not fire even
    # though the table is otherwise clearly unhealthy. schema_drift always
    # appears (as not_evaluated here, since no baseline was supplied) --
    # see run_metadata_checks()'s docstring.
    assert check_names == {"row_count_empty", "no_primary_key_candidate", "schema_drift"}


def test_run_metadata_checks_returns_only_schema_drift_not_evaluated_for_a_healthy_profile():
    findings = run_metadata_checks(_profile())
    assert len(findings) == 1
    assert findings[0].check_name == "schema_drift"
    assert findings[0].status == "not_evaluated"


def test_run_metadata_checks_reports_schema_drift_pass_when_baseline_matches():
    profile = _profile(column_types={"id": "INTEGER", "status": "STRING"})
    baseline = SchemaSnapshot(
        table_id="order_items",
        captured_at="2026-07-01T00:00:00Z",
        columns={"id": "INTEGER", "status": "STRING"},
    )
    findings = run_metadata_checks(profile, schema_baseline=baseline)
    assert len(findings) == 1
    assert findings[0].check_name == "schema_drift"
    assert findings[0].status == "pass"


# ---------------------------------------------------------------------------
# check_schema_drift (Phase 4 correction item 1)
# ---------------------------------------------------------------------------


_BASELINE = SchemaSnapshot(
    table_id="order_items",
    captured_at="2026-07-01T00:00:00Z",
    columns={"id": "INTEGER", "order_id": "INTEGER", "status": "STRING"},
)


def test_check_schema_drift_returns_not_evaluated_with_no_baseline():
    profile = _profile(column_types={"id": "INTEGER", "order_id": "INTEGER", "status": "STRING"})
    finding = check_schema_drift(profile, None)
    assert finding.check_name == "schema_drift"
    assert finding.status == "not_evaluated"
    assert finding.observed_value is None


def test_check_schema_drift_passes_when_schema_is_unchanged():
    profile = _profile(column_types=dict(_BASELINE.columns))
    finding = check_schema_drift(profile, _BASELINE)
    assert finding.status == "pass"
    assert finding.severity == "low"
    assert finding.observed_value == 0.0


def test_check_schema_drift_warns_on_additions_only():
    profile = _profile(column_types={**_BASELINE.columns, "notes": "STRING"})
    finding = check_schema_drift(profile, _BASELINE)
    assert finding.status == "warn"
    assert finding.severity == "medium"
    assert "added: notes" in finding.summary


def test_check_schema_drift_fails_on_removal():
    current = dict(_BASELINE.columns)
    del current["status"]
    profile = _profile(column_types=current)
    finding = check_schema_drift(profile, _BASELINE)
    assert finding.status == "fail"
    assert finding.severity == "high"
    assert "removed: status" in finding.summary


def test_check_schema_drift_fails_on_type_change():
    current = {**_BASELINE.columns, "status": "INTEGER"}
    profile = _profile(column_types=current)
    finding = check_schema_drift(profile, _BASELINE)
    assert finding.status == "fail"
    assert finding.severity == "high"
    assert "type changed: status (STRING->INTEGER)" in finding.summary


def test_check_schema_drift_detects_a_rename_candidate_via_matching_name_and_type():
    # "status" (STRING) disappears while "state" (STRING) appears -- same
    # type, so it's reported as a renamed candidate rather than a separate
    # add + remove.
    current = dict(_BASELINE.columns)
    del current["status"]
    current["state"] = "STRING"
    profile = _profile(column_types=current)
    finding = check_schema_drift(profile, _BASELINE)
    assert finding.status == "fail"
    assert "renamed (candidate): status->state" in finding.summary
    assert "added:" not in finding.summary
    assert "removed:" not in finding.summary


def test_check_schema_drift_each_column_participates_in_at_most_one_rename_pairing():
    # Two removed STRING columns, only one same-typed addition: only one
    # rename pairing is formed, the other removal stays a pure removal.
    baseline = SchemaSnapshot(
        table_id="order_items",
        captured_at="2026-07-01T00:00:00Z",
        columns={"id": "INTEGER", "status": "STRING", "notes": "STRING"},
    )
    profile = _profile(column_types={"id": "INTEGER", "state": "STRING"})
    finding = check_schema_drift(profile, baseline)
    assert finding.status == "fail"
    # Exactly one of {status, notes} is paired as a rename candidate to
    # "state"; the other is reported as a pure removal.
    assert "renamed (candidate):" in finding.summary
    assert "removed:" in finding.summary


# ---------------------------------------------------------------------------
# Guardrails catalog
# ---------------------------------------------------------------------------


def test_guardrails_catalog_has_eight_entries_covering_every_implemented_check():
    # Grew from six to eight in the Phase 4 correction pass: Schema Drift
    # and Query Exception fill the two documented categories that
    # previously had no deterministic implementation.
    names = {entry.name for entry in GUARDRAILS_CATALOG}
    assert names == {
        "Row Count / Empty Table",
        "Freshness Delay",
        "Schema Drift",
        "Volume Drift",
        "Null Spike",
        "Duplicate Key Growth",
        "Query Exception",
        "Primary Key Candidate Missing",
    }


def test_guardrails_catalog_entries_all_have_valid_severities():
    for entry in GUARDRAILS_CATALOG:
        assert entry.severity in {"low", "medium", "high", "critical"}


# ---------------------------------------------------------------------------
# collapse_severity / build_incident_from_finding / findings_to_incidents
# ---------------------------------------------------------------------------


def test_collapse_severity_maps_critical_to_high():
    assert collapse_severity("critical") == "high"


def test_collapse_severity_leaves_high_medium_low_unchanged():
    assert collapse_severity("high") == "high"
    assert collapse_severity("medium") == "medium"
    assert collapse_severity("low") == "low"


def test_build_incident_from_finding_raises_for_a_passing_finding():
    finding = TableFinding(
        table_id="order_items",
        check_name="row_count_empty",
        status="pass",
        severity="low",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    with pytest.raises(ValueError):
        build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")


def test_build_incident_from_finding_starts_status_open_even_for_critical_severity():
    # Behavioral change vs. the original app, which set status="investigating"
    # directly for high/critical findings -- see checks.py's docstring.
    finding = TableFinding(
        table_id="order_items",
        check_name="row_count_empty",
        status="fail",
        severity="critical",
        observed_value=0.0,
        threshold=0.0,
        summary="s",
        likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    assert incident.status == "open"
    assert incident.severity == "high"  # collapsed from "critical"


def test_build_incident_from_finding_populates_affected_metrics_from_the_catalog():
    finding = TableFinding(
        table_id="order_items",
        check_name="duplicate_key_ratio",
        status="fail",
        severity="high",
        observed_value=0.02,
        threshold=0.01,
        summary="s",
        likely_root_cause="r",
        sql="SELECT 1",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    # order_items is an approved source table for revenue, margin,
    # return_rate, and margin_leakage in the real catalog.
    assert "revenue" in incident.affected_metrics
    assert "margin" in incident.affected_metrics
    assert incident.sql_template == "SELECT 1"
    assert incident.query_hash is not None


def test_build_incident_from_finding_generates_a_deterministic_incident_id():
    finding = TableFinding(
        table_id="order_items",
        check_name="row_count_empty",
        status="fail",
        severity="high",
        observed_value=0.0,
        threshold=0.0,
        summary="s",
        likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    assert incident.incident_id == "thelook_ecommerce.order_items.row_count_empty.2026-07-11T00:00:00Z"


def test_findings_to_incidents_skips_passing_findings():
    passing = TableFinding(
        table_id="t", check_name="c1", status="pass", severity="low",
        observed_value=None, threshold=None, summary="s", likely_root_cause="r",
    )
    failing = TableFinding(
        table_id="t", check_name="c2", status="fail", severity="high",
        observed_value=1.0, threshold=0.5, summary="s", likely_root_cause="r",
    )
    incidents = findings_to_incidents(
        [passing, failing], dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z"
    )
    assert len(incidents) == 1
    assert incidents[0].check_type == "c2"


def test_build_incident_from_finding_raises_for_a_not_evaluated_finding():
    finding = TableFinding(
        table_id="order_items",
        check_name="schema_drift",
        status="not_evaluated",
        severity="low",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    with pytest.raises(ValueError):
        build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")


def test_build_incident_from_finding_promotes_an_error_status_finding():
    # A query_exception finding ("error" status) DOES become an incident --
    # an inability to run a check is itself an operability problem worth
    # surfacing, unlike "pass"/"not_evaluated".
    finding = TableFinding(
        table_id="order_items",
        check_name="query_exception",
        status="error",
        severity="high",
        observed_value=None,
        threshold=None,
        summary="s",
        likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    assert incident.status == "open"
    assert incident.severity == "high"


def test_findings_to_incidents_skips_not_evaluated_findings():
    not_evaluated = TableFinding(
        table_id="t", check_name="schema_drift", status="not_evaluated", severity="low",
        observed_value=None, threshold=None, summary="s", likely_root_cause="r",
    )
    failing = TableFinding(
        table_id="t", check_name="c2", status="fail", severity="high",
        observed_value=1.0, threshold=0.5, summary="s", likely_root_cause="r",
    )
    incidents = findings_to_incidents(
        [not_evaluated, failing], dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z"
    )
    assert len(incidents) == 1
    assert incidents[0].check_type == "c2"


# ---------------------------------------------------------------------------
# Detected vs. open (Phase 4 correction item 3)
# ---------------------------------------------------------------------------


def test_build_incident_from_finding_never_starts_an_incident_as_detected():
    # "detected" is reserved for raw, unconfirmed monitoring signals (see
    # shared/incidents.py); every finding reaching this function already
    # came from a completed deterministic check that breached its rule, so
    # "open" -- the first status for a *confirmed* incident -- is always
    # the correct starting point, never "detected".
    assert "detected" not in ACTIVE_INCIDENT_STATUSES
    assert "open" in ACTIVE_INCIDENT_STATUSES

    finding = TableFinding(
        table_id="order_items", check_name="row_count_empty", status="fail", severity="critical",
        observed_value=0.0, threshold=0.0, summary="s", likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    assert incident.status == "open"
    assert incident.status != "detected"
    assert incident.status in ACTIVE_INCIDENT_STATUSES


# ---------------------------------------------------------------------------
# build_audit_event_for_incident (Phase 4 correction item 2)
# ---------------------------------------------------------------------------


def test_build_audit_event_for_incident_retains_the_original_local_severity():
    finding = TableFinding(
        table_id="order_items", check_name="row_count_empty", status="fail", severity="critical",
        observed_value=0.0, threshold=0.0, summary="s", likely_root_cause="r",
    )
    incident = build_incident_from_finding(finding, dataset="thelook_ecommerce", created_at="2026-07-11T00:00:00Z")
    assert incident.severity == "high"  # collapsed

    event = build_audit_event_for_incident(
        incident, finding, event_id="evt_1", timestamp="2026-07-11T00:00:00Z"
    )
    assert event.context["local_severity"] == "critical"
    assert event.context["collapsed_severity"] == "high"
    assert event.context["table_id"] == "order_items"
    assert event.context["check_name"] == "row_count_empty"
    assert event.context["check_status"] == "fail"
    assert event.subject == incident.incident_id
    assert event.event_type == "incident_created"
