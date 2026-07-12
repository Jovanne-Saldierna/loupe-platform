# Production cutover checklist (prepared, NOT executed)

> Final credential-free verification: 869 passed, with one intentionally
> skipped live-only test. UI-facing Triage lifecycle actions now use the same
> transactional transition service as the live integration flow. Guarded
> operator calls pass an explicit `PlatformConfig`, so test-dataset incident,
> audit, and schema-baseline operations cannot fall back to an import-time
> production target.

Status: **prepared only**. Nothing in this checklist has been run against
`loupe_platform` (production). Per Phase 6E's explicit scope boundary, this
phase stops before any production bootstrap, seeding, deployment, or
certification. This document exists so a human operator can execute the
cutover deliberately, step by step, once ready -- not so an agent or script
can run it automatically.

Every command below targets `--dataset loupe_platform` (no `test` substring)
-- the exact target every guarded script in `tools/phase6e_ops/` and
`tools/phase6b_spike/` refuses outright (`require_safe_test_dataset()`/
`_require_safe_target()` both hard-reject `dataset == "loupe_platform"`).
That refusal is intentional and must not be worked around: production
bootstrap uses `shared/schema_management.py`'s and
`shared/metric_catalog_persistence.py`'s own CLIs directly, which have no
"must contain test" guard because they are the actual production tool --
safety here comes from the explicit `--yes` flag and human judgment, not a
name-pattern check.

## 1. Production `loupe_platform` bootstrap

```bash
python -m shared.schema_management bootstrap \
    --project ai-weekend-agent-501502 --dataset loupe_platform --location US
    # dry run first (no --yes) -- confirm the printed target is correct

python -m shared.schema_management bootstrap \
    --project ai-weekend-agent-501502 --dataset loupe_platform --location US --yes
```

Applies the nine-table schema (idempotent -- safe even if partially applied
before). Confirm afterward:

```bash
python -m shared.schema_management validate \
    --project ai-weekend-agent-501502 --dataset loupe_platform --location US
```

Expect `SchemaValidationResult(ok=True, applied_version=1, expected_version=1, safe_error=None)`.

## 2. Pending-validation catalog seed

```bash
python -m shared.metric_catalog_persistence seed \
    --project ai-weekend-agent-501502 --location US --actor <operator-identity>
    # dry run first (no --yes)

python -m shared.metric_catalog_persistence seed \
    --project ai-weekend-agent-501502 --location US --actor <operator-identity> --yes
```

**Note:** this CLI has no `--dataset` flag of its own -- it reads
`LOUPE_DATASET` from the process environment (default `loupe_platform`,
which is exactly what production cutover wants). Confirm `LOUPE_DATASET` is
unset (or explicitly `loupe_platform`) in the shell before running this --
do not run it in a shell that still has `LOUPE_DATASET=loupe_platform_test`
exported from a prior test session.

Seeds all five definitions as `certification_status="pending_validation"`.
`seed_metric_definition()` refuses outright (raises `ValueError`, before any
BigQuery call) if a definition's `certification_status` is anything other
than `pending_validation` -- this CLI structurally cannot seed a certified
metric.

## 3. Set `LOUPE_PERSISTENCE_MODE=persisted`

In the deployment environment for all three Streamlit apps (not a local
shell -- wherever `apps/*/main.py` actually runs):

```bash
LOUPE_PERSISTENCE_MODE=persisted
LOUPE_BQ_PROJECT=ai-weekend-agent-501502
LOUPE_DATASET=loupe_platform
LOUPE_BQ_LOCATION=US
LOUPE_STRICT_SEPARATION_OF_DUTIES=false   # confirm this deployment's intended policy explicitly
```

Restart all three apps after setting these. Each app's `main.py` calls
`shared.persistence_bootstrap.resolve_persistence()` at the start of every
`build_state()` run -- this is read-only (validates dataset reachability and
schema version) and will report an honest `PersistenceResolution(available=False, ...)`
if steps 1-2 above were skipped or failed, never a fabricated healthy state.

## 4. Application identity permissions

Each running identity (a service account attached to the deployment, or an
operator's ADC for manual verification) needs:

- **BigQuery Job User** (to run queries/scripts) at the project level.
- **BigQuery Data Editor** scoped to `loupe_platform` (to read/write the nine
  persistence tables via `run_query()`/`execute_transaction()`).
- **No broader grant.** In particular, no identity used by the running
  applications needs `bigquery.datasets.create` -- the dataset already
  exists after step 1; only the human/service-account performing the
  one-time bootstrap needs that (or the dataset must be pre-created by
  someone who does, per `docs/PHASE_6B_HANDOFF.md`'s "Required IAM" note).
- **Per docs/persistence.md section 7**: no identity is ever represented as
  a downloaded service-account JSON key this codebase reads. Application
  Default Credentials or a workload identity binding only.

## 5. Verification queries

Run these (read-only) against production after cutover, before declaring it
complete:

```sql
-- Schema is at the expected version
SELECT * FROM `loupe_platform.schema_migrations` ORDER BY version;

-- All five definitions are seeded, all pending_validation, none certified
SELECT name, current_version, certification_status
FROM `loupe_platform.metric_catalog`
ORDER BY name;
-- Expect exactly 5 rows, every certification_status = 'pending_validation'

-- No incidents exist yet (a fresh production cutover should start clean)
SELECT COUNT(*) AS n FROM `loupe_platform.incidents`;
-- Expect 0, unless this is a deliberate re-cutover with pre-existing data

-- write_locks has exactly the four fixed domain rows
SELECT lock_domain FROM `loupe_platform.write_locks` ORDER BY lock_domain;
-- Expect exactly: audit_events, incidents, metric_catalog, schema_baselines
```

Then, from an app instance actually running with `LOUPE_PERSISTENCE_MODE=persisted`:
confirm Data Quality Triage's build_state() reports `persistence_available=True`,
Metric Governance's Catalog page shows all five metrics as `pending_validation`
with no `catalog_unavailable` banner, and Loupe answers a metric question with
a certification note that says "pending_validation" for every metric it
references (never "certified").

## 6. Rollback to an explicit unavailable state

If cutover needs to be reverted (a verification query above fails, or an
application misbehaves under real persisted data):

1. Set `LOUPE_PERSISTENCE_MODE=constants` in the deployment environment and
   restart all three apps. This is the fast, always-safe rollback -- every
   app immediately stops reading/writing `loupe_platform` and falls back to
   the explicit, pre-cutover demo configuration (`shared/metric_catalog.py`'s
   in-memory registry), with no data loss to the persisted tables (nothing
   is deleted, nothing is migrated backward).
2. Do **not** attempt to `DROP TABLE`/`TRUNCATE` anything in
   `loupe_platform` as part of rollback -- `shared/schema_management.py`
   deliberately exposes no destructive path (see that module's docstring,
   "Forward-only migrations"). If the persisted data itself is genuinely
   bad and needs to be cleared, that is a separate, manual, out-of-band
   operation requiring its own explicit review -- never a scripted part of
   this rollback.
3. Confirm rollback succeeded the same way `shared.persistence_bootstrap.resolve_persistence()`
   would report it: each app should now show its honest `constants`-mode
   state (no `catalog_unavailable` banner, because `constants` mode never
   attempts a persisted read in the first place -- it's a different code
   path, not a degraded one).

## 7. Confirmation: no real metric becomes certified automatically

Structural guarantees already in place, verified by the credential-free test
suite (`tests/test_persistence_boundary.py`), that make this impossible by
construction, not merely by operator discipline:

- `certify_metric_definition()`/`certify_definition()` are referenced from
  exactly two files in the entire `apps/` tree:
  `apps/metric_governance/persistence.py` (the thin pass-through wrapper)
  and `apps/metric_governance/ui.py` (the Catalog page's "Certify" form
  handler) -- enforced by
  `test_certify_metric_definition_is_only_referenced_from_the_allowed_governance_paths`.
- No app's `main.py` (where every `build_state()` lives -- the automatic,
  per-request assembly path) may reference certification at all -- enforced
  by `test_no_build_state_function_calls_certify_metric_definition`.
- `seed_metric_definition()`/`seed_current_catalog()` (the bootstrap seed
  path used in step 2 above) structurally refuse to seed anything whose
  `certification_status` is not `pending_validation` -- there is no
  seeding call shape that produces a certified metric.
- Neither this checklist, `tools/phase6e_ops/bootstrap_test_dataset.py`, nor
  `tools/phase6e_ops/live_integration_validation.py` imports or calls
  `certify_metric_definition()`/`certify_definition()` anywhere.

Certification can only happen through one path: a human operator filling out
Metric Governance's Catalog page "Certify" form (`reviewer`,
`validation_evidence`, `reviewed_at`, `change_reason` all required, entered
by that human, before the certification call is even constructed).
