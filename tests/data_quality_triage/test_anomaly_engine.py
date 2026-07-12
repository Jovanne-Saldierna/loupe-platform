"""Tests for apps/data_quality_triage/anomaly_engine.py.

Uses the FakeBigQueryClient from tests/shared/conftest.py (imported
directly) since anomaly_engine.py routes every query through
shared.data_service.run_query(), which that fake already backs.
"""

from __future__ import annotations

from apps.data_quality_triage.anomaly_engine import (
    _classify_exception,
    _query_exception_finding,
    evaluate_profiles,
)
from apps.data_quality_triage.models import SchemaSnapshot
from apps.data_quality_triage.profiling import TableProfile
from tests.shared.conftest import FakeBigQueryClient

DATASET = "bigquery-public-data.thelook_ecommerce"


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


def test_evaluate_profiles_returns_only_a_passing_schema_drift_finding_when_everything_is_clean():
    # run_metadata_checks() always includes schema_drift's result, even
    # when it's a "pass" (see checks.check_schema_drift()'s docstring) --
    # so "clean" now means the sole surviving finding is that pass, not an
    # empty list. Supplying a baseline that matches the profile's (empty,
    # since this fixture sets no column_types) columns is what produces
    # "pass" rather than the not_evaluated default.
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.0}]
    baseline = SchemaSnapshot(table_id="order_items", captured_at="2026-07-01T00:00:00Z", columns={})
    findings = evaluate_profiles(client, DATASET, [_profile()], schema_baselines={"order_items": baseline})
    assert len(findings) == 1
    assert findings[0].check_name == "schema_drift"
    assert findings[0].status == "pass"


def test_evaluate_profiles_includes_a_not_evaluated_schema_drift_finding_with_no_baseline():
    # Without schema_baselines, schema_drift always reports not_evaluated
    # rather than being silently skipped -- see checks.check_schema_drift().
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.0}]
    findings = evaluate_profiles(client, DATASET, [_profile()])
    assert len(findings) == 1
    assert findings[0].check_name == "schema_drift"
    assert findings[0].status == "not_evaluated"


def test_evaluate_profiles_flags_a_high_duplicate_ratio():
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.02}]  # above the 0.01 "high" threshold
    findings = evaluate_profiles(client, DATASET, [_profile()])
    duplicate_findings = [f for f in findings if f.check_name == "duplicate_key_ratio"]
    assert len(duplicate_findings) == 1
    assert duplicate_findings[0].severity == "high"
    assert duplicate_findings[0].sql is not None
    assert "order_id" in duplicate_findings[0].sql


def test_evaluate_profiles_skips_duplicate_check_when_no_primary_candidate():
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.9}]  # would fire if the check ran at all
    findings = evaluate_profiles(client, DATASET, [_profile(primary_candidate=None)])
    assert all(f.check_name != "duplicate_key_ratio" for f in findings)


def test_evaluate_profiles_skips_null_check_when_no_nullable_candidates():
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.9}]
    findings = evaluate_profiles(client, DATASET, [_profile(nullable_candidates=[])])
    assert all(f.check_name != "null_ratio" for f in findings)


def test_evaluate_profiles_skips_volume_drift_when_no_temporal_candidates():
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.0}]
    findings = evaluate_profiles(client, DATASET, [_profile(temporal_candidates=[])])
    assert all(f.check_name != "volume_drift" for f in findings)


def test_evaluate_profiles_includes_metadata_findings_alongside_live_findings():
    # Confirms the dead-code fix: an empty table (metadata-only check) must
    # be flagged even when the live ratio queries all come back clean.
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.0}]
    findings = evaluate_profiles(client, DATASET, [_profile(row_count=0)])
    check_names = {f.check_name for f in findings}
    assert "row_count_empty" in check_names


def test_evaluate_profiles_covers_every_profile_passed_in():
    client = FakeBigQueryClient()
    client.next_rows = [{"ratio": 0.0}]
    profiles = [_profile(table_id="order_items"), _profile(table_id="dim_customers", row_count=0)]
    findings = evaluate_profiles(client, DATASET, profiles)
    table_ids = {f.table_id for f in findings}
    assert "dim_customers" in table_ids


# ---------------------------------------------------------------------------
# Query exceptions (Phase 4 correction item 1, "Query exceptions")
# ---------------------------------------------------------------------------


def test_classify_exception_detects_timeout():
    assert _classify_exception(TimeoutError("Deadline Exceeded while running query")) == "timeout"


def test_classify_exception_detects_permission_denied():
    assert _classify_exception(PermissionError("403 Forbidden: caller lacks bigquery.jobs.create")) == "permission_denied"


def test_classify_exception_detects_malformed_query():
    assert _classify_exception(ValueError("Syntax error: Unexpected token at [1:8]")) == "malformed_query"


def test_classify_exception_falls_back_to_execution_failure_for_unrecognized_errors():
    assert _classify_exception(RuntimeError("connection reset by peer")) == "execution_failure"


def test_query_exception_finding_never_echoes_the_raw_exception_message():
    # The raw message could contain credentials, bound parameter values,
    # or query text a warehouse driver embeds in its error -- none of that
    # may ever reach a TableFinding.
    exc = RuntimeError("service_account_key=SECRET123 failed for user@example.com; SELECT * FROM secrets")
    finding = _query_exception_finding(table_id="order_items", check_name="duplicate_key_ratio", exc=exc)
    assert finding.status == "error"
    assert finding.check_name == "query_exception"
    assert finding.severity == "high"
    assert finding.sql is None
    assert "SECRET123" not in finding.summary
    assert "SECRET123" not in finding.likely_root_cause
    assert "user@example.com" not in finding.summary
    assert "duplicate_key_ratio" in finding.summary  # the *originating* check name is safe to surface
    assert "order_items" in finding.summary


def test_evaluate_profiles_converts_a_timeout_into_an_error_finding():
    client = FakeBigQueryClient()
    client.query_exception = TimeoutError("Deadline Exceeded")
    findings = evaluate_profiles(client, DATASET, [_profile()])
    exception_findings = [f for f in findings if f.check_name == "query_exception"]
    assert exception_findings  # duplicate/null/volume-drift queries all fail
    assert all(f.status == "error" for f in exception_findings)
    assert all("Deadline Exceeded" not in f.summary for f in exception_findings)


def test_evaluate_profiles_converts_a_permission_failure_into_an_error_finding():
    client = FakeBigQueryClient()
    client.query_exception = PermissionError("403 Forbidden")
    findings = evaluate_profiles(client, DATASET, [_profile()])
    exception_findings = [f for f in findings if f.check_name == "query_exception"]
    assert exception_findings
    assert all(f.status == "error" and f.severity == "high" for f in exception_findings)


def test_evaluate_profiles_converts_a_malformed_query_into_an_error_finding():
    client = FakeBigQueryClient()
    client.query_exception = ValueError("Syntax error near SELECT")
    findings = evaluate_profiles(client, DATASET, [_profile()])
    exception_findings = [f for f in findings if f.check_name == "query_exception"]
    assert exception_findings
    assert all(f.status == "error" for f in exception_findings)


def test_evaluate_profiles_converts_a_generic_execution_failure_into_an_error_finding():
    client = FakeBigQueryClient()
    client.query_exception = RuntimeError("connection reset by peer")
    findings = evaluate_profiles(client, DATASET, [_profile()])
    exception_findings = [f for f in findings if f.check_name == "query_exception"]
    assert exception_findings
    assert all(f.status == "error" for f in exception_findings)


def test_evaluate_profiles_does_not_abort_the_whole_run_when_a_query_raises():
    # A raising client.query() would, if uncaught, blow up evaluate_profiles()
    # entirely -- the fact that this call returns normally (rather than
    # raising out of the test) demonstrates each check's query failure is
    # caught and isolated per-check via _safe_run_query(), not left to
    # propagate.
    client = FakeBigQueryClient()
    client.query_exception = RuntimeError("connection reset by peer")
    findings = evaluate_profiles(client, DATASET, [_profile()])
    assert isinstance(findings, list)
    assert len(findings) >= 1
