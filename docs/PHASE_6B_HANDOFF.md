# Phase 6B/6C Handoff

Status as of this handoff: **Phase 6C complete: persisted metric-catalog reads, explicit one-time seeding, and governed metric certification are implemented, tested, and green. Application cutover, live BigQuery seeding/certification, and Phase 6D remain out of scope -- see "Phase 6C: persisted metric catalog and governed certification" below.**

## Phase 6C: persisted metric catalog and governed certification

New module: `shared/metric_catalog_persistence.py`, built on the same spike-verified `execute_transaction()` mechanism Phase 6B's three business-persistence modules already use. Three responsibilities:

| Responsibility | Functions | Notes |
|---|---|---|
| Persisted reads | `get_current_definition`, `get_version_history`, `resolve_current_definition` | Pure `run_query()` reads (no transaction needed). `resolve_current_definition()` is the persisted-mode-safe wrapper: a raised exception becomes `ok=False` with a safe error, never a silent fallback to `shared.metric_catalog`'s in-memory registry -- matching `shared.config.ConfigValidationResult`'s pattern. `certification_status` is read from `metric_catalog`'s pointer row (not the resolved version's own field), so `pending_validation` is preserved honestly. |
| Explicit one-time seeding | `seed_metric_definition`, `seed_current_catalog` | Insert-if-absent for both `metric_versions` (keyed name+version) and `metric_catalog` (keyed name), guarded by the `metric_catalog` write-lock domain -- the same shape `CREATE_INCIDENT_TXN`/`WRITE_AUDIT_EVENT_TXN` already use. Idempotent on identical retry; a content-hash mismatch or a pointer already at a different version raises `PayloadConflictError`. Refuses any definition whose `certification_status` isn't `pending_validation`. Only ever invoked for real via this module's own `python -m shared.metric_catalog_persistence seed --project ... --yes` CLI -- never at app startup, never during this phase's implementation or tests. |
| Governed certification | `certify_metric_definition` | One atomic script: inserts a new immutable `metric_versions` row (`prior_version` set to the caller's `expected_current_version`), advances `metric_catalog`'s pointer (guarded by the same `expected_current_version` as an optimistic-concurrency check), and writes the certification's audit event -- committed together via one `execute_transaction()` call. Requires `reviewer`, `validation_evidence`, `reviewed_at`, and `change_reason` up front; refuses if `reviewer == created_by` (separation of duties) before any BigQuery call. Uses the canonical `shared.metric_hashing.compute_content_hash()`. |

**Audit ownership**: `certify_metric_definition()`'s audit event is written by composing `shared.audit_persistence.WRITE_AUDIT_EVENT_TXN` as a second `BoundStatement` in the same `execute_transaction()` call, rather than re-embedding an equivalent audit-insert SQL fragment a third time (as `schema_baseline_persistence.py` currently does) or calling the streaming `shared.audit.write_event()` (which would neither be atomic with the metric writes nor idempotent). A static test (`test_module_never_imports_the_streaming_write_event_path`) confirms `certify_metric_definition()`'s own source never references `write_event(`. `shared.audit.write_event()` remains the right tool only for ordinary, single-shot audit writes with no accompanying state change that must commit atomically alongside it.

**Not done in Phase 6C** (explicitly out of scope, staying deferred): wiring any `apps/*/main.py` to call `get_current_definition`/`resolve_current_definition`/`certify_metric_definition`/seeding (enforced by two new boundary tests in `tests/test_persistence_boundary.py`), any real (non-test) seeding or certification against `loupe_platform`, refactoring `schema_baseline_persistence.py` to reuse `audit_persistence.py`'s template the same way, Phase 6D, UI implementation, and further concurrency research beyond what Phase 6B's live spike already confirmed (this module introduces no new correctness mechanism -- it reuses `execute_transaction()`'s existing, proven one).

Test suite after Phase 6C: **32 new focused tests** (30 in `tests/shared/test_metric_catalog_persistence.py`, 2 new boundary tests in `tests/test_persistence_boundary.py`), covering current-definition resolution, version history, identical-seed idempotency, conflicting-seed rejection, certification with and without a content change, atomic rollback on a simulated ASSERT failure, reviewer-vs-creator enforcement, `pending_validation` preservation, persistence-unavailable behavior, and no-startup-seeding. All credential-free against the fake BigQuery client.

Full suite after Phase 6C: **785 passed, 0 failed** (753 baseline + 32 new), fully credential-free.

---

## Phase 6A/6B history (preserved below, unchanged)

Status as of the original Phase 6B handoff: **Live spike verification complete. Phase 6B business persistence (atomic incident creation + transition history, idempotent audit-event persistence, transactional schema-baseline promotion) is now implemented, tested, and green. Metric-catalog certification persistence, app cutover, and any real (non-test) dataset bootstrap remain out of scope -- Phase 6C+.**

## 2026-07-12 final live spike confirmation (narrowly-scoped rerun)

The narrowly-scoped rerun requested at the end of the prior entry below was completed. Confirmed against real BigQuery:

- Transaction commit and rollback (ASSERT-triggered) both verified.
- `ASSERT` and `@@row_count` verified.
- `execute_transaction()` verified end-to-end, including its trailing `result_sql`-after-`COMMIT` ordering.
- Sequential idempotency verified (same id, two sequential insert-if-absent attempts -> one row).
- Concurrent duplicate prevention verified: two workers, same deterministic id, routed through `execute_transaction()`'s own retry -- exactly one logical row remained.
- Confirmed genuine conflicts (matching the confirmed `concurrent_update_signature_matched` signature) retried successfully to a committed outcome.
- Different-payload reuse of the same id correctly raised `PayloadConflictError` (Python-side, never a raw BigQuery error), and the persisted row was left unchanged.
- Cleanup succeeded; `loupe_platform` (production) was never touched.

One residual finding: some genuine concurrent-conflict failures surfaced with wording the current conservative message-signature matcher (`_matches_concurrent_update_signature()` in `shared/persistence_transactions.py`) does not recognize, so a subset of real conflicts are currently classified non-retryable rather than retried.

**Decision: do not spend another round broadening the matcher or rerunning the spike now.** The mechanism's conservative default is kept as-is:

- Retry only conflicts matching the confirmed signature.
- Treat unrecognized `BadRequest`/`invalidQuery` errors as non-retryable.
- Surface them safely without raw messages (per `bigquery_error_diagnostics()`'s existing sanitization contract).
- Never retry `invalidQuery` generally.

This means a real, unrecognized-wording conflict currently surfaces to the caller as an ordinary unclassified exception rather than being retried transparently -- correctness is preserved (nothing commits partially, nothing silently loses a write), but liveness under that specific conflict shape is not yet as smooth as it could be. **Recorded as a Phase 6E operational-hardening item: tolerant conflict-message classification**, not solved in Phase 6B.

## Phase 6B business persistence: implemented

Three focused modules, each built directly on the spike-verified `execute_transaction()` mechanism (script rendering, `ASSERT @@row_count`, `result_sql`-after-`COMMIT`, the `write_locks` lock-row contention pattern) and registering their templates at import time, per the closed-allowlist discipline `tests/test_persistence_boundary.py` enforces:

| Module | Templates | Responsibility |
|---|---|---|
| `shared/incident_persistence.py` | `CREATE_INCIDENT_TXN`, `TRANSITION_INCIDENT_STATUS_TXN` | Atomic insert-if-absent incident creation (idempotent; `PayloadConflictError` on a same-id/different-severity-or-status conflict); atomic status transition + `incident_transitions` history row in one script, guarded by `shared.incidents.validate_transition()` client-side before any BigQuery call |
| `shared/audit_persistence.py` | `WRITE_AUDIT_EVENT_TXN` | Idempotent, transactional audit-event write (insert-if-absent by `event_id`), complementing (not replacing) `shared/audit.py`'s existing streaming-insert `write_event()`; reuses `shared.audit`'s secret-scan via a new public `validate_no_secrets()` wrapper |
| `shared/schema_baseline_persistence.py` | `PROMOTE_SCHEMA_BASELINE_TXN` | Atomic schema-baseline upsert (`MERGE` on `(dataset, table_id)`) + audit-event insert, both write-lock domains (`schema_baselines` then `audit_events`) touched in the same script before their respective writes |

Also fixed: `shared/persistence_transactions.py::_build_job_config()` previously raised `ValueError` for any empty-list array parameter, which made it impossible to create an incident with no `affected_metrics`/`affected_dashboards` yet -- a common, legitimate case. Empty lists now bind as a typed, empty `ArrayQueryParameter` (`STRING` element type, matching every current empty-array column). This is a narrow client-side parameter-binding fix, not a change to retry/error-classification behavior, so it required no additional live spike.

Two SQL shapes used by the new templates -- `PARSE_JSON(@x)`-on-a-bound-`STRING` (for `audit_events.context` and `schema_baselines.columns`, both complex/nested column types with no native named-parameter binding in the BigQuery Python client) -- are new in Phase 6B and have **not** themselves been exercised by the live spike; only `execute_transaction()`'s core mechanism (the part every template shares) was live-verified. Flagged in each module's docstring as a follow-up live check, not blocking Phase 6B's fake-client-tested contract.

Deterministic-ID discipline is unchanged from Phase 2/5: `incident_id` and `event_id` continue to be caller-supplied (e.g. `apps/data_quality_triage/checks.py`'s existing `f"{dataset}.{table_id}.{check_name}.{created_at}"` construction) -- this phase did not introduce a new ID-generation scheme, only made persisting those IDs atomic and idempotent.

Test suite after Phase 6B: **34 new focused tests** across `tests/shared/test_incident_persistence.py` (16), `tests/shared/test_audit_persistence.py` (9), `tests/shared/test_schema_baseline_persistence.py` (9), plus 2 new regression tests in `tests/shared/test_persistence_transactions.py` for the empty-array fix. All credential-free against the fake BigQuery client. Full suite run once at completion (see bottom of this document for the count).

**Not done in Phase 6B** (explicitly out of scope, staying deferred): metric-catalog certification persistence, any app (`apps/*/main.py`) wiring to call these new functions, any real (non-test) `loupe_platform` dataset bootstrap, and the Phase 6E tolerant-conflict-matcher work noted above.

---

## Phase 6A/pre-6B history (preserved below, unchanged)

Status as of the original handoff: **Phase 6A complete. Live BigQuery spike run once (2026-07-12, run_id=c0536479d3), found 3 real defects, all corrected and covered by new credential-free tests. Spike must be RERUN before Phase 6B business persistence begins.**

## 2026-07-12 live spike run: findings and corrections

The operator ran the spike for the first time. Confirmed working: commit +
`ASSERT @@row_count`, forced-failure rollback (both the lock-row update
and the incident insert were absent afterward), `client.list_jobs(parent_job=job)`
child-job enumeration, automatic cleanup, and that `loupe_platform` was
never touched. Three real defects were found and are now fixed (all in
this commit, all covered by new tests, none require a live spike to
verify the fix itself -- only to confirm the spike now runs clean
end-to-end):

1. **Invalid SQL** (`tools/phase6b_spike/live_transaction_spike.py`,
   `_insert_if_absent_script()`): BigQuery rejected
   `SELECT <literals> WHERE NOT EXISTS (...)` with "Query without FROM
   clause cannot have a WHERE clause." This aborted step 10a immediately
   and prevented steps 10a, 10b, and the final production-untouched
   verification from ever running. Fixed by adding
   `FROM UNNEST([1]) AS _seed`. Regression tests:
   `tests/test_phase6b_spike_validators.py`.
2. **Result-row bug in `execute_transaction()`** (`shared/persistence_transactions.py`,
   `_render_script()`): step 9 (`execute_transaction()` against real
   BigQuery) succeeded but returned an empty `result_rows`, because a
   template's trailing `SELECT` embedded inline in `sql` was rendered
   BEFORE the script's `COMMIT TRANSACTION;`, not after -- so it was
   never the script's final statement. Fixed by adding a dedicated
   `StatementTemplate.result_sql` field that `_render_script()` renders
   AFTER `COMMIT TRANSACTION;`. This is a real correctness fix to the
   mechanism every future Phase 6B business template will use, not just
   a spike-only patch. Regression tests: `tests/shared/test_persistence_transactions.py`
   (`test_render_script_places_result_sql_after_commit_transaction` and
   neighbors).
3. **Error classification was unverified against real BigQuery**
   (`shared/persistence_transactions.py`, `_is_retryable()`): all
   observed failures -- the deliberate ASSERT failure (step 2, correctly
   non-retryable), the concurrent lock-row conflicts (steps 7/8), and
   the SQL bug above (step 10a) -- raised the SAME exception type,
   `google.api_core.exceptions.BadRequest`. Type-name-only classification
   cannot distinguish a genuine SQL/ASSERT failure from genuine
   contention. Added `bigquery_error_diagnostics()` (sanitized: exception
   class, structured `errors[].reason` codes, HTTP status -- never the
   raw message) and changed `_is_retryable()` to treat `BadRequest` as
   retryable only when its reason code is in
   `_CONFIRMED_CONCURRENT_CONFLICT_REASONS`, which **starts empty** --
   no reason code for BigQuery's documented concurrent-conflict condition
   has been confirmed yet (the first run only captured type names, not
   structured reason codes). The spike's own concurrent-lock-row
   classification (`_classify_concurrency_round`) was also refactored to
   classify using the real exception instance inside the worker thread
   (via `_is_retryable()`), rather than reconstructing a fake exception
   from a type-name string downstream. **The rerun's output must be used
   to populate `_CONFIRMED_CONCURRENT_CONFLICT_REASONS` from confirmed
   evidence, never a guess.**

A fourth, unrelated defect was also fixed: `pyproject.toml` had no
`[build-system]` table and no explicit `[tool.setuptools.packages.find]`
config, so `pip install -e .` had no documented, reliable package
discovery for this multi-package (`apps/`, `shared/`, `tools/`) flat
layout; `tools/` was also missing its own top-level `__init__.py` (only
`tools/phase6b_spike/__init__.py` existed). Both fixed; see
`tests/test_packaging.py` for a credential-free, install-free check
(drives `setuptools.find_packages()` with the declared include/exclude
patterns directly against the repo tree).

Full suite after these corrections: **706 passed, 0 failed** (675
baseline + 31 new tests), still fully credential-free.

**The spike must be rerun from a clean state before Phase 6B business
persistence begins** -- this repo has not yet observed the corrected
spike reach steps 10a/10b or the final production-untouched check, and
`_CONFIRMED_CONCURRENT_CONFLICT_REASONS` remains empty until a rerun's
structured diagnostics confirm a real value.

## 2026-07-12 rerun (run_id=ad466ad893): confirmed and corrected further

The corrected spike was rerun and reached every step, including 10a,
10b, and the final production-untouched check. Confirmed working: commit
+ ASSERT + `@@row_count`, forced-failure rollback, child-job enumeration,
`execute_transaction()`'s trailing `result_sql` (step 9 now returns its
row correctly), sequential retry idempotency, the concurrent
duplicate-insert proof (exactly one row, losing worker's retry resolved
as an identical-payload no-op), and `loupe_platform` untouched.

One finding required a further correction: **both** concurrent workers
touching the same lock row were cancelled by BigQuery in every attempt
(not "one wins, one loses" as previously assumed), and the loser(s)
raised `google.api_core.exceptions.BadRequest` / HTTP 400 / reason
`invalidQuery` -- the SAME type and reason code as the deliberately
-failing ASSERT in step 2. Reason code alone cannot distinguish a
genuine SQL/ASSERT defect from genuine contention. Corrected:

1. **`_CONFIRMED_CONCURRENT_CONFLICT_REASONS` mechanism replaced.**
   `_is_retryable()` now requires all three of: HTTP status 400, reason
   `invalidQuery`, AND a sanitized `concurrent_update_signature_matched`
   boolean -- computed by inspecting the exception's raw message
   *internally, only to produce that one boolean* (never
   returned/logged/persisted) against BigQuery's own documented phrase
   `"Transaction is aborted due to concurrent update against table"`.
   `bigquery_error_diagnostics()` now exposes exactly: exception class,
   HTTP status, reason codes, and that boolean -- never raw message text.
2. **Contention classification corrected for the "both cancelled" shape.**
   `_classify_concurrency_round()` now treats a round as `confirmed` when
   *every* failing worker's failure is retryable (covers both "one loses"
   and "both get cancelled" -- the shape actually observed), not only
   "exactly one succeeded."
3. **Raw contention and retry-driven liveness are now separate steps.**
   `spike_concurrent_lock_row_raw_contention()` (step 6, no retry, proves
   contention exists) and `spike_concurrent_lock_row_liveness_via_execute_transaction()`
   (step 7, same touch routed through `execute_transaction()`'s own
   retry+backoff, proves at least one caller's retry resolves to
   liveness) replace the old combined step. Retry-budget exhaustion
   (`ConcurrentModificationError`) is proven deterministically
   credential-free (`tests/shared/test_persistence_transactions.py`,
   `test_execute_transaction_exhausts_retry_budget_on_sustained_concurrent_update_conflict`)
   rather than attempted live, where sustained contention isn't
   reliably reproducible on demand.
4. **The concurrent duplicate-insert test now routes through
   `execute_transaction()`'s own retry**, not a manual retry loop outside
   it (`_insert_if_absent_via_execute_transaction()`,
   `_register_insert_if_absent_template()`). A new step,
   `spike_payload_conflict_on_mismatched_retry()`, proves the other half
   of amendment 2's contract: the SAME id with a genuinely DIFFERENT
   payload raises `PayloadConflictError` (a Python-side check against the
   template's `result_sql`, never a raw BigQuery error), and the
   persisted row is left unchanged.

Full suite after these corrections: **717 passed, 0 failed** (was 706;
+11 new tests), still fully credential-free.

**A further, narrowly-scoped live rerun is needed** to confirm the new
`concurrent_update_signature_matched` boolean and the liveness-via-retry
step behave as designed against real BigQuery (the message-signature
match itself was derived from this run's evidence but the corrected
`_is_retryable()` gate combining all three conditions has not yet been
observed end-to-end live). Phase 6B business persistence still does not
begin until that verification passes.

## Phase 6A architecture

Phase 6A built the persistence foundation Phase 6B's business logic will sit on top of, entirely under `shared/` (framework-independent, no Streamlit imports) so it's usable from any app in the monorepo.

| Concern | File | Responsibility |
|---|---|---|
| Data model | `shared/models.py` | `MetricVersion`, `Incident` (with `sql_template`/`query_hash`), and related dataclasses |
| Config | `shared/config.py` | `PlatformConfig` + `validate_persistence_config()` — project/dataset/location validation, no caching (that's an app-layer concern) |
| Schema | `shared/schema_management.py` | 9-table DDL, forward-only idempotent migrations (`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` only — no `DROP`/`TRUNCATE` anywhere), bootstrap is CLI-only, never triggered from app startup |
| Transactions | `shared/persistence_transactions.py` | `execute_transaction()`, `StatementTemplate`/`BoundStatement`, `RetryPolicy` (exponential backoff + full jitter), `_is_retryable()` error classification, the `write_locks` lock-row pattern over a fixed `LOCK_DOMAINS` set (`incidents`, `audit_events`, `metric_catalog`, `schema_baselines`), and the private `_TEMPLATES` allowlist (`apps/` may never register templates directly — enforced by `tests/test_persistence_boundary.py`) |
| Audit | `shared/audit.py` | `audit_events` table writer, recursive secret-scanning before any payload is persisted |
| Test doubles | `tests/shared/conftest.py` | `FakeBigQueryClient`/`FakeDataset`/`FakeTable` — in-memory BigQuery stand-ins supporting query/insert/transaction/dataset-lookup simulation, no real cloud access |

**Correctness mechanism (post pre-6B-spike revision):** `execute_transaction()` no longer depends on inspecting a script job's child jobs (`job.child_statement_results()` — that method does not exist on a real `google.cloud.bigquery.QueryJob`). Each `StatementTemplate` is responsible for embedding its own `ASSERT @@row_count = N` invariants in its SQL; `execute_transaction()` simply returns whatever rows the script's final statement (typically a trailing `SELECT`) produced, via `TransactionResult.result_rows`. If the script raises, its `COMMIT` never happened — full stop, no ambiguous partial-commit state.

## Test results

```
675 passed, 0 failed, 1 warning (pre-existing, unrelated Python 3.10 EOL notice from google.api_core)
```

Fully credential-free — no BigQuery client, no ADC, no network access required to run this suite.

## Live BigQuery spike: all safety corrections completed

`tools/phase6b_spike/live_transaction_spike.py` is the minimal guarded live-transaction verification required before Phase 6B business persistence begins. A second review round required 9 corrections, all now implemented and covered by credential-free tests:

1. Guaranteed cleanup via `try/finally` around setup + all spike steps; `run_id` printed at start and end for manual recovery.
2. `--cleanup-only --run-id` strictly validated against `^[0-9a-f]{10}$` (via `fullmatch`, closing a trailing-newline edge case) before any table is listed or dropped.
3. `--project`/`--dataset` validated against safe GCP identifier patterns before SQL interpolation; `--location` must equal `US` exactly; an existing dataset's actual location is read back and a mismatch is a hard refusal (never a silent `exists_ok=True`).
4. Concurrent lock-row test reports outcomes honestly as `confirmed` / `inconclusive` / `failed` (not "at least one succeeded"), with up to 3 bounded attempts to obtain genuine overlap.
5. A new **concurrent** duplicate-insert test was added (two workers, same deterministic incident ID, lock-row pattern) alongside the existing sequential-retry idempotency test — the critical proof that the write-lock pattern provides logical uniqueness under real concurrency, not just sequential retries.
6. The script is no longer left in a known-broken state: `execute_transaction()` was fixed first (see "Correctness mechanism" above), so step 9 is now expected to succeed, not predicted to fail.
7. Required-permissions documentation corrected: dataset creation may need `bigquery.datasets.create`, which BigQuery Data Editor + Job User does not guarantee — README documents both remediation paths.
8. Cleanup is documented and implemented to only ever drop `spike_<run_id>_*` tables — `loupe_platform_test` itself is never deleted by any code path.
9. 42 new credential-free tests added (`tests/test_phase6b_spike_validators.py`) covering the run-id/project/dataset/location validators; full suite rerun and confirmed green.

## Live spike status: prepared, NOT run

Nothing in `shared/persistence_transactions.py` about real BigQuery multi-statement transaction behavior is verified until this script is actually executed and its output reviewed. This environment has no `gcloud` CLI, no Application Default Credentials, and no `GOOGLE_CLOUD_PROJECT` configured — the spike cannot and must not be run from here.

## Target

| | |
|---|---|
| Project | `ai-weekend-agent-501502` |
| Dataset | `loupe_platform_test` |
| Location | `US` |

## Authentication and credential rules

- The spike must be run by a human operator from an authenticated environment: a local terminal with `gcloud auth application-default login` completed, or Google Cloud Shell (which provides an authenticated identity automatically).
- Nothing in this repository or the spike script requests, generates, uploads, or reads a service-account JSON key.
- No credential, token, or ADC content is ever displayed, logged, or committed — the spike script prints only exception type/module names for classification, never raw exception text or secret material.
- Required IAM: BigQuery Job User + BigQuery Data Editor scoped to `loupe_platform_test`, plus either the dataset already existing in `US` (created ahead of time by someone with `bigquery.datasets.create`) or the running identity itself holding `bigquery.datasets.create`.

## Exact files required for the spike

- `tools/phase6b_spike/live_transaction_spike.py` — the script itself
- `tools/phase6b_spike/README.md` — full run instructions, safety guards, expected output, cleanup guarantee
- `tools/phase6b_spike/__init__.py` — package marker (never imported by `shared/`/`apps/`/`tests/`)
- `shared/persistence_transactions.py` — the real `execute_transaction()`/`StatementTemplate`/`register_template` the spike calls directly in step 9 (not a reimplementation)
- Operator command:

```bash
cd loupe
python -m tools.phase6b_spike.live_transaction_spike \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US \
  --yes
```

## Remaining Phase 6B work (superseded — see "Phase 6B business persistence: implemented" above)

1. ~~Confirm or correct this repo's assumptions against the spike's actual output~~ — done; see "Known BigQuery assumptions" below, updated.
2. ~~Build the real business transaction templates~~ — done for incident creation/transition and schema-baseline promotion (see table above). Metric certification is explicitly **not** built — deferred to Phase 6C.
3. **Not done**: wiring these templates into `apps/` business logic. Remains Phase 6C+/6D work, per the original plan (`apps/` may only call the persistence-layer functions above, never `register_template()`/`execute_transaction()` directly — enforced by `tests/test_persistence_boundary.py`).
4. ~~Extend `tests/` with integration-safety tests for each new business template~~ — done (34 new focused tests; see table above).
5. **Not done**: any real (non-test) dataset bootstrap. Still out of scope.

## Known BigQuery assumptions: resolved vs. still open

Resolved by the live spike (see "final live spike confirmation" above):

- ✅ A failing `ASSERT` inside a `BEGIN ... END` script fully rolls back all prior statements in that script — confirmed.
- ✅ The exception type/reason code BigQuery raises for a genuine concurrent lock-row conflict — confirmed: `google.api_core.exceptions.BadRequest` / HTTP 400 / reason `invalidQuery`, distinguished from an ordinary ASSERT/syntax failure only by the `concurrent_update_signature_matched` message-signature check.
- ✅ `client.list_jobs(parent_job=job)` — confirmed usable for diagnostics; remains informational/never load-bearing.

Still open (deferred, not blocking Phase 6B):

- Real-world latency/backoff behavior under actual BigQuery contention beyond what the two spike runs observed, as opposed to the fake client's simulated retry queue.
- Whether `bigquery.datasets.create` is actually required in the target project's IAM configuration, or whether Data Editor already suffices there.
- The `PARSE_JSON(@x)`-on-bound-`STRING` SQL shape used by the two new Phase 6B templates that bind complex column types (`audit_events.context`, `schema_baselines.columns`) — not itself exercised by the live spike (see "Phase 6B business persistence: implemented" above).
- Phase 6E: tolerant conflict-message classification, so a real conflict with unrecognized wording is retried rather than surfaced as an unclassified error.

## Original repositories must remain untouched

The spike creates only `spike_<run_id>_*`-prefixed tables inside `loupe_platform_test` and never issues any statement referencing `loupe_platform` (the real production dataset name — refused outright by `_require_safe_target()` if passed as `--dataset`). Cleanup never deletes the dataset itself. No application code, no real metric catalog rows, and no other repository in this workspace (e.g. `ecommerce-analytics-agent`) is read, modified, or referenced by any part of this work.
