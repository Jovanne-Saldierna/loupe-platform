# Phase 6B live transaction spike

**Status: prepared, not yet run.** This directory contains the exact
script, commands, and safety checks for the live BigQuery verification
required before Phase 6B business persistence begins. It has not been
executed — every claim in `shared/persistence_transactions.py` about
real BigQuery multi-statement transaction behavior remains an unverified
assumption until you run this and share the output.

This was prepared in an environment with no `gcloud` CLI, no
Application Default Credentials file, and no `GOOGLE_CLOUD_PROJECT`
configured — there is no authenticated Google Cloud identity available
here, and per your instructions nothing here ever requests, generates,
uploads, or reads a service-account key. You run this yourself.

## Revision note (this version)

This is a **revised** version of the spike, corrected per a second
round of review before being run. Changes from the prior draft:

1. **Guaranteed cleanup.** Dataset/table setup and all spike steps now
   run inside a `try/finally` block, so cleanup always fires even if a
   step raises unexpectedly. The `run_id` is printed at both the start
   and the end specifically so it's available for manual
   `--cleanup-only` recovery if the automatic cleanup itself fails.
2. **Strict `--run-id` validation.** `--cleanup-only`'s `--run-id` must
   match `^[0-9a-f]{10}$` exactly (the same format
   `uuid.uuid4().hex[:10]` always produces) — checked before any
   `list_tables`/`DROP TABLE` call. Empty, shortened, extended,
   wildcard-like, punctuation-containing, and non-hex values are all
   rejected. See `tests/tools/test_phase6b_spike_validators.py`.
3. **Identifier + location validation.** `--project` and `--dataset`
   are validated against safe Google Cloud identifier patterns before
   ever being interpolated into a SQL identifier. `--location` must
   equal `US` exactly. If `loupe_platform_test` already exists,
   its actual location is read back and compared — a mismatch is a
   hard refusal, never a silent `exists_ok=True` accept.
4. **Honest concurrency reporting.** The concurrent lock-row test now
   classifies each attempt as `confirmed` (exactly one worker
   succeeded and the loser's failure was classified retryable by
   `shared.persistence_transactions._is_retryable()`), `inconclusive`
   (both workers succeeded — no genuine contention was observed), or
   `failed` (neither succeeded, or the loser's failure was NOT
   classified retryable). Up to 3 bounded attempts run automatically
   if the first doesn't produce genuine overlap.
5. **New: concurrent duplicate-insert test.** In addition to the
   existing *sequential* retry-idempotency test, a *separate* test now
   runs two workers **concurrently** attempting to insert the SAME
   deterministic incident ID using the intended lock-row transaction
   pattern, asserts exactly one logical row exists afterward, and
   retries the losing worker's script once to confirm it resolves to
   an identical-payload no-op. This is the proof that the write-lock
   pattern provides logical uniqueness under real concurrency, not
   just under sequential retries.
6. **`execute_transaction()` is no longer left in a known-broken
   state.** The underlying adapter
   (`shared/persistence_transactions.py`) was corrected first: it no
   longer depends on `job.child_statement_results()` (which doesn't
   exist on a real BigQuery job) and instead relies on
   `ASSERT @@row_count = N` embedded directly in each template's SQL,
   with the script's trailing `SELECT` as the only thing
   `execute_transaction()` reads back. Step 9 below is now expected to
   **succeed**, not fail — if it still fails, that's a genuine,
   actionable finding, not a predicted and accepted one.
7. **Corrected permissions documentation** — see "Required permissions"
   below; dataset creation may require `bigquery.datasets.create`,
   which Data Editor + Job User does not guarantee.
8. **Explicit dataset-cleanup clarification** — cleanup never deletes
   `loupe_platform_test` itself, only `spike_<run_id>_*` tables. See
   "Cleanup guarantee" below.

## Target

| | |
|---|---|
| Project | `ai-weekend-agent-501502` |
| Dataset | `loupe_platform_test` (created by this script only if it does not already exist — never `loupe_platform`) |
| Location | `US` (hard requirement — the script refuses any other value, and refuses an existing dataset in any other location) |

## Required permissions

This script needs two different kinds of access, and they don't
necessarily come from the same role:

1. **BigQuery Job User** (to run queries/scripts) + **BigQuery Data
   Editor** scoped to `loupe_platform_test` (to create/drop the
   spike's own tagged tables and run DML inside it). These two
   together are **not guaranteed** to include
   `bigquery.datasets.create` — dataset creation is a project-level
   (or higher) permission that dataset-scoped Data Editor does not
   grant in every IAM configuration.
2. Either:
   - **(a)** `loupe_platform_test` already exists in `US`, created
     ahead of time by someone with `bigquery.datasets.create` (a
     project Owner/Editor, or a custom role including that
     permission) — in which case the identity running this script
     only ever needs Job User + Data Editor scoped to that one
     dataset, **or**
   - **(b)** the identity running this script itself has
     `bigquery.datasets.create` (e.g. BigQuery Admin, or
     `roles/bigquery.dataEditor` granted at the **project** level
     rather than the dataset level), so `_ensure_dataset()` can create
     it.

If dataset creation fails for a permissions reason, the script lets
the error propagate with a printed hint pointing back at this section
— it does not catch and silently downgrade a permissions failure.

## Prerequisites

Either:
- A local terminal with `gcloud auth application-default login`
  completed for an identity with the permissions above on
  `ai-weekend-agent-501502`, or
- Google Cloud Shell with `gcloud config set project
  ai-weekend-agent-501502` already run (Cloud Shell provides an
  authenticated identity automatically).

Python dependencies: this repo's existing `google-cloud-bigquery`
dependency (already declared in `pyproject.toml`) is all that's needed
— no new packages.

Run everything from the repository root so `tools.phase6b_spike` and
`shared.persistence_transactions` are importable:

```bash
cd loupe
pip install -e .   # or: pip install google-cloud-bigquery>=3.42,<4.0
```

## Commands

### 1. Dry run (default — touches nothing)

```bash
python -m tools.phase6b_spike.live_transaction_spike \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US
```

Expected output: prints the target, a generated `run_id`, and the
planned step list. Exits 0. No BigQuery API calls are made in this
mode.

### 2. Execute the spike

```bash
python -m tools.phase6b_spike.live_transaction_spike \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US \
  --yes
```

This is the only command that touches BigQuery. It:

1. Confirms `loupe_platform_test` exists in `US` (refuses to continue
   if it exists in a different location), or creates it in `US` if
   absent.
2. Creates two tables, both prefixed `spike_<run_id>_` (e.g.
   `spike_a1b2c3d4e5_lock_rows`, `spike_a1b2c3d4e5_incidents_like`) —
   never touches any other table.
3. Runs seven verification steps (below) inside a `try/finally` block,
   printing `[OK]`/`[FAILED/UNEXPECTED]` for each plus diagnostic
   detail.
4. Cleans up its own tagged tables automatically at the end — this
   `finally` block runs even if a step above raised unexpectedly.
5. Confirms `loupe_platform` was never referenced.

### 3. Manual cleanup (only needed if automatic cleanup itself failed)

```bash
python -m tools.phase6b_spike.live_transaction_spike \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US \
  --cleanup-only --run-id <run_id printed by the run>
```

`--run-id` must match `^[0-9a-f]{10}$` exactly — anything else
(missing, wrong length, containing `%`/`*`/punctuation/non-hex
characters) is rejected before this script ever lists or drops a
single table. This command only drops tables whose name starts with
`spike_<run_id>_` — it cannot accidentally drop anything else, even if
pointed at a dataset with other content in it, and it never deletes
the dataset itself.

## What each step verifies

| # | Step (function in `live_transaction_spike.py`) | What it proves |
|---|---|---|
| 1, 3, 4, 5 | `spike_successful_commit_with_assert_and_row_count` | A multi-statement `BEGIN TRANSACTION ... COMMIT` script commits; `ASSERT @@row_count = 1` after each DML statement is the per-statement affected-row signal; the script's final `SELECT` is the structured result `job.result()` returns. |
| 2 | `spike_forced_failure_and_rollback` | A failing `ASSERT` aborts the whole script before `COMMIT` — neither the lock-row update nor the incident insert persists. |
| 6 | `spike_child_job_enumeration_diagnostics_only` | Whether `client.list_jobs(parent_job=job)` is a usable diagnostic view of a script's child jobs — explicitly informational only, never the correctness mechanism (that's ASSERT/`@@row_count`). |
| 7, 8 | `spike_concurrent_lock_row_mutation` | Two threads submit transactions touching the same lock row as close together as possible, run for up to 3 bounded attempts, and are classified `confirmed`/`inconclusive`/`failed` per the honest three-way rule (see revision note #4 above) — feeding `shared.persistence_transactions._is_retryable()`'s classification. |
| 9 | `spike_execute_transaction_against_real_bigquery` | Runs `shared.persistence_transactions.execute_transaction()` itself (not a reimplementation) against real BigQuery via a spike-only registered template that embeds its own `ASSERT @@row_count`, per the corrected `StatementTemplate` contract. Expected to succeed. |
| 10a | `spike_sequential_retry_idempotency` | Runs the same idempotent `INSERT ... WHERE NOT EXISTS` twice, sequentially, with the same deterministic ID (simulating a client retry after an ambiguous failure) and confirms exactly one row exists afterward. |
| 10b | `spike_concurrent_duplicate_insert` | **The critical proof.** Two workers concurrently attempt the SAME deterministic incident ID using the intended lock-row transaction pattern; asserts exactly one logical row exists afterward; captures whether the loser aborted and whether retrying it resolves to an identical-payload no-op success. |
| — | `verify_production_untouched` | Confirms `loupe_platform` was never referenced during the run. |

## Estimated resources / cost

- Two small tables (a handful of rows each) in `loupe_platform_test`,
  created and dropped within the same run.
- Roughly 15-20 small query jobs (a few bytes to a few KB scanned
  each) plus one deliberately slow `SELECT COUNT(*) FROM
  UNNEST(GENERATE_ARRAY(1, 3000000))` per concurrency-test worker
  (used in both the lock-row test and, indirectly, the duplicate-insert
  test — up to 3 bounded attempts each), which scans no table data
  (pure compute, not billed by bytes-scanned) but does consume a few
  seconds of slot time per attempt.
- No BigQuery reservation/slot purchase required — this runs entirely
  on-demand pricing, and total cost is expected to be negligible (well
  under $0.01) since no real table data is scanned at any point.

## Cleanup guarantee

Every table this script creates is named `spike_<run_id>_...`. Both
the automatic `finally`-block cleanup and the standalone
`--cleanup-only` command only ever issue `DROP TABLE IF EXISTS` for
names matching that exact prefix inside the configured `--dataset` —
never a bare `DROP TABLE` by guessed name, and never any statement
referencing `loupe_platform`.

**The dataset itself (`loupe_platform_test`) is never deleted by this
script, under any code path.** It may have pre-existed, or may hold
another concurrent spike run's tables; this script has no way to know
it's safe to remove and never attempts to. `verify_production_untouched()`
runs at the end of every full spike execution and prints whether
`loupe_platform` exists at all in the target project, as a final
sanity check.

## After you run it

Paste the full console output back for review. From that, the Phase
6B report will:

1. Confirm whether every step behaved as this revised design expects
   (in particular, that step 9 now succeeds rather than the previously
   predicted failure).
2. Record the actual exception type/module BigQuery raised for the
   losing side of the concurrent lock-row test and the concurrent
   duplicate-insert test, and lock in `_is_retryable()`'s
   classification against it.
3. Note any quota/rate-limit messages observed.
4. Only then proceed to Phase 6B's real business-transaction templates
   (metric certification, incident transition, baseline promotion),
   built on the corrected ASSERT/`@@row_count` pattern this spike
   validates.
