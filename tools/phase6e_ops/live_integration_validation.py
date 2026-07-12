"""Phase 6E: the one guarded, opt-in, operator-run live integration
validation, proving the demonstrable end-to-end workflow against real
BigQuery -- `loupe_platform_test` ONLY:

    Triage creates and persists a deterministic test incident
        -> Governance reads it and lowers trust with incident evidence
        -> Loupe reads it and emits a source-health warning
        -> resolving the incident clears both apps' degradation
        -> pending-validation metric status remains consistent
        -> audit and transition records exist
        -> every row this run created is tagged with a run_id and
           safely cleaned up afterward

STATUS: NOT YET RUN. Requires a real, authenticated Google Cloud
identity (see tools/phase6e_ops/bootstrap_test_dataset.py's module
docstring for the same authentication rules -- unchanged here) and
requires `loupe_platform_test` to already be bootstrapped and seeded
(run tools.phase6e_ops.bootstrap_test_dataset --yes first). This script
does not bootstrap or seed anything itself -- it only reads the already-
seeded catalog and writes/reads/cleans up its own run-tagged incident,
transition, and audit rows.

WHAT THIS NEVER DOES
----------------------
  - Never certifies a metric (this module never imports or calls
    certify_metric_definition()/certify_definition()).
  - Never runs against `loupe_platform` (production) -- validated by
    tools.phase6e_ops.safety.require_safe_test_dataset() before any
    environment variable is set or any shared/*_persistence.py module is
    imported.
  - Never touches metric_catalog/metric_versions rows (those are seeded
    once by bootstrap_test_dataset.py, not per-run -- this script only
    reads them).
  - Never leaves its own rows behind on success OR failure: cleanup runs
    in a `finally` block, deleting only rows tagged with this run's
    run_id (incidents.incident_id, incident_transitions.incident_id,
    audit_events.subject -- all containing the run_id-tagged
    incident_id), the same "only ever touch what I tagged" discipline
    tools/phase6b_spike/live_transaction_spike.py already established
    for its own spike_<run_id>_-prefixed tables.

USAGE
------
    python -m tools.phase6e_ops.live_integration_validation \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test \\
        --location US --actor <your-identity> --yes

Manual cleanup (only needed if a run was interrupted before its own
try/finally cleanup ran):

    python -m tools.phase6e_ops.live_integration_validation \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test \\
        --location US --cleanup-only --run-id <run_id printed by the run>
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional

from tools.phase6e_ops.safety import (
    DatasetTargetGuard,
    UnsafeTargetError,
    generate_run_id,
    require_safe_test_dataset,
    validate_run_id,
)

# The dataset/table this validation's synthetic incident is tagged
# against -- must match apps.metric_governance.persistence.QUALIFIED_DATASET
# and apps.loupe_agent.metrics.QUALIFIED_DATASET exactly (both hardcode
# the same "bigquery-public-data.thelook_ecommerce" value), and "orders"
# must be one of the seeded "revenue" definition's approved_source_tables
# -- otherwise Governance/Loupe would have no reason to look at this
# table's health at all.
SOURCE_DATASET = "bigquery-public-data.thelook_ecommerce"
SOURCE_TABLE = "orders"
CHECK_METRIC_NAME = "revenue"


class ValidationFailure(RuntimeError):
    """Raised when a proof step's assertion does not hold. Distinct from
    an unexpected exception -- this means the script ran to completion
    but observed behavior that contradicts what Phase 6D's wiring is
    supposed to guarantee."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)
    print(f"  OK: {message}")


def _incident_id(run_id: str, timestamp: str) -> str:
    return f"{SOURCE_DATASET}.{SOURCE_TABLE}.phase6e_integration_{run_id}.{timestamp}"


def run_validation(*, client: Any, actor: str, run_id: str, config: Any) -> None:
    from datetime import datetime, timezone

    from apps.data_quality_triage.checks import build_audit_event_for_incident
    from apps.data_quality_triage.incident_lifecycle import (
        acknowledge_incident,
        begin_investigation,
        resolve_incident,
    )
    from apps.data_quality_triage.models import TableFinding
    from apps.data_quality_triage.persistence import persist_confirmed_incidents
    from apps.loupe_agent import chat as loupe_chat
    from apps.loupe_agent import source_health as loupe_source_health
    from apps.metric_governance.persistence import read_catalog, source_health_for_definition, trust_score_for_definition
    from shared.metric_catalog import get_definition
    from shared.models import Incident

    timestamp = datetime.now(timezone.utc).isoformat()
    incident_id = _incident_id(run_id, timestamp)
    incident = Incident(
        incident_id=incident_id,
        created_at=timestamp,
        dataset=SOURCE_DATASET,
        table_id=SOURCE_TABLE,
        check_type=f"phase6e_integration_{run_id}",
        severity="high",
        status="open",
    )

    print(f"\nTagged incident_id for this run: {incident_id!r}")

    # --- 1. Triage creates and persists a deterministic test incident ---
    print("\n[1/6] Triage: persist_confirmed_incidents()...")

    def _build_event(persisted_incident: Incident):
        finding = TableFinding(
            table_id=persisted_incident.table_id,
            check_name=persisted_incident.check_type,
            status="fail",
            severity="high",
            observed_value=None,
            threshold=None,
            summary="Phase 6E live integration validation -- synthetic finding.",
            likely_root_cause="Phase 6E live integration validation run.",
        )
        return build_audit_event_for_incident(
            persisted_incident,
            finding,
            event_id=f"incident_created.{persisted_incident.incident_id}",
            timestamp=timestamp,
        )

    outcomes = persist_confirmed_incidents(client, [incident], actor=actor, build_audit_event=_build_event, config=config)
    _check(len(outcomes) == 1, "exactly one persist outcome returned")
    _check(outcomes[0].persisted is True, f"incident persisted (error={outcomes[0].error!r})")

    # --- 2. Governance reads it and lowers trust with incident evidence ---
    print("\n[2/6] Governance: source_health_for_definition() / trust_score_for_definition()...")
    definition = get_definition(CHECK_METRIC_NAME)
    _check(SOURCE_TABLE in definition.approved_source_tables, f"{SOURCE_TABLE!r} is an approved source table for {CHECK_METRIC_NAME!r}")

    trust_result = trust_score_for_definition(client, definition)
    _check(trust_result.evidence.worst_health is not None, "source health evidence resolved (not unavailable)")
    _check(trust_result.evidence.worst_health.status == "critical", f"worst source health is 'critical' (got {trust_result.evidence.worst_health.status!r})")
    _check(
        any(inc.incident_id == incident_id for inc in trust_result.evidence.active_incidents),
        "the tagged incident appears in Governance's active-incident evidence",
    )
    source_factor = next(f for f in trust_result.trust.factors if f.name == "source_health")
    _check(source_factor.points < 0, f"source_health trust factor is penalized (points={source_factor.points})")

    # --- 3. Loupe reads it and emits a source-health warning ---
    print("\n[3/6] Loupe: source_health.get_source_health() / summarize()...")
    health_rows = loupe_source_health.get_source_health(client, (SOURCE_TABLE,))
    summary = loupe_source_health.summarize(health_rows)
    _check(summary["status"] == "critical", f"Loupe reports source health critical (got {summary['status']!r})")
    _check(summary["warning"] is not None and SOURCE_TABLE in summary["warning"], "Loupe's warning names the affected table")

    # --- 4. A valid UI lifecycle clears both apps' degradation ---
    # Exercise the SAME UI-facing lifecycle functions as the real Triage
    # app. The shared state machine deliberately prohibits open -> resolved,
    # so this proof follows open -> acknowledged -> investigating -> resolved
    # (the documented direct-fix path from investigating).
    print(
        "\n[4/6] Following the persisted UI lifecycle "
        "open -> acknowledged -> investigating -> resolved..."
    )
    acknowledged = acknowledge_incident(
        client,
        incident_id,
        expected_current_status="open",
        mode="persisted",
        actor=actor,
        config=config,
        transition_id=f"phase6e_integration_{run_id}_acknowledge",
    )
    _check(acknowledged.status == "acknowledged", "incident transitioned to acknowledged")

    investigating = begin_investigation(
        client,
        incident_id,
        expected_current_status="acknowledged",
        mode="persisted",
        actor=actor,
        config=config,
        transition_id=f"phase6e_integration_{run_id}_investigate",
    )
    _check(investigating.status == "investigating", "incident transitioned to investigating")

    transition_id = f"phase6e_integration_{run_id}_resolve"
    transition_outcome = resolve_incident(
        client,
        incident_id,
        resolution_notes="Phase 6E live integration validation -- automatic cleanup resolution.",
        expected_current_status="investigating",
        mode="persisted",
        actor=actor,
        config=config,
        transition_id=transition_id,
    )
    _check(transition_outcome.persisted is True, "the resolution was actually persisted, not session-only")
    _check(transition_outcome.status == "resolved", f"incident transitioned to resolved (got {transition_outcome.status!r})")

    trust_after = trust_score_for_definition(client, definition)
    _check(
        trust_after.evidence.worst_health is None or trust_after.evidence.worst_health.status == "healthy",
        f"Governance no longer reports critical health for {SOURCE_TABLE!r} after resolution",
    )
    health_after = loupe_source_health.summarize(loupe_source_health.get_source_health(client, (SOURCE_TABLE,)))
    _check(health_after["status"] == "healthy", f"Loupe reports healthy after resolution (got {health_after['status']!r})")

    # --- 5. Pending-validation metric status remains consistent ---
    print("\n[5/6] Pending-validation status consistency (Governance catalog read + Loupe certification note)...")
    catalog = read_catalog(client)
    _check(not catalog.catalog_unavailable, f"persisted catalog is readable (safe_error={catalog.safe_error!r} -- has bootstrap_test_dataset.py --yes been run?)")
    persisted_definition = next((d for d in catalog.definitions if d.name == CHECK_METRIC_NAME), None)
    _check(persisted_definition is not None, f"{CHECK_METRIC_NAME!r} is present in the persisted catalog")
    _check(persisted_definition.certification_status == "pending_validation", f"{CHECK_METRIC_NAME!r} is pending_validation in Governance's read (got {persisted_definition.certification_status!r})")

    note = loupe_chat.certification_note(client, CHECK_METRIC_NAME)
    _check("pending_validation" in note, "Loupe's certification note also reports pending_validation")
    _check("certification_status=certified" not in note, "Loupe's note never claims this metric is certified")

    # --- 6. Audit and transition records exist ---
    print("\n[6/6] Confirming audit and transition records exist...")
    from shared.audit import list_events_for_subject
    from shared.incident_persistence import INCIDENT_TRANSITIONS_TABLE
    from shared.data_service import run_query

    events = list_events_for_subject(client, incident_id)
    _check(any(e.event_type == "incident_created" for e in events), "an incident_created audit event exists for this incident_id")

    transition_rows = run_query(
        client,
        f"SELECT transition_id, from_status, to_status FROM `{INCIDENT_TRANSITIONS_TABLE}` WHERE incident_id = @incident_id",
        {"incident_id": incident_id},
    )
    _check(len(transition_rows) == 3, "exactly three lifecycle transition rows exist for this incident_id")
    _check(any(row["to_status"] == "resolved" for row in transition_rows), "an incident_transitions row records the transition to 'resolved'")

    print("\nAll six proof steps passed.")


def cleanup_run(*, client: Any, run_id: str) -> None:
    """Deletes only rows this run's incident_id tag identifies -- never
    metric_catalog/metric_versions (seeded once, not per-run), never
    write_locks, never any other run's rows."""

    validate_run_id(run_id)
    from shared.audit_persistence import AUDIT_EVENTS_TABLE
    from shared.incident_persistence import INCIDENT_TRANSITIONS_TABLE, INCIDENTS_TABLE

    tag = f"phase6e_integration_{run_id}"
    deleted = {}
    for label, table, column in (
        ("incident_transitions", INCIDENT_TRANSITIONS_TABLE, "incident_id"),
        ("audit_events", AUDIT_EVENTS_TABLE, "subject"),
        ("incidents", INCIDENTS_TABLE, "incident_id"),
    ):
        sql = f"DELETE FROM `{table}` WHERE {column} LIKE '%{tag}%'"
        client.query(sql).result()
        deleted[label] = tag
    print(f"Cleanup: deleted rows tagged {tag!r} from {list(deleted.keys())}.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase6e_ops.live_integration_validation",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True, help="Must contain 'test' and must never be 'loupe_platform'.")
    parser.add_argument("--location", default="US")
    parser.add_argument("--actor", default=None, help="Required unless --cleanup-only.")
    parser.add_argument("--yes", action="store_true", help="Required to actually execute. Without it, dry-run only.")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--run-id", default=None, help="Required with --cleanup-only. Must match ^[0-9a-f]{10}$.")
    args = parser.parse_args(argv)

    try:
        require_safe_test_dataset(args.project, args.dataset, args.location)
    except UnsafeTargetError as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.cleanup_only:
        if not args.run_id:
            print("ERROR: --run-id is required with --cleanup-only.")
            return 2
        try:
            validate_run_id(args.run_id)
        except UnsafeTargetError as exc:
            print(f"ERROR: {exc}")
            return 2

        os.environ["LOUPE_BQ_PROJECT"] = args.project
        os.environ["LOUPE_DATASET"] = args.dataset
        os.environ["LOUPE_BQ_LOCATION"] = args.location

        from google.cloud import bigquery

        client = DatasetTargetGuard(
            bigquery.Client(project=args.project, location=args.location),
            allowed_dataset=args.dataset,
        )
        cleanup_run(client=client, run_id=args.run_id)
        return 0

    if not args.actor:
        print("ERROR: --actor is required (unless --cleanup-only).")
        return 2

    run_id = generate_run_id()
    print(f"Target: project={args.project!r} dataset={args.dataset!r} location={args.location!r} actor={args.actor!r}")
    print(f"Run ID (all rows this run creates are tagged phase6e_integration_{run_id}): {run_id}")

    if not args.yes:
        print(
            "\nDry run only (pass --yes to actually execute). This command "
            "would run all six proof steps against real BigQuery and clean "
            "up every row it created afterward, regardless of outcome.\n"
            "Requires loupe_platform_test to already be bootstrapped and "
            "seeded -- run tools.phase6e_ops.bootstrap_test_dataset --yes "
            "first if it is not."
        )
        return 0

    os.environ["LOUPE_BQ_PROJECT"] = args.project
    os.environ["LOUPE_DATASET"] = args.dataset
    os.environ["LOUPE_BQ_LOCATION"] = args.location
    os.environ["LOUPE_PERSISTENCE_MODE"] = "persisted"

    from google.cloud import bigquery

    from shared.config import load_platform_config

    config = load_platform_config()
    require_safe_test_dataset(config.project, config.dataset, config.location)

    # Phase 6E correction 2: guard the client so that ANY generated SQL
    # this run's calls issue is refused outright if it references a
    # dataset other than the one just validated above -- never a silent
    # fallback to loupe_platform, regardless of any module's import order.
    client = DatasetTargetGuard(
        bigquery.Client(project=config.project, location=config.location),
        allowed_dataset=config.dataset,
    )

    failure: Optional[Exception] = None
    try:
        run_validation(client=client, actor=args.actor, run_id=run_id, config=config)
    except Exception as exc:  # noqa: BLE001 -- must still run cleanup below
        failure = exc
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
    finally:
        print(f"\nRun ID for manual recovery if cleanup below fails: {run_id}")
        try:
            cleanup_run(client=client, run_id=run_id)
        except Exception as cleanup_exc:  # noqa: BLE001 -- must not mask the original failure, if any
            print(
                f"WARNING: automatic cleanup failed ({type(cleanup_exc).__name__}). Run this manually:\n"
                f"  python -m tools.phase6e_ops.live_integration_validation "
                f"--project {args.project} --dataset {args.dataset} --location {args.location} "
                f"--cleanup-only --run-id {run_id}"
            )

    if failure is not None:
        return 1

    print("\nLive integration validation PASSED. All rows this run created have been cleaned up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
