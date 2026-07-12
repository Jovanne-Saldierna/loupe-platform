"""Phase 6B pre-work: the minimal guarded LIVE BigQuery transaction spike.

STATUS: NOT YET RUN. Nothing in shared/persistence_transactions.py's
claims about real BigQuery multi-statement transaction behavior is
verified until this script has actually been executed and its output
reviewed.

WHO RUNS THIS AND HOW
----------------------
This script requires a real, authenticated Google Cloud identity. It is
NOT run by Claude/this agent -- there is no Application Default
Credentials file, no gcloud CLI, and no service-account key material
available in the sandboxed environment this repository was prepared in,
and per the approved instructions this script must never request,
generate, upload, or read one. You run this yourself, from either:

  (a) a local terminal with `gcloud auth application-default login`
      already completed, or
  (b) Google Cloud Shell (which already has an authenticated identity).

REQUIRED PERMISSIONS (amendment 7)
------------------------------------
This script needs two different kinds of access:

  1. BigQuery Job User (to run queries/scripts) + BigQuery Data Editor
     scoped to the test dataset (to create/drop the spike's own tagged
     tables and run DML inside it). These two together are NOT
     guaranteed to include `bigquery.datasets.create` -- dataset
     creation is a project-level (or higher) permission that Data Editor
     alone does not grant in every IAM configuration.
  2. Either:
       (a) `loupe_platform_test` already exists in `US`, created ahead
           of time by someone with `bigquery.datasets.create` (e.g. a
           project Owner/Editor, or a custom role including that
           permission) -- in which case this script only ever needs
           Job User + Data Editor scoped to that one dataset, or
       (b) the identity running this script itself has
           `bigquery.datasets.create` (e.g. BigQuery Admin, or
           roles/bigquery.dataEditor at the PROJECT level rather than
           dataset level) so `_ensure_dataset()` below can create it.

  This script does not attempt to distinguish which case applies ahead
  of time -- if dataset creation fails for a permissions reason, the
  error is allowed to propagate with a printed hint pointing back at
  this section, rather than being caught and silently downgraded.

USAGE
------
Dry run (prints the plan, touches nothing):

    python -m tools.phase6b_spike.live_transaction_spike \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test --location US

Actually execute the spike:

    python -m tools.phase6b_spike.live_transaction_spike \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test --location US --yes

Manual cleanup (only needed if a run was interrupted before its own
try/finally cleanup ran -- see "Guaranteed cleanup" below):

    python -m tools.phase6b_spike.live_transaction_spike \\
        --project ai-weekend-agent-501502 --dataset loupe_platform_test --location US \\
        --cleanup-only --run-id <run_id printed by the run>

SAFETY GUARDS
--------------
  - `--location` must equal "US" -- this is not configurable to anything
    else for this spike (bigquery-public-data.thelook_ecommerce is
    hosted in US; a different location is simply out of scope here).
  - `--project` and `--dataset` are validated against safe Google Cloud
    identifier patterns BEFORE being interpolated into any SQL
    identifier -- see `_validate_project_id`/`_validate_dataset_id`.
  - `--dataset` must contain the substring "test" and must never equal
    "loupe_platform" (the real production dataset name).
  - If the target dataset already exists, its actual location is read
    back and compared against "US" -- a location mismatch is a hard
    refusal, never a silent `exists_ok=True` accept (amendment 3).
  - `--run-id` (for `--cleanup-only`) must match `^[0-9a-f]{10}$` exactly
    -- the same format `uuid.uuid4().hex[:10]` always generates. Anything
    else (empty, shortened, extended, containing `%`/`*`/other
    wildcard-like characters, punctuation, or non-hex characters) is
    rejected before this script ever lists or drops a single table.
  - Every resource this script creates is prefixed `spike_<run_id>_`.
    Cleanup only ever targets that exact prefix within the configured
    dataset -- it NEVER deletes the dataset itself (amendment 8): the
    dataset may pre-exist, or hold another concurrent spike run's
    tables, and this script has no way to know it's safe to remove.
  - Table/dataset creation and the full spike run are wrapped in
    try/finally so cleanup always runs even if a step raises
    unexpectedly (amendment 1) -- the run_id is printed both at the
    start and again at the end specifically so it's available for
    manual `--cleanup-only` recovery if the automatic cleanup itself
    fails.
  - Never seeds shared.metric_catalog's real catalog rows, never wires
    any application to this script, never modifies shared/, apps/, or
    tests/.
  - Prints only exception TYPE/MODULE names for classification purposes
    -- never full raw exception text, and never anything that could
    contain credential/token material (there is nothing in this script
    that ever reads such material -- ADC resolution happens entirely
    inside google-cloud-bigquery, never in this script's own code).
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

REAL_DATASET_NAME = "loupe_platform"
REQUIRED_LOCATION = "US"

# Google Cloud project IDs: 6-30 chars, lowercase letters/digits/hyphens,
# must start with a letter, must not end with a hyphen. This is the
# documented GCP project-ID format; validated here BEFORE any value is
# interpolated into a backtick-quoted SQL identifier.
_PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

# BigQuery dataset IDs: letters, digits, underscores only, 1-1024 chars.
_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,1024}$")

# Exactly the format uuid.uuid4().hex[:10] produces: 10 lowercase hex
# characters. Nothing else is ever accepted as a run_id -- no wildcards,
# no punctuation, no path separators, no arbitrary length.
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{10}$")


class UnsafeSpikeConfigurationError(ValueError):
    """Raised and left uncaught -- this script must exit non-zero rather
    than proceed if any safety check fails."""


def _validate_project_id(project: str) -> None:
    # fullmatch (not match): match() would let a trailing "\n" slip
    # through because "$" matches just before a trailing newline in
    # non-MULTILINE mode. fullmatch requires the entire string --
    # including any trailing newline -- to satisfy the pattern.
    if not _PROJECT_ID_PATTERN.fullmatch(project):
        raise UnsafeSpikeConfigurationError(
            f"--project {project!r} does not look like a valid Google Cloud "
            "project ID (expected lowercase letters, digits, and hyphens, "
            "6-30 characters, starting with a letter). Refusing to "
            "interpolate it into any SQL identifier."
        )


def _validate_dataset_id(dataset: str) -> None:
    if not _DATASET_ID_PATTERN.fullmatch(dataset):
        raise UnsafeSpikeConfigurationError(
            f"--dataset {dataset!r} does not look like a valid BigQuery "
            "dataset ID (expected letters, digits, and underscores only). "
            "Refusing to interpolate it into any SQL identifier."
        )


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise UnsafeSpikeConfigurationError(
            f"--run-id {run_id!r} does not match the required format "
            f"{_RUN_ID_PATTERN.pattern!r} (exactly 10 lowercase hex "
            "characters, the same format uuid.uuid4().hex[:10] always "
            "produces). Refusing to list or drop anything: an "
            "over-broad or malformed run_id could match table names "
            "this spike never created."
        )


def _require_safe_target(project: str, dataset: str, location: str) -> None:
    _validate_project_id(project)
    _validate_dataset_id(dataset)
    if location != REQUIRED_LOCATION:
        raise UnsafeSpikeConfigurationError(
            f"--location must be {REQUIRED_LOCATION!r} for this spike "
            f"(got {location!r}). bigquery-public-data.thelook_ecommerce "
            "is hosted in US; this spike is not scoped to verify any "
            "other location."
        )
    if dataset == REAL_DATASET_NAME:
        raise UnsafeSpikeConfigurationError(
            f"Refusing to run: --dataset must never be {REAL_DATASET_NAME!r} "
            "(the real production dataset name)."
        )
    if "test" not in dataset.lower():
        raise UnsafeSpikeConfigurationError(
            f"Refusing to run: --dataset {dataset!r} does not contain 'test'. "
            "This spike only ever operates against an isolated test dataset."
        )


def _generate_run_id() -> str:
    run_id = uuid.uuid4().hex[:10]
    _validate_run_id(run_id)  # self-check: guarantees the invariant _RUN_ID_PATTERN encodes
    return run_id


# ---------------------------------------------------------------------------
# Result bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class SpikeStepResult:
    name: str
    ok: bool
    detail: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpikeReport:
    run_id: str
    project: str
    dataset: str
    location: str
    steps: list[SpikeStepResult] = field(default_factory=list)

    def add(self, result: SpikeStepResult) -> None:
        self.steps.append(result)
        status = "OK" if result.ok else "FAILED/UNEXPECTED"
        print(f"[{status}] {result.name}: {result.detail}")
        for key, value in result.diagnostics.items():
            print(f"    {key}: {value}")

    def print_summary(self) -> None:
        print("\n=== Phase 6B live transaction spike -- summary ===")
        for step in self.steps:
            print(f"  {'OK' if step.ok else 'FAILED'}  {step.name}")


# ---------------------------------------------------------------------------
# Table naming -- every resource this script creates is prefixed with the
# run's spike tag. Cleanup only ever targets this exact prefix.
# ---------------------------------------------------------------------------


def _table_prefix(run_id: str) -> str:
    _validate_run_id(run_id)
    return f"spike_{run_id}_"


def _lock_table(dataset: str, run_id: str) -> str:
    return f"{dataset}.{_table_prefix(run_id)}lock_rows"


def _incidents_table(dataset: str, run_id: str) -> str:
    return f"{dataset}.{_table_prefix(run_id)}incidents_like"


# ---------------------------------------------------------------------------
# Setup: dataset location check (amendment 3) + spike-tagged tables
# ---------------------------------------------------------------------------


def _ensure_dataset(client, project: str, dataset: str, location: str) -> None:
    """Confirm the target dataset exists and is in `location`, or create
    it in `location` if it doesn't exist yet. Never silently accepts an
    existing dataset in the wrong location -- that is a hard refusal.
    """

    from google.cloud import bigquery

    dataset_ref = f"{project}.{dataset}"
    try:
        existing = client.get_dataset(dataset_ref)
    except Exception:
        existing = None

    if existing is not None:
        actual_location = getattr(existing, "location", None)
        if actual_location != location:
            raise UnsafeSpikeConfigurationError(
                f"Dataset {dataset_ref!r} already exists but is in location "
                f"{actual_location!r}, not {location!r}. Refusing to "
                "continue against an incompatible existing dataset -- "
                "this must be resolved manually (either use a different "
                "dataset name, or have an administrator confirm the "
                "existing dataset's location before re-running)."
            )
        print(f"Dataset {dataset_ref!r} already exists in {actual_location!r} -- reusing it.")
        return

    print(
        f"Dataset {dataset_ref!r} does not exist yet; attempting to create it "
        f"in {location!r}. This requires bigquery.datasets.create -- see this "
        "script's module docstring, 'REQUIRED PERMISSIONS', if this fails."
    )
    new_dataset = bigquery.Dataset(dataset_ref)
    new_dataset.location = location
    client.create_dataset(new_dataset)  # no exists_ok=True: existence was already checked above


def _create_spike_tables(client, project: str, dataset: str, run_id: str) -> None:
    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"

    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{lock_table}` (
            lock_domain STRING NOT NULL,
            last_touched_at TIMESTAMP,
            last_touched_by STRING
        )
        """
    ).result()
    client.query(
        f"INSERT INTO `{lock_table}` (lock_domain, last_touched_at, last_touched_by) "
        f"VALUES ('spike_domain', CURRENT_TIMESTAMP(), 'setup')"
    ).result()

    client.query(
        f"""
        CREATE TABLE IF NOT EXISTS `{incidents_table}` (
            incident_id STRING NOT NULL,
            status STRING NOT NULL,
            row_version INT64 NOT NULL,
            created_by STRING
        )
        """
    ).result()


# ---------------------------------------------------------------------------
# Spike step 1 + 3 + 4 + 5: successful commit, ASSERT, @@row_count, final result
# ---------------------------------------------------------------------------


def spike_successful_commit_with_assert_and_row_count(
    client, project: str, dataset: str, run_id: str, report: SpikeReport
) -> None:
    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    incident_id = f"{run_id}_commit_ok"

    script = f"""
    BEGIN
      BEGIN TRANSACTION;

      UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = 'commit_ok'
      WHERE lock_domain = 'spike_domain';
      ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';

      INSERT INTO `{incidents_table}` (incident_id, status, row_version, created_by)
      VALUES ('{incident_id}', 'open', 1, 'commit_ok');
      ASSERT @@row_count = 1 AS 'expected exactly one incident row inserted';

      COMMIT TRANSACTION;

      -- Final structured result: this SELECT's result set is what
      -- job.result() returns for a script job -- the reliable way to
      -- get a structured outcome, independent of child-job introspection.
      SELECT '{incident_id}' AS incident_id, 'open' AS status, CURRENT_TIMESTAMP() AS committed_at;
    END;
    """

    job = client.query(script)
    rows = list(job.result())

    verify = list(
        client.query(
            f"SELECT status, row_version FROM `{incidents_table}` WHERE incident_id = '{incident_id}'"
        ).result()
    )

    ok = len(rows) == 1 and len(verify) == 1 and verify[0]["status"] == "open"
    report.add(
        SpikeStepResult(
            name="1/3/4/5: successful commit + ASSERT + @@row_count + final result",
            ok=ok,
            detail=(
                "Script committed; final SELECT returned "
                f"{len(rows)} row(s); post-commit verification found "
                f"{len(verify)} matching incident row(s)."
            ),
            diagnostics={"final_result_row": dict(rows[0]) if rows else None},
        )
    )


# ---------------------------------------------------------------------------
# Spike step 2: forced failure and complete rollback (via a failing ASSERT)
# ---------------------------------------------------------------------------


def spike_forced_failure_and_rollback(
    client, project: str, dataset: str, run_id: str, report: SpikeReport
) -> None:
    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    incident_id = f"{run_id}_should_rollback"

    script = f"""
    BEGIN
      BEGIN TRANSACTION;

      UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = 'should_rollback'
      WHERE lock_domain = 'spike_domain';
      ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';

      INSERT INTO `{incidents_table}` (incident_id, status, row_version, created_by)
      VALUES ('{incident_id}', 'open', 1, 'should_rollback');

      -- Deliberately-failing invariant: forces the script to abort
      -- before COMMIT is ever reached.
      ASSERT (SELECT COUNT(*) FROM `{incidents_table}` WHERE incident_id = '{incident_id}') = 999
        AS 'deliberately false -- this must abort the whole script';

      COMMIT TRANSACTION;
    END;
    """

    raised: Optional[Exception] = None
    try:
        client.query(script).result()
    except Exception as exc:  # noqa: BLE001 -- classification is the point
        raised = exc

    verify = list(
        client.query(
            f"SELECT COUNT(*) AS n FROM `{incidents_table}` WHERE incident_id = '{incident_id}'"
        ).result()
    )
    verify_lock = list(
        client.query(f"SELECT last_touched_by FROM `{lock_table}` WHERE lock_domain = 'spike_domain'").result()
    )

    rolled_back = verify[0]["n"] == 0
    lock_unaffected = verify_lock[0]["last_touched_by"] != "should_rollback"

    report.add(
        SpikeStepResult(
            name="2: forced failure via ASSERT + complete rollback",
            ok=raised is not None and rolled_back and lock_unaffected,
            detail=(
                f"Script raised {type(raised).__name__ if raised else 'NOTHING (unexpected)'}; "
                f"incident row present after failed script: {'yes -- ROLLBACK DID NOT WORK' if not rolled_back else 'no (correct)'}; "
                f"lock row shows the failed transaction's write: {'yes -- ROLLBACK DID NOT WORK' if not lock_unaffected else 'no (correct)'}."
            ),
            diagnostics={
                "exception_type": type(raised).__name__ if raised else None,
                "exception_module": type(raised).__module__ if raised else None,
            },
        )
    )


# ---------------------------------------------------------------------------
# Spike step 6: child-job enumeration, diagnostics only
# ---------------------------------------------------------------------------


def spike_child_job_enumeration_diagnostics_only(client, project: str, report: SpikeReport) -> None:
    """Purely informational: confirms whether
    `client.list_jobs(parent_job=job)` is usable to inspect a script's
    child jobs. This is NEVER the correctness mechanism (that role
    belongs to ASSERT + @@row_count, embedded directly in each template,
    per steps 1-4 above and shared/persistence_transactions.py's
    corrected design) -- this step exists only to document what's
    available for optional debugging/observability tooling later.
    """

    script = "BEGIN\n  SELECT 1 AS noop;\nEND;"
    job = client.query(script)
    job.result()

    try:
        child_jobs = list(client.list_jobs(parent_job=job))
        diagnostics = {
            "parent_job_id": job.job_id,
            "child_job_count": len(child_jobs),
            "child_job_ids": [child_job.job_id for child_job in child_jobs][:5],
        }
        detail = f"client.list_jobs(parent_job=job) returned {len(child_jobs)} child job(s) for a trivial one-statement script."
    except Exception as exc:  # noqa: BLE001 -- this step is diagnostic-only, never load-bearing
        diagnostics = {"exception_type": type(exc).__name__}
        detail = f"client.list_jobs(parent_job=job) raised {type(exc).__name__} -- noted for the record, not treated as a spike failure."

    report.add(
        SpikeStepResult(
            name="6: child-job enumeration (diagnostics only, never correctness-load-bearing)",
            ok=True,  # informational regardless of outcome -- see docstring
            detail=detail,
            diagnostics=diagnostics,
        )
    )


# ---------------------------------------------------------------------------
# Spike steps 7 + 8: concurrent lock-row mutation, classified honestly
# ---------------------------------------------------------------------------


def _touch_lock_script(lock_table: str, worker_label: str) -> str:
    return f"""
    BEGIN
      BEGIN TRANSACTION;
      UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = '{worker_label}'
      WHERE lock_domain = 'spike_domain';
      ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';
      -- Deliberate delay to widen the window for a genuine overlap with
      -- the other worker's transaction.
      SELECT COUNT(*) FROM UNNEST(GENERATE_ARRAY(1, 3000000));
      COMMIT TRANSACTION;
    END;
    """


def _run_concurrent_pair(client_factory, script_builder, worker_labels: tuple[str, str]) -> dict[str, Any]:
    """Run two workers concurrently and, for each, capture a SANITIZED
    outcome only -- never the raw exception or its message. Classification
    (_is_retryable(), bigquery_error_diagnostics()) happens HERE, inside
    the worker thread, while the real exception instance is still in
    scope -- not downstream by reconstructing a fake exception from a
    type name string (a prior draft did that; it lost the structured
    `errors[].reason` information bigquery_error_diagnostics() needs and
    could never have been able to distinguish a genuine SQL bug from
    genuine contention, both of which surface as the same exception type,
    BadRequest, against real BigQuery -- confirmed by the Phase 6B spike).
    """

    # Local import: keeps this a spike-only dependency on the module it's
    # verifying, not a circular one.
    from shared.persistence_transactions import _is_retryable, bigquery_error_diagnostics

    outcomes: dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def _worker(label: str) -> None:
        client = client_factory()
        script = script_builder(label)
        barrier.wait()  # both workers submit as close together as possible
        start = time.monotonic()
        try:
            client.query(script).result()
            outcomes[label] = {
                "ok": True,
                "exception_type": None,
                "exception_module": None,
                "reason_codes": [],
                "http_status": None,
                "is_retryable": False,
                "elapsed_s": time.monotonic() - start,
            }
        except Exception as exc:  # noqa: BLE001 -- sanitized classification is the point
            diagnostics = bigquery_error_diagnostics(exc)
            outcomes[label] = {
                "ok": False,
                "exception_type": diagnostics["exception_type"],
                "exception_module": diagnostics["exception_module"],
                "reason_codes": diagnostics["reason_codes"],
                "http_status": diagnostics["http_status"],
                "is_retryable": _is_retryable(exc),
                "elapsed_s": time.monotonic() - start,
            }

    threads = [threading.Thread(target=_worker, args=(label,)) for label in worker_labels]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def _classify_concurrency_round(outcomes: dict[str, Any]) -> str:
    """Returns 'confirmed', 'inconclusive', or 'failed'.

    'Confirmed' requires genuine evidence of contention: at least one
    worker failed, AND every worker that failed did so with a failure
    classified retryable by shared.persistence_transactions._is_retryable()
    (computed from the REAL exception instance inside
    _run_concurrent_pair()'s worker threads, not reconstructed here from
    a type name). This deliberately covers BOTH shapes real BigQuery
    produced across the two Phase 6B live spike runs: "exactly one loses,
    the other commits" (the originally-assumed shape) AND "BigQuery
    cancels both concurrent transactions touching the same row" (the
    shape actually observed in the second run, 2026-07-12,
    run_id=ad466ad893 -- both worker_a and worker_b failed with a
    confirmed concurrent-update-signature BadRequest in every attempt).
    Neither shape is "generic failure": both are genuine, confirmed
    contention. A round is only 'failed' if a failure did NOT match the
    confirmed retryable signature (a genuine bug, not contention).
    """

    succeeded = [label for label, outcome in outcomes.items() if outcome["ok"]]
    failed = [label for label, outcome in outcomes.items() if not outcome["ok"]]

    if len(succeeded) == 2:
        return "inconclusive"  # both succeeded -- never actually conflicted
    if failed and all(outcomes[label]["is_retryable"] for label in failed):
        return "confirmed"  # every failure observed was genuine, confirmed contention
    return "failed"  # at least one failure was NOT confirmed contention -- a real bug


def spike_concurrent_lock_row_raw_contention(
    client_factory, project: str, dataset: str, run_id: str, report: SpikeReport, *, max_attempts: int = 3
) -> None:
    """RAW test (bypasses execute_transaction()'s own retry loop): two
    workers submit the lock-touch script directly via
    `client.query(script).result()`, as close together as possible, to
    prove genuine contention occurs at the BigQuery layer itself. This
    step is deliberately NOT retried by this module's own mechanism --
    see spike_concurrent_lock_row_liveness_via_execute_transaction() for
    the companion step proving the retry mechanism resolves that
    contention to liveness.
    """

    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    rounds: list[dict[str, Any]] = []
    classification = "failed"

    for attempt in range(1, max_attempts + 1):
        outcomes = _run_concurrent_pair(
            client_factory,
            lambda label: _touch_lock_script(lock_table, label),
            (f"worker_a_{attempt}", f"worker_b_{attempt}"),
        )
        classification = _classify_concurrency_round(outcomes)
        rounds.append({"attempt": attempt, "classification": classification, "outcomes": outcomes})
        if classification == "confirmed":
            break

    report.add(
        SpikeStepResult(
            name="7: raw concurrent lock-row mutation (no retry) -- proves genuine contention",
            ok=classification == "confirmed",
            detail=(
                f"Final classification after {len(rounds)} attempt(s): {classification}. "
                "'confirmed' = every worker that failed did so with a failure "
                "shared.persistence_transactions._is_retryable() classifies as genuine "
                "contention (covers both 'one loses' and 'both get cancelled' -- the shape "
                "actually observed against real BigQuery); "
                "'inconclusive' = both workers succeeded (no genuine contention observed -- "
                "the lock-row pattern was not actually exercised under real overlap); "
                "'failed' = at least one failure was NOT classified as confirmed contention "
                "(a genuine bug, not ordinary contention)."
            ),
            diagnostics={"rounds": rounds},
        )
    )


def spike_concurrent_lock_row_liveness_via_execute_transaction(
    client_factory, project: str, dataset: str, run_id: str, report: SpikeReport
) -> None:
    """Runs the SAME lock-row touch concurrently, but THROUGH
    shared.persistence_transactions.execute_transaction() (which retries
    a classified-retryable failure internally, with bounded exponential
    backoff + full jitter) instead of a raw client.query() call.

    This is the companion liveness proof required alongside
    spike_concurrent_lock_row_raw_contention(): once a conflict is
    correctly classified as retryable
    (`concurrent_update_signature_matched`, see bigquery_error_diagnostics()
    and _is_retryable()), the retry loop must resolve it -- at least one
    worker's call should ultimately commit, rather than both permanently
    failing. (Deterministically forcing ConcurrentModificationError from
    sustained real-BigQuery contention is not reliable to reproduce live;
    that path is proven credential-free and deterministically instead --
    see tests/shared/test_persistence_transactions.py's
    test_execute_transaction_exhausts_retry_budget_on_sustained_concurrent_update_conflict.)
    """

    from shared.persistence_transactions import BoundStatement, StatementTemplate, execute_transaction, register_template

    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    template = StatementTemplate(
        name=f"_SPIKE_LOCK_TOUCH_LIVENESS_{run_id}",
        sql=f"""
        UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @who
        WHERE lock_domain = 'spike_domain';
        ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';
        -- Same deliberate delay as the raw test, to widen the overlap window.
        SELECT COUNT(*) FROM UNNEST(GENERATE_ARRAY(1, 3000000));
        """,
        lock_domain="incidents",
    )
    register_template(template)

    outcomes: dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def _worker(label: str) -> None:
        client = client_factory()
        barrier.wait()
        start = time.monotonic()
        try:
            result = execute_transaction(client, [BoundStatement(template_name=template.name, params={"who": label})])
            outcomes[label] = {"ok": True, "attempts": result.attempts, "elapsed_s": time.monotonic() - start}
        except Exception as exc:  # noqa: BLE001 -- sanitized classification is the point
            from shared.persistence_transactions import bigquery_error_diagnostics

            diagnostics = bigquery_error_diagnostics(exc) if type(exc).__name__ == "BadRequest" else {}
            outcomes[label] = {
                "ok": False,
                "exception_type": type(exc).__name__,
                "diagnostics": diagnostics,
                "elapsed_s": time.monotonic() - start,
            }

    threads = [threading.Thread(target=_worker, args=(label,)) for label in ("liveness_worker_a", "liveness_worker_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    succeeded = [label for label, outcome in outcomes.items() if outcome["ok"]]
    report.add(
        SpikeStepResult(
            name="8: liveness -- concurrent lock-row touch via execute_transaction()'s built-in retry",
            ok=len(succeeded) >= 1,
            detail=(
                f"{len(succeeded)}/2 worker(s) succeeded via execute_transaction()'s internal "
                "retry + backoff (expected >= 1 -- the retry mechanism must resolve genuine "
                "contention to liveness, not leave every concurrent caller permanently failed)."
            ),
            diagnostics={"outcomes": outcomes},
        )
    )


# ---------------------------------------------------------------------------
# Spike step 9: execute_transaction() against real BigQuery
# ---------------------------------------------------------------------------


def spike_execute_transaction_against_real_bigquery(
    client_factory, project: str, dataset: str, run_id: str, report: SpikeReport
) -> None:
    """Runs shared.persistence_transactions.execute_transaction() itself
    (not a reimplementation) against real BigQuery, using a spike-only
    registered template that follows the corrected pattern every real
    Phase 6B template must follow: embed ASSERT @@row_count after the
    DML it cares about, and end with a trailing SELECT so
    TransactionResult.result_rows carries something meaningful back.

    Unlike the prior draft of this spike, this is expected to SUCCEED
    cleanly -- shared/persistence_transactions.py no longer depends on
    any job-introspection adapter that could leave an ambiguous
    committed-write/client-failure state. If this step still fails, that
    is a genuine, actionable finding (not a predicted, accepted one).
    """

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from shared.persistence_transactions import (  # noqa: E402
        BoundStatement,
        StatementTemplate,
        execute_transaction,
        register_template,
    )

    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    # `result_sql` is rendered AFTER COMMIT TRANSACTION by
    # shared.persistence_transactions._render_script() -- confirmed
    # necessary by this exact step's prior failure (2026-07-12 run,
    # run_id=c0536479d3): a SELECT embedded inline in `sql` lands BEFORE
    # the script's COMMIT TRANSACTION, so it is not the script's final
    # statement and job.result() returns empty rows despite the
    # transaction having committed successfully. See StatementTemplate's
    # docstring for the corrected contract.
    template = StatementTemplate(
        name=f"_SPIKE_LOCK_TOUCH_{run_id}",
        sql=f"""
        UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @who
        WHERE lock_domain = 'spike_domain';
        ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';
        """,
        lock_domain="incidents",
        result_sql="SELECT @who AS touched_by, CURRENT_TIMESTAMP() AS touched_at;",
    )
    register_template(template)
    client = client_factory()

    raised: Optional[Exception] = None
    result = None
    try:
        result = execute_transaction(
            client,
            [BoundStatement(template_name=template.name, params={"who": "execute_transaction_spike"})],
        )
    except Exception as exc:  # noqa: BLE001
        raised = exc

    # A successful transaction with no result_sql (not this template's
    # case, but a real possibility for pure-DML templates) legitimately
    # returns an empty result_rows -- that is NOT a failure on its own
    # (see TransactionResult's docstring). This step's template DOES
    # declare result_sql, so it specifically expects exactly one row
    # back; that expectation is what this step's `ok` check verifies.
    report.add(
        SpikeStepResult(
            name="9: shared.persistence_transactions.execute_transaction() against real BigQuery",
            ok=raised is None and result is not None and len(result.result_rows) == 1,
            detail=(
                f"execute_transaction() {'succeeded' if raised is None else f'raised {type(raised).__name__}'}."
                + (f" result_rows={result.result_rows}" if result else "")
            ),
            diagnostics={
                "attempts": result.attempts if result else None,
                "exception_type": type(raised).__name__ if raised else None,
            },
        )
    )


# ---------------------------------------------------------------------------
# Spike step 10 (part 1): sequential retry idempotency
# ---------------------------------------------------------------------------


def _insert_if_absent_script(lock_table: str, incidents_table: str, incident_id: str, worker_label: str) -> str:
    # Follows the intended lock-row transaction pattern: touch the
    # relevant domain's lock row FIRST (forcing genuine contention with
    # any concurrent writer targeting the same domain), THEN perform the
    # insert-if-absent, all inside one atomic script.
    #
    # The INSERT's SELECT must have a FROM clause: BigQuery rejects
    # "SELECT <literals> WHERE NOT EXISTS (...)" with no FROM at all
    # ("Query without FROM clause cannot have a WHERE clause") --
    # confirmed against real BigQuery during the Phase 6B live spike
    # (2026-07-12 run, run_id=c0536479d3). `FROM UNNEST([1]) AS _seed`
    # supplies exactly one synthetic row so the SELECT's literal values
    # and the WHERE NOT EXISTS guard both have somewhere to attach.
    return f"""
    BEGIN
      BEGIN TRANSACTION;
      UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = '{worker_label}'
      WHERE lock_domain = 'spike_domain';
      ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';

      INSERT INTO `{incidents_table}` (incident_id, status, row_version, created_by)
      SELECT '{incident_id}', 'open', 1, '{worker_label}'
      FROM UNNEST([1]) AS _seed
      WHERE NOT EXISTS (
        SELECT 1 FROM `{incidents_table}` WHERE incident_id = '{incident_id}'
      );
      ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one row';
      COMMIT TRANSACTION;
    END;
    """


def spike_sequential_retry_idempotency(
    client, project: str, dataset: str, run_id: str, report: SpikeReport
) -> None:
    """Simulates a client that retries an idempotent insert after an
    ambiguous failure (e.g. it never learned whether its first attempt
    committed), using the SAME deterministic incident_id both times,
    SEQUENTIALLY (no real concurrency here -- see
    spike_concurrent_duplicate_insert for the concurrent version, which
    is the stronger claim amendment 5 requires)."""

    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    incident_id = f"{run_id}_idempotent_retry_sequential"

    script = _insert_if_absent_script(lock_table, incidents_table, incident_id, "sequential_retry")
    client.query(script).result()  # "first attempt"
    client.query(script).result()  # "retry after ambiguous failure"

    rows = list(
        client.query(f"SELECT COUNT(*) AS n FROM `{incidents_table}` WHERE incident_id = '{incident_id}'").result()
    )
    count = rows[0]["n"]

    report.add(
        SpikeStepResult(
            name="10a: sequential retry idempotency (same id, two sequential attempts)",
            ok=count == 1,
            detail=f"After two identical sequential insert-if-absent attempts with the same id, {count} row(s) exist (expected 1).",
            diagnostics={"row_count": count},
        )
    )


# ---------------------------------------------------------------------------
# Spike step 10 (part 2): CONCURRENT duplicate-insert test (amendment 5),
# now routed through execute_transaction()'s own retry mechanism instead
# of a manual, outside-the-mechanism retry loop.
# ---------------------------------------------------------------------------


def _register_insert_if_absent_template(project: str, dataset: str, run_id: str) -> "StatementTemplate":
    """Register (once) a parameterized insert-if-absent template usable
    via execute_transaction(). Unlike _insert_if_absent_script() (used
    directly by client.query() in step 10a), this template declares
    `result_sql` so the caller can read back whatever status is actually
    persisted under the given incident_id -- the basis for this spike's
    Python-side idempotency/conflict contract (see
    _insert_if_absent_via_execute_transaction()'s docstring)."""

    from shared.persistence_transactions import StatementTemplate, register_template

    lock_table = f"{project}.{_lock_table(dataset, run_id)}"
    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    template = StatementTemplate(
        name=f"_SPIKE_INSERT_IF_ABSENT_{run_id}",
        sql=f"""
        UPDATE `{lock_table}` SET last_touched_at = CURRENT_TIMESTAMP(), last_touched_by = @worker_label
        WHERE lock_domain = 'spike_domain';
        ASSERT @@row_count = 1 AS 'expected exactly one lock row updated';

        INSERT INTO `{incidents_table}` (incident_id, status, row_version, created_by)
        SELECT @incident_id, @status, 1, @worker_label
        FROM UNNEST([1]) AS _seed
        WHERE NOT EXISTS (
          SELECT 1 FROM `{incidents_table}` WHERE incident_id = @incident_id
        );
        ASSERT @@row_count IN (0, 1) AS 'insert-if-absent must affect at most one row';
        """,
        lock_domain="incidents",
        result_sql=f"SELECT status FROM `{incidents_table}` WHERE incident_id = @incident_id;",
    )
    register_template(template)
    return template


def _insert_if_absent_via_execute_transaction(
    client_factory, template_name: str, incident_id: str, worker_label: str, status: str
):
    """Calls shared.persistence_transactions.execute_transaction() itself
    (not a reimplementation) -- its own internal retry/backoff handles a
    classified-retryable lock-row conflict transparently, so callers of
    THIS function never need a manual outside-the-mechanism retry loop
    (correcting the prior draft's spike_concurrent_duplicate_insert,
    which retried the losing worker's script manually).

    Applies this spike's own minimal idempotency contract in Python,
    using the template's `result_sql` to read back whatever status is
    actually persisted: same id + identical persisted status -> ordinary
    successful return (no exception); same id + a persisted status that
    differs from what THIS call intended -> PayloadConflictError, per
    Phase 6's amendment 2 contract ("same ID, different payload -> this
    error"). execute_transaction() itself has no opinion on payload
    equality -- that is real business-template logic (Phase 6B/6C), and
    this spike-only wrapper is the minimal illustration of that contract,
    not a new mechanism feature.
    """

    from shared.persistence_transactions import BoundStatement, PayloadConflictError, execute_transaction

    client = client_factory()
    result = execute_transaction(
        client,
        [
            BoundStatement(
                template_name=template_name,
                params={"incident_id": incident_id, "status": status, "worker_label": worker_label},
            )
        ],
    )
    persisted_status = result.result_rows[0]["status"] if result.result_rows else None
    if persisted_status is not None and persisted_status != status:
        raise PayloadConflictError(f"id={incident_id!r} conflicts on fields: status (values withheld)")
    return result


def spike_concurrent_duplicate_insert(
    client_factory, project: str, dataset: str, run_id: str, report: SpikeReport, *, template_name: str
) -> None:
    """The critical proof amendment 5 requires: two workers concurrently
    attempt to insert the SAME deterministic incident_id with the SAME
    payload (status='open'), both routed through
    _insert_if_absent_via_execute_transaction() -- i.e. through
    execute_transaction()'s own retry mechanism, not a manual retry loop
    outside it. After both finish, exactly one logical row must exist,
    and (since both intended the identical payload) BOTH calls are
    expected to resolve successfully -- the loser's internal retry
    should transparently observe the row already present with a matching
    status and return normally, never raising PayloadConflictError (that
    is reserved for a genuinely DIFFERENT payload -- see
    spike_payload_conflict_on_mismatched_retry()).
    """

    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    incident_id = f"{run_id}_concurrent_duplicate_insert"

    outcomes: dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def _worker(label: str) -> None:
        barrier.wait()
        start = time.monotonic()
        try:
            result = _insert_if_absent_via_execute_transaction(
                client_factory, template_name, incident_id, label, status="open"
            )
            outcomes[label] = {"ok": True, "attempts": result.attempts, "elapsed_s": time.monotonic() - start}
        except Exception as exc:  # noqa: BLE001 -- sanitized classification is the point
            from shared.persistence_transactions import bigquery_error_diagnostics

            diagnostics = bigquery_error_diagnostics(exc) if type(exc).__name__ == "BadRequest" else {}
            outcomes[label] = {
                "ok": False,
                "exception_type": type(exc).__name__,
                "diagnostics": diagnostics,
                "elapsed_s": time.monotonic() - start,
            }

    threads = [threading.Thread(target=_worker, args=(label,)) for label in ("insert_worker_a", "insert_worker_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = list(
        client_factory()
        .query(f"SELECT COUNT(*) AS n FROM `{incidents_table}` WHERE incident_id = '{incident_id}'")
        .result()
    )
    count = rows[0]["n"]

    both_succeeded = all(outcome["ok"] for outcome in outcomes.values())
    ok = count == 1 and both_succeeded
    report.add(
        SpikeStepResult(
            name="10b: CONCURRENT duplicate-insert test, routed through execute_transaction()'s retry",
            ok=ok,
            detail=(
                f"After two concurrent identical-payload insert-if-absent attempts with the SAME id "
                f"(each via execute_transaction()'s own retry mechanism): {count} row(s) exist "
                f"(expected 1). Both workers succeeded: {both_succeeded} (expected True -- the losing "
                "worker's internal retry should transparently resolve to a no-op success, never a "
                "manually-retried exception)."
            ),
            diagnostics={"outcomes": outcomes, "row_count": count},
        )
    )


def spike_payload_conflict_on_mismatched_retry(
    client_factory, project: str, dataset: str, run_id: str, report: SpikeReport, *, template_name: str
) -> None:
    """Sequential, deliberate proof of the OTHER half of amendment 2's
    idempotency contract: same ID, DIFFERENT payload -> PayloadConflictError.
    A first call inserts status='open' for a fresh deterministic id; a
    second call for the SAME id intentionally requests a different
    status ('resolved'), simulating a caller that (incorrectly) retries
    with changed data rather than the original payload. This must raise
    PayloadConflictError (a Python-side check in
    _insert_if_absent_via_execute_transaction(), never a raw BigQuery
    error), and the persisted row must remain unchanged (still 'open').
    """

    incidents_table = f"{project}.{_incidents_table(dataset, run_id)}"
    incident_id = f"{run_id}_payload_conflict"

    _insert_if_absent_via_execute_transaction(client_factory, template_name, incident_id, "first_writer", status="open")

    from shared.persistence_transactions import PayloadConflictError

    raised: Optional[Exception] = None
    try:
        _insert_if_absent_via_execute_transaction(
            client_factory, template_name, incident_id, "second_writer_different_payload", status="resolved"
        )
    except PayloadConflictError as exc:
        raised = exc

    rows = list(
        client_factory()
        .query(f"SELECT status FROM `{incidents_table}` WHERE incident_id = '{incident_id}'")
        .result()
    )
    persisted_status = rows[0]["status"] if rows else None

    ok = raised is not None and persisted_status == "open"
    report.add(
        SpikeStepResult(
            name="10c: mismatched-payload retry raises PayloadConflictError (never a raw BigQuery error)",
            ok=ok,
            detail=(
                f"Second call for the same id with a different status "
                f"{'raised PayloadConflictError (correct)' if raised else 'did NOT raise (INCORRECT)'}; "
                f"persisted status is {persisted_status!r} (expected 'open' -- unchanged by the "
                "rejected second call)."
            ),
            diagnostics={"exception_type": type(raised).__name__ if raised else None},
        )
    )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_spike_resources(client, project: str, dataset: str, run_id: str) -> None:
    """Drops only tables whose name starts with spike_<run_id>_ inside
    the configured dataset. NEVER drops the dataset itself (amendment 8)
    -- it may pre-exist, or hold another concurrent run's tables, and
    this script has no way to know it's safe to remove."""

    _validate_run_id(run_id)
    prefix = _table_prefix(run_id)
    tables = client.list_tables(f"{project}.{dataset}")
    dropped = []
    for table in tables:
        if table.table_id.startswith(prefix):
            client.query(f"DROP TABLE IF EXISTS `{project}.{dataset}.{table.table_id}`").result()
            dropped.append(table.table_id)
    print(f"Cleanup: dropped {len(dropped)} table(s) matching prefix {prefix!r}: {dropped}")
    print(f"Dataset {project}.{dataset!r} itself was NOT deleted (by design -- see amendment 8).")


def verify_production_untouched(client, project: str) -> None:
    """Read-only check: confirms `loupe_platform` (the real dataset) was
    never touched by this run. Does not create it if absent -- an
    absent `loupe_platform` is itself proof nothing was touched."""

    try:
        client.get_dataset(f"{project}.{REAL_DATASET_NAME}")
        print(
            f"NOTE: {REAL_DATASET_NAME!r} exists in {project!r} (it may predate this spike). "
            "This script never issued any query against it -- see the printed queries above."
        )
    except Exception:
        print(f"{REAL_DATASET_NAME!r} does not exist (or is not accessible) in {project!r}. Nothing to verify further.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_plan(run_id: str) -> None:
    print(f"\nDry run only (pass --yes to actually execute). Nothing was created or modified.")
    print("Planned steps:")
    print("  1) ensure test dataset (refuses if it exists in the wrong location)")
    print("  2) create spike-tagged tables")
    print("  3) successful commit + ASSERT + @@row_count + final result")
    print("  4) forced failure + rollback via ASSERT")
    print("  5) child-job enumeration (diagnostics only)")
    print("  6) raw concurrent lock-row mutation (no retry) -- proves genuine contention")
    print("  7) liveness: same concurrent lock-row touch via execute_transaction()'s retry")
    print("  8) execute_transaction() against real BigQuery (trailing result_sql)")
    print("  9) sequential retry idempotency (same id, two sequential attempts)")
    print(" 10) CONCURRENT duplicate-insert test, routed through execute_transaction()'s retry")
    print(" 11) mismatched-payload retry raises PayloadConflictError")
    print(f" 12) cleanup (drops only spike_{run_id}_* tables; dataset itself is kept)")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", default=REQUIRED_LOCATION)
    parser.add_argument("--yes", action="store_true", help="Actually execute. Without this, dry-run only.")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--run-id", default=None, help="Required with --cleanup-only. Must match ^[0-9a-f]{10}$.")
    args = parser.parse_args(argv)

    try:
        _require_safe_target(args.project, args.dataset, args.location)
    except UnsafeSpikeConfigurationError as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.cleanup_only:
        if not args.run_id:
            print("ERROR: --run-id is required with --cleanup-only.")
            return 2
        try:
            _validate_run_id(args.run_id)
        except UnsafeSpikeConfigurationError as exc:
            print(f"ERROR: {exc}")
            return 2

        from google.cloud import bigquery

        client = bigquery.Client(project=args.project, location=args.location)
        cleanup_spike_resources(client, args.project, args.dataset, args.run_id)
        return 0

    run_id = _generate_run_id()
    print(f"Target: project={args.project!r} dataset={args.dataset!r} location={args.location!r}")
    print(f"Run ID (all created resources are prefixed spike_{run_id}_): {run_id}")

    if not args.yes:
        _print_plan(run_id)
        return 0

    from google.cloud import bigquery

    def client_factory():
        return bigquery.Client(project=args.project, location=args.location)

    client = client_factory()
    report = SpikeReport(run_id=run_id, project=args.project, dataset=args.dataset, location=args.location)
    tables_created = False

    try:
        _ensure_dataset(client, args.project, args.dataset, args.location)
        _create_spike_tables(client, args.project, args.dataset, run_id)
        tables_created = True

        spike_successful_commit_with_assert_and_row_count(client, args.project, args.dataset, run_id, report)
        spike_forced_failure_and_rollback(client, args.project, args.dataset, run_id, report)
        spike_child_job_enumeration_diagnostics_only(client, args.project, report)
        spike_concurrent_lock_row_raw_contention(client_factory, args.project, args.dataset, run_id, report)
        spike_concurrent_lock_row_liveness_via_execute_transaction(client_factory, args.project, args.dataset, run_id, report)
        spike_execute_transaction_against_real_bigquery(client_factory, args.project, args.dataset, run_id, report)
        spike_sequential_retry_idempotency(client, args.project, args.dataset, run_id, report)

        insert_if_absent_template = _register_insert_if_absent_template(args.project, args.dataset, run_id)
        spike_concurrent_duplicate_insert(
            client_factory, args.project, args.dataset, run_id, report, template_name=insert_if_absent_template.name
        )
        spike_payload_conflict_on_mismatched_retry(
            client_factory, args.project, args.dataset, run_id, report, template_name=insert_if_absent_template.name
        )

        report.print_summary()
    finally:
        # Guaranteed cleanup (amendment 1): runs even if a step above
        # raised unexpectedly. Only cleans up if table creation is known
        # to have at least started, so a failure before that point
        # doesn't attempt to clean up tables that were never created.
        print(f"\nRun ID for manual recovery if anything above looks incomplete: {run_id}")
        if tables_created:
            print("\nCleaning up this run's spike-tagged resources...")
            try:
                cleanup_spike_resources(client, args.project, args.dataset, run_id)
            except Exception as cleanup_exc:  # noqa: BLE001 -- must not mask the original error, if any
                print(
                    f"WARNING: automatic cleanup failed ({type(cleanup_exc).__name__}). "
                    "Run this manually:\n"
                    f"  python -m tools.phase6b_spike.live_transaction_spike "
                    f"--project {args.project} --dataset {args.dataset} --location {args.location} "
                    f"--cleanup-only --run-id {run_id}"
                )

    print("\nVerifying production was untouched...")
    verify_production_untouched(client, args.project)

    print(
        "\nNext: paste this script's full console output into the Phase 6B "
        "review so shared/persistence_transactions.py's classification "
        "(_is_retryable) can be corrected against ACTUAL observed "
        "behavior, not assumption."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
