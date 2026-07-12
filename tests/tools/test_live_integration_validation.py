"""Credential-free tests for
tools/phase6e_ops/live_integration_validation.py.

Covers the safety-gate, dry-run, and argument-validation paths (never
touch BigQuery), plus cleanup_run()'s SQL shape against a fake client
(proving it only ever deletes run-tagged rows from the three per-run
tables, and never references metric_catalog/metric_versions/write_locks
at all). The full run_validation() live proof sequence is deliberately
NOT covered here -- it requires a real, authenticated BigQuery client
against an already-bootstrapped loupe_platform_test; see
docs/persistence.md's "Live integration command" section for how an
operator actually runs it.
"""

from __future__ import annotations

from pathlib import Path

from tools.phase6e_ops.live_integration_validation import _incident_id, cleanup_run, main
from tests.shared.conftest import FakeBigQueryClient


def test_dry_run_prints_the_plan_and_returns_zero_without_yes(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--actor", "test-operator",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Dry run only" in out
    assert "bootstrap_test_dataset" in out


def test_refuses_the_real_production_dataset_name(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform",
            "--location", "US",
            "--actor", "test-operator",
            "--yes",
        ]
    )
    assert exit_code == 2
    assert "must never be" in capsys.readouterr().out


def test_requires_actor_unless_cleanup_only(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--yes",
        ]
    )
    assert exit_code == 2
    assert "--actor is required" in capsys.readouterr().out


def test_cleanup_only_requires_run_id(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--cleanup-only",
        ]
    )
    assert exit_code == 2
    assert "--run-id is required" in capsys.readouterr().out


def test_cleanup_only_rejects_a_malformed_run_id(capsys):
    exit_code = main(
        [
            "--project", "ai-weekend-agent-501502",
            "--dataset", "loupe_platform_test",
            "--location", "US",
            "--cleanup-only",
            "--run-id", "not-a-real-run-id",
        ]
    )
    assert exit_code == 2


def test_incident_id_is_deterministic_and_tagged_with_the_run_id():
    incident_id = _incident_id("abc1234567", "2026-07-12T00:00:00Z")
    assert "abc1234567" in incident_id
    assert incident_id.startswith("bigquery-public-data.thelook_ecommerce.orders.phase6e_integration_")


def test_cleanup_run_only_issues_deletes_scoped_to_the_run_tag_and_never_touches_the_catalog():
    # Note: this test does NOT set LOUPE_DATASET, because
    # shared.incident_persistence/shared.audit_persistence's table
    # constants are resolved once at each module's FIRST import in this
    # process (see tests/shared/test_dataset_parameterization.py, which
    # verifies the dataset-parameterization itself via subprocess
    # isolation specifically because in-process monkeypatching cannot
    # affect an already-imported module's constants). This test instead
    # asserts the SHAPE of cleanup_run()'s DELETEs -- run-tag scoping and
    # never referencing the catalog/lock tables -- which holds regardless
    # of which dataset those constants happened to resolve to.
    from shared.audit_persistence import AUDIT_EVENTS_TABLE
    from shared.incident_persistence import INCIDENT_TRANSITIONS_TABLE, INCIDENTS_TABLE

    client = FakeBigQueryClient()

    cleanup_run(client=client, run_id="abc1234567")

    assert len(client.queries) == 3
    for sql, _ in client.queries:
        assert sql.strip().upper().startswith("DELETE FROM")
        assert "abc1234567" in sql
        # Never touches the seeded catalog or the write-lock rows -- only
        # the three per-run tables this validation itself writes to.
        assert "metric_catalog" not in sql
        assert "metric_versions" not in sql
        assert "write_locks" not in sql
    tables_touched = {sql.split("`")[1] for sql, _ in client.queries}
    assert tables_touched == {INCIDENT_TRANSITIONS_TABLE, AUDIT_EVENTS_TABLE, INCIDENTS_TABLE}


# ---------------------------------------------------------------------------
# "The live integration script and the real UI must exercise the same
# lifecycle service path, not separate implementations."
# ---------------------------------------------------------------------------


def test_resolution_step_calls_the_same_lifecycle_wrapper_the_ui_calls():
    # A static source check, mirroring tests/test_persistence_boundary.py's
    # approach for a parallel guarantee: this script's resolution step
    # must go through apps.data_quality_triage.incident_lifecycle.resolve_incident()
    # -- the exact function apps/data_quality_triage/ui.py's "Resolve"
    # button calls -- rather than calling
    # shared.incident_persistence.record_incident_transition() directly.
    # A regression here (reintroducing a second, separate implementation)
    # would defeat the entire point of "the live script proves what the
    # real UI does."
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "tools" / "phase6e_ops" / "live_integration_validation.py").read_text(encoding="utf-8")

    assert "from apps.data_quality_triage.incident_lifecycle import (" in source
    assert "acknowledge_incident," in source
    assert "begin_investigation," in source
    assert "resolve_incident," in source
    assert "resolve_incident(" in source
    # The module-level import list at the top of the file may still
    # reference record_incident_transition's sibling functions for other
    # purposes, but run_validation()'s resolution step itself must not
    # call shared.incident_persistence.record_incident_transition
    # directly -- only ever via the incident_lifecycle wrapper.
    assert "from shared.incident_persistence import record_incident_transition" not in source


def test_ui_module_imports_the_same_lifecycle_functions_the_live_script_relies_on():
    repo_root = Path(__file__).resolve().parents[2]
    ui_source = (repo_root / "apps" / "data_quality_triage" / "ui.py").read_text(encoding="utf-8")
    for name in ("acknowledge_incident", "begin_investigation", "mark_mitigated", "resolve_incident", "reopen_incident"):
        assert name in ui_source, f"ui.py must reference {name} from incident_lifecycle"
    assert "from apps.data_quality_triage.incident_lifecycle import" in ui_source
