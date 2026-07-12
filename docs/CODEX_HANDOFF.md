# Codex Handoff

Updated: 2026-07-12

## Objective

Complete and present the Loupe AI Analytics Platform Family as a production-minded portfolio platform:

1. Data Quality Triage deterministically detects and persists an incident.
2. Metric Governance incorporates that incident into trust scoring with evidence.
3. Loupe warns the user before explaining affected metrics.
4. Resolving the incident clears the Governance and Loupe degradation.

Avoid speculative high-scale infrastructure. Prioritize a reliable, visible end-to-end demonstration.

## Repository

`/Users/jovannesaldierna/Projects/loupe`

Important: this directory is not currently initialized as a Git repository. Do not assume `git status` or rollback history exists. Ask before initializing Git or creating a remote.

The original source repositories are read-only migration sources and must remain untouched.

## Completed backend

- Monorepo structure under `apps/`, `shared/`, `tests/`, and `docs/`
- Shared BigQuery query gateway and safety controls
- Metric definitions and immutable version contracts
- Deterministic trust scoring
- Incident models, lifecycle rules, source health, audit contracts, and schema baselines
- Transactional persistence for incidents, transition history, audit events, metric versions, and baseline promotion
- Metric Governance migrated from fictional ARR runtime data to real e-commerce definitions
- Data Quality Triage migrated without sample-data runtime fallback
- Loupe migrated with real structured BigQuery results and bounded LLM narration
- Explicit persistence configuration and honest unavailable states
- Three-app credential-free cross-app workflow tests

## Final corrections completed in Codex

Claude stopped midway through the final lifecycle/dataset-safety correction. Codex preserved its valid work and completed the remainder:

- Triage UI lifecycle actions use `shared.incident_persistence.record_incident_transition()` in persisted mode.
- Expected status and row version protect transitions.
- UI state changes only after successful persistence.
- Non-persisted mode is labeled session-only.
- The live integration script uses the same lifecycle service as the real UI.
- Transactional audit writes accept explicit `PlatformConfig`.
- Schema-baseline reads/promotions accept explicit `PlatformConfig`.
- `apps/data_quality_triage/persistence.py` passes config through incident, audit, and baseline operations.
- Triage `main.py` passes validated persistence config into baseline reads.
- Stale “not yet persisted” UI text was removed.
- Raw persistence exception details are no longer displayed by lifecycle UI errors.
- Dataset-routing regression tests cover imports-before-config and reject production fallback.

## Verification

- Last full credential-free suite before the live-compatibility corrections: `869 passed, 1 skipped`
- Timestamp/numeric transaction-binding focused suite: `74 passed`
- Lifecycle/live-validation focused suite: `35 passed`
- Full command:

```bash
cd /Users/jovannesaldierna/Projects/loupe
.venv/bin/python -m pytest -q
```

The skipped test was intentionally live-only. The live validation described below was subsequently executed by the user from an authenticated terminal.

The local `.venv` now has the editable project and development dependencies installed successfully.

## Live BigQuery validation: PASSED

Target:

- Project: `ai-weekend-agent-501502`
- Dataset: `loupe_platform_test`
- Location: `US`
- Actor: `jovannesaldierna`

The user ran the bootstrap and integration scripts from an authenticated Mac Terminal using Application Default Credentials, never a downloaded JSON key.

Bootstrap completed successfully:

- All nine schemas were created/current in `loupe_platform_test`.
- The four fixed `write_locks` domain rows were seeded idempotently.
- Five real metric definitions were seeded as `pending_validation`.
- Nothing was automatically certified.

The live run exposed and Codex corrected three real BigQuery compatibility gaps that the fake client did not enforce:

- BigQuery does not permit `NOT NULL` on ARRAY columns.
- Bootstrap originally declared but did not seed the fixed `write_locks` rows.
- ISO timestamp and nullable numeric transaction parameters required explicit `TIMESTAMP`/`FLOAT64` binding.

The final live integration run ID was `10b3fd502d`. All six proof steps passed:

1. Triage persisted one deterministic incident.
2. Governance found the incident, reported critical source health, and penalized trust scoring.
3. Loupe reported critical source health and named the affected table.
4. Triage followed the valid persisted lifecycle `open -> acknowledged -> investigating -> resolved`.
5. Governance and Loupe returned to healthy after resolution, while catalog status remained `pending_validation` in both applications.
6. The audit event and exactly three transition-history rows existed.

The script then deleted every run-tagged incident, transition, and audit row. Cleanup passed. No manual cleanup is pending. The seeded pending-validation catalog definitions intentionally remain in the test dataset.

Backend persistence and the cross-application portfolio story are therefore validated against real BigQuery.

Commands retained only for reproducibility:

```bash
cd /Users/jovannesaldierna/Projects/loupe
source .venv/bin/activate

python -m tools.phase6e_ops.bootstrap_test_dataset \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US \
  --actor jovannesaldierna \
  --yes
```

```bash
python -m tools.phase6e_ops.live_integration_validation \
  --project ai-weekend-agent-501502 \
  --dataset loupe_platform_test \
  --location US \
  --actor jovannesaldierna \
  --yes
```

Never run these commands against `loupe_platform` production.

## Known deferred limitation

The BigQuery concurrent-conflict message classifier is intentionally conservative. A genuine conflict with unrecognized wording may surface safely instead of retrying. Further matcher work is deferred because extreme contention is outside the realistic portfolio workload and the verified behavior is data-safe.

## Locked UI direction

The old universal dark Streamlit theme is superseded.

Binding UI reference pack:

- `docs/ui-reference/approved-ui-mockup.html`
- `docs/ui-reference/UI_PRODUCT_DIRECTION.md`
- `docs/ui-reference/VISUAL_ACCEPTANCE.md`
- `docs/ui-reference/loupe-reference.png`
- `docs/ui-reference/governance-reference.png`
- `docs/ui-reference/triage-reference.png`

Approved mockup SHA-256:

`33cf1f77f30ef12797b3c499ab52c1de2d9b6f256b78a7a63b4b407bb9dc103c`

The toggle in the reference HTML is mockup-only. The final product must be three separately deployed applications and URLs:

- Loupe: bright Signal Intelligence workspace
- Governance: Violet Ledger definition and SQL-review workspace
- Triage: dark Midnight Command reliability console

The exact mockup is locked. Do not reinterpret, simplify, or redesign it without explicit approval.

Frontend implementation may now begin. Intended direction: Next.js + TypeScript + shadcn/ui over a typed FastAPI boundary, reusing the existing framework-independent Python services.

## Next-chat instructions

1. Read this file first.
2. Do not reread the full repository unless a specific failure requires it.
3. Treat backend persistence and live BigQuery integration as complete. Do not repeat transaction, schema, concurrency, or architecture analysis unless a concrete frontend/API integration failure requires it.
4. Begin the dedicated UI phase with the smallest useful slice: confirm Git protection with the user, define the typed FastAPI boundary, then implement the exact locked design as three separate Next.js applications/URLs.
5. Preserve the existing Python services as the source of domain behavior; the frontend is a delivery-layer replacement, not a rewrite of analytical logic.
6. Maintain the binding end-to-end story: Triage persists an incident, Governance reflects its trust impact, and Loupe warns about affected metrics.
7. Keep usage efficient: inspect only directly relevant files, implement one vertical slice at a time, run focused tests during development, and run one full suite only at a meaningful checkpoint.
