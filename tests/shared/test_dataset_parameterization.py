"""Phase 6E correction: every persistence module's table-name constants
must be derived from LOUPE_DATASET, never hardcoded to "loupe_platform".

Before this correction, shared/data_service.py, shared/audit.py, shared/
audit_persistence.py, shared/incident_persistence.py, shared/
metric_catalog_persistence.py, and shared/schema_baseline_persistence.py
all baked "loupe_platform.<table>" as a literal string constant, computed
once at import time and burned directly into each registered
StatementTemplate's SQL. That meant setting LOUPE_DATASET=loupe_platform_test
(exactly what shared.config.load_platform_config() already reads, and
exactly what an operator running a guarded test-dataset validation would
set) had NO effect on where these modules actually read or wrote --
every persisted call would have silently targeted production
`loupe_platform` regardless of configuration. This is a real correctness
gap, not a style nit: it is the thing that made "use the existing
loupe_platform_test dataset only" impossible to honor safely before this
fix.

These tests prove the fix by re-importing each affected module in a
subprocess with LOUPE_DATASET set to a distinct test value and asserting
its table constants reflect it. A subprocess (rather than importlib.reload
in-process) is used because these modules register their StatementTemplate
instances into shared.persistence_transactions' process-global _TEMPLATES
registry at import time -- reloading in-process would either raise
register_template()'s "different StatementTemplate already registered
under this name" error or leave stale, non-reloaded downstream imports
(e.g. shared.metric_catalog_persistence imports WRITE_AUDIT_EVENT_TXN from
an already-imported shared.audit_persistence) still pointing at the OLD
constant. A fresh subprocess is the same isolation every real operator
invocation (a fresh `python -m ...` process per command) already gets, so
this is not a synthetic test-only environment.
"""

from __future__ import annotations

import subprocess
import sys

from shared.config import PlatformConfig
from shared.models import AuditEvent

_CASES = [
    ("shared.data_service", "INCIDENTS_TABLE", "incidents"),
    ("shared.audit", "AUDIT_TABLE", "audit_events"),
    ("shared.audit_persistence", "AUDIT_EVENTS_TABLE", "audit_events"),
    ("shared.audit_persistence", "WRITE_LOCKS_TABLE", "write_locks"),
    ("shared.incident_persistence", "INCIDENTS_TABLE", "incidents"),
    ("shared.incident_persistence", "INCIDENT_TRANSITIONS_TABLE", "incident_transitions"),
    ("shared.metric_catalog_persistence", "METRIC_CATALOG_TABLE", "metric_catalog"),
    ("shared.metric_catalog_persistence", "METRIC_VERSIONS_TABLE", "metric_versions"),
    ("shared.schema_baseline_persistence", "SCHEMA_BASELINES_TABLE", "schema_baselines"),
    ("shared.schema_baseline_persistence", "AUDIT_EVENTS_TABLE", "audit_events"),
]


def _read_constant_in_subprocess(module_name: str, constant_name: str, env_dataset: str | None) -> str:
    import os

    env = dict(os.environ)
    if env_dataset is not None:
        env["LOUPE_DATASET"] = env_dataset
    else:
        env.pop("LOUPE_DATASET", None)
    code = f"import {module_name} as m; print(m.{constant_name})"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    return result.stdout.strip()


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_every_table_constant_defaults_to_loupe_platform_when_unset():
    for module_name, constant_name, suffix in _CASES:
        value = _read_constant_in_subprocess(module_name, constant_name, env_dataset=None)
        assert value == f"loupe_platform.{suffix}", (module_name, constant_name, value)


def test_every_table_constant_follows_loupe_dataset_when_set_to_the_test_dataset():
    for module_name, constant_name, suffix in _CASES:
        value = _read_constant_in_subprocess(
            module_name, constant_name, env_dataset="loupe_platform_test"
        )
        assert value == f"loupe_platform_test.{suffix}", (module_name, constant_name, value)
        # The whole point: it must NEVER still be pointed at production
        # when a distinct dataset was explicitly configured.
        assert "loupe_platform." not in value or value.startswith("loupe_platform_test")


def test_explicit_config_overrides_already_imported_audit_template():
    from shared.audit_persistence import write_event_idempotent
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    client = SequencedFakeBigQueryClient(
        rows_per_call=[[{"event_id": "evt_1", "event_type": "test", "subject": "subject", "outcome": "ok"}]]
    )
    config = PlatformConfig(project="test-project", dataset="loupe_platform_test")
    event = AuditEvent(
        event_id="evt_1",
        timestamp="2026-07-12T00:00:00Z",
        actor="tester",
        event_type="test",
        subject="subject",
        outcome="ok",
    )

    write_event_idempotent(client, event, config=config)

    sql, _ = client.queries[0]
    assert "`loupe_platform_test.audit_events`" in sql
    assert "`loupe_platform_test.write_locks`" in sql
    assert "`loupe_platform.audit_events`" not in sql


def test_explicit_config_overrides_already_imported_schema_baseline_templates():
    from shared.schema_baseline_persistence import get_schema_baseline, promote_schema_baseline
    from tests.data_quality_triage.conftest import SequencedFakeBigQueryClient

    config = PlatformConfig(project="test-project", dataset="loupe_platform_test")
    client = SequencedFakeBigQueryClient(
        rows_per_call=[
            [{"dataset": "source", "table_id": "orders", "source_snapshot_id": "snap_1", "promoted_at": "2026-07-12T00:00:00Z", "promoted_by": "tester"}],
            [],
        ]
    )

    promote_schema_baseline(
        client,
        dataset="source",
        table_id="orders",
        columns={"id": "STRING"},
        source_snapshot_id="snap_1",
        promoted_by="tester",
        event_id="evt_baseline_1",
        event_timestamp="2026-07-12T00:00:00Z",
        config=config,
    )
    get_schema_baseline(client, dataset="source", table_id="orders", config=config)

    promote_sql, _ = client.queries[0]
    read_sql, _ = client.queries[1]
    assert "`loupe_platform_test.schema_baselines`" in promote_sql
    assert "`loupe_platform_test.audit_events`" in promote_sql
    assert "`loupe_platform_test.write_locks`" in promote_sql
    assert "`loupe_platform_test.schema_baselines`" in read_sql
    assert "`loupe_platform.schema_baselines`" not in promote_sql + read_sql
