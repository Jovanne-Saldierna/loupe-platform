# Persistence: configuration, operator commands, and troubleshooting

This document covers everything an operator needs to run Loupe's persistence
layer locally, against the isolated test dataset, or (eventually) in
production. It does not repeat the architectural history already recorded in
`docs/PHASE_6B_HANDOFF.md` -- read that first if you need to know *why* the
transaction mechanism works the way it does.

## 1. Local configuration

Three applications (`apps/data_quality_triage`, `apps/metric_governance`,
`apps/loupe_agent`) share one persistence layer under `shared/`. Nothing in
`shared/` imports Streamlit; every app reads the same environment variables
via `shared.config.load_platform_config()` and `shared.config.load_persistence_mode()`.

| Variable | Default | Purpose |
|---|---|---|
| `LOUPE_PERSISTENCE_MODE` | `constants` | `constants` = read `shared/metric_catalog.py`'s in-memory registry, never touch BigQuery for the metric catalog. `persisted` = read/write through `shared/*_persistence.py` against real tables. Never chosen automatically as an error fallback -- an app configured `persisted` that cannot reach storage reports an honest "unavailable" state, it does not silently drop back to `constants`. |
| `LOUPE_BQ_PROJECT` / `GOOGLE_CLOUD_PROJECT` | *(required for any real BigQuery access)* | Which GCP project to bill/query against. `LOUPE_BQ_PROJECT` is checked first so persistence can target a project distinct from wherever query jobs happen to run. |
| `LOUPE_DATASET` | `loupe_platform` | Which dataset every persistence table lives under. **This is the dataset-isolation switch** -- set it to `loupe_platform_test` for anything other than a real production run (see "Persistence modes and dataset isolation" below). |
| `LOUPE_BQ_LOCATION` | `US` | Must stay `US` -- `bigquery-public-data.thelook_ecommerce` is hosted in the US multi-region, and a `loupe_platform*` dataset in any other location cannot be joined against it in one query. |
| `LOUPE_STRICT_SEPARATION_OF_DUTIES` | `false` | Governs whether Metric Governance's certification action refuses `reviewer == created_by`. Defaults to `false` for this portfolio deployment (a single reviewer may certify their own authored content); set to `true`/`1`/`yes` for a stricter policy. `created_by` and `reviewer` are always recorded as distinct fields either way -- this only controls whether the *same identity* may occupy both roles. |
| `ANTHROPIC_API_KEY` | *(required for LLM narration)* | Read via `os.getenv` first, then `st.secrets` -- each app's `chat.py` degrades to a fixed "not configured" message when absent, never a fabricated narration. Unrelated to persistence, listed here because it's the other required-for-full-functionality secret. |

Local development normally runs with no persistence variables set at all
(`LOUPE_PERSISTENCE_MODE=constants` by default) -- every app, UI, and test
works fully credential-free in that mode. Set `LOUPE_PERSISTENCE_MODE=persisted`
plus the BigQuery variables above only once you have a bootstrapped dataset to
point at (see section 2).

## 2. Test-dataset bootstrap and seed

**Target:** project `ai-weekend-agent-501502`, dataset `loupe_platform_test`,
location `US`. This is a real BigQuery dataset, isolated from production
`loupe_platform` by name only (there is no separate project) -- every command
below validates that `--dataset` contains `test` and is never literally
`loupe_platform` before touching anything.

Requires a real, authenticated Google Cloud identity: a local terminal with
`gcloud auth application-default login` completed, or Google Cloud Shell.
This agent cannot and does not run these commands -- there is no ADC, no
`gcloud`, and no service-account key material in the environment this repo
was prepared in, and nothing in this repository ever requests, generates,
uploads, or reads one.

```bash
# Dry run -- prints the plan, touches nothing.
python -m tools.phase6e_ops.bootstrap_test_dataset \
    --project ai-weekend-agent-501502 --dataset loupe_platform_test \
    --location US --actor <your-identity>

# Actually execute.
python -m tools.phase6e_ops.bootstrap_test_dataset \
    --project ai-weekend-agent-501502 --dataset loupe_platform_test \
    --location US --actor <your-identity> --yes
```

This applies pending schema migrations (idempotent -- `CREATE TABLE IF NOT
EXISTS` / `ADD COLUMN IF NOT EXISTS` only, safe to re-run) and then seeds the
five `pending_validation` metric definitions (`revenue`, `margin`,
`return_rate`, `margin_leakage`, `channel_mix`) from
`shared/metric_catalog.py`'s in-memory registry (also idempotent -- seeding
identical content twice is a no-op; seeding genuinely different content under
an existing name/version raises `PayloadConflictError` rather than silently
overwriting anything). **It never certifies anything** -- every seeded
definition stays `pending_validation` until a human certifies it through
Metric Governance's UI. It never runs automatically; nothing in `shared/` or
`apps/` imports this module.

## 3. Live integration command

The one guarded, opt-in proof that the wired Triage → Governance → Loupe
workflow actually works against real BigQuery. Requires the test dataset to
already be bootstrapped and seeded (section 2).

```bash
python -m tools.phase6e_ops.live_integration_validation \
    --project ai-weekend-agent-501502 --dataset loupe_platform_test \
    --location US --actor <your-identity> --yes
```

Proves, in order, against a single deterministic, run-tagged incident on
`bigquery-public-data.thelook_ecommerce.orders`:

1. Triage persists the incident (`apps.data_quality_triage.persistence.persist_confirmed_incidents`).
2. Governance's `source_health_for_definition()`/`trust_score_for_definition()` see it, report `orders` as `critical`, and penalize the `revenue` metric's trust score.
3. Loupe's `source_health.get_source_health()`/`summarize()` report the same critical status and a warning naming `orders`.
4. Resolving the incident (via `shared.incident_persistence.record_incident_transition()`) clears both apps' degradation.
5. `revenue`'s persisted catalog status is `pending_validation` in both Governance's read and Loupe's certification note.
6. An `incident_created` audit event and a `resolved` `incident_transitions` row both exist for the tagged incident.

Every row this run creates or touches is tagged with a generated `run_id`
(printed at the start of the run) and deleted in a `finally` block regardless
of outcome. If an interrupted run leaves rows behind, recover manually:

```bash
python -m tools.phase6e_ops.live_integration_validation \
    --project ai-weekend-agent-501502 --dataset loupe_platform_test \
    --location US --cleanup-only --run-id <run_id printed by the run>
```

## 4. Cleanup

- **Live integration rows**: cleaned up automatically by
  `live_integration_validation.py`'s own `finally` block; use
  `--cleanup-only --run-id <id>` for manual recovery (above). Only ever
  deletes rows whose `incident_id`/`subject` contains
  `phase6e_integration_<run_id>` from `incidents`, `incident_transitions`,
  and `audit_events` -- it never touches `metric_catalog`, `metric_versions`,
  or `write_locks`.
- **Seeded catalog rows**: not per-run, not cleaned up by the integration
  script. If `loupe_platform_test` needs a full reset, drop and recreate the
  dataset manually (outside any script in this repository) and re-run
  bootstrap + seed.
- **Live spike rows** (`tools/phase6b_spike/`): a separate, older guarded
  script with its own `spike_<run_id>_`-prefixed tables and its own
  `--cleanup-only --run-id` flag -- see `tools/phase6b_spike/README.md`.

## 5. Persistence modes and dataset isolation

`LOUPE_PERSISTENCE_MODE` selects the backing store (`constants` vs.
`persisted`); `LOUPE_DATASET` selects *which* dataset `persisted` mode reads
and writes. Every `shared/*_persistence.py` module (plus `shared/data_service.py`
and `shared/audit.py`) resolves its table-name constants from `LOUPE_DATASET`
once, at first import in a process -- this is a Phase 6E correction: earlier
phases hardcoded `loupe_platform.*` as a literal, which meant setting
`LOUPE_DATASET=loupe_platform_test` had no effect on where persisted reads
and writes actually landed. Every operator script in `tools/phase6e_ops/`
sets `LOUPE_DATASET` (after validating it) *before* importing any of those
modules, so this is now a real, load-bearing isolation boundary, not just a
naming convention.

Practical implication: always run a fresh `python -m ...` process per
target. Do not import `shared.incident_persistence` (or any sibling module)
in a long-lived process and then try to switch `LOUPE_DATASET` mid-process --
the table constants are frozen at first import and will not update.

## 6. Production environment variables

Not executed in this phase -- see section 7's cutover checklist for the full
sequence. The variables a production deployment sets:

```bash
LOUPE_PERSISTENCE_MODE=persisted
LOUPE_BQ_PROJECT=ai-weekend-agent-501502
LOUPE_DATASET=loupe_platform
LOUPE_BQ_LOCATION=US
LOUPE_STRICT_SEPARATION_OF_DUTIES=false   # or true, per deployment's review staffing
ANTHROPIC_API_KEY=<narration key>
```

## 7. No-JSON-key policy

No file, script, environment variable, or piece of documentation in this
repository requests, generates, uploads, reads, or references a
service-account JSON key. Every operator command in `tools/phase6b_spike/`
and `tools/phase6e_ops/` authenticates via Application Default Credentials
(`gcloud auth application-default login` for a local terminal, or the
identity Cloud Shell already provides) -- `google.cloud.bigquery.Client(...)`
resolves credentials entirely inside the `google-cloud-bigquery` library,
never through code in this repository. This is unchanged from Phase 6B (see
`docs/PHASE_6B_HANDOFF.md`, "Authentication and credential rules") and
applies identically to production: the running identity (a service account
attached to the deployment, or a human operator's ADC) must never be
represented as a downloaded key file this codebase touches.

## 8. Three application identities

Each of the three Streamlit apps writes audit events and (once `persisted`
mode is live) incidents/certifications under its own `actor` identity, never
a shared generic one:

| App | Typical actor value | What it writes |
|---|---|---|
| Data Quality Triage | `data_quality_triage.checks` (default in `build_audit_event_for_incident`) or the operating user's identity | Incidents, `incident_created` audit events, schema-baseline promotions |
| Metric Governance | The human reviewer's identity, supplied in the Catalog page's "Certify" form | Metric certifications (`metric_versions` rows, `metric_catalog` pointer updates, `metric_certified` audit events) |
| Loupe | *(read-only in this phase)* | Nothing -- Loupe only reads persisted metric definitions and source health; it never writes |

Every write records `actor`/`created_by`/`reviewer` as real, distinct fields
-- never conflated, never defaulted to a shared "system" identity that would
make it impossible to tell which application or human took an action.

## 9. Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| An app shows "catalog unavailable" in `persisted` mode | Dataset not bootstrapped/seeded yet, or the running identity lacks read access | Run `bootstrap_test_dataset.py` (or the production bootstrap, once approved) against the correct dataset; confirm `LOUPE_DATASET` matches what was bootstrapped |
| An app silently stays on `constants` data when you expected `persisted` | `LOUPE_PERSISTENCE_MODE` not set, or set in the wrong process/shell | `constants` is the explicit default -- it is never chosen automatically as an error fallback, so this is almost always a missing/unset env var, not a persistence failure. Confirm with `python -c "from shared.config import load_persistence_mode; print(load_persistence_mode())"` in the exact process/shell the app runs in |
| `live_integration_validation.py` fails at step 5 ("persisted catalog is readable") | `loupe_platform_test` was never bootstrapped/seeded | Run `bootstrap_test_dataset.py --yes` first |
| A persistence write raises `PayloadConflictError` | The same deterministic ID (incident_id/event_id/name+version) was submitted with genuinely different content than what's already persisted | This is a real conflict, not a bug -- re-derive the ID or resolve the conflicting content manually; never silently overwrite |
| A persistence write raises `ConcurrentModificationError` | Sustained real contention exhausted the retry budget (rare at this platform's volume) | Re-fetch whatever state the write was based on and retry the whole operation from scratch, not just resubmit the same parameters |
| An operator flow appears to target the wrong dataset | A new persistence call failed to propagate its explicit `PlatformConfig` | Guarded operator flows pass config through incident, audit, and schema-baseline operations and reject SQL outside the validated dataset. Treat a new write path without explicit config propagation as a defect. |
| Governance/Loupe still show a table as degraded after resolving an incident | Another *different* active incident still exists for that table, or the resolution didn't actually commit | Check `list_active_incidents_for_table()`/the `incidents` table directly for the specific `dataset`/`table_id` in question |

## 10. The end-to-end portfolio demo workflow

The single demonstrable story this platform is built to tell, in order:

1. **Data Quality Triage** runs its deterministic checks against
   `bigquery-public-data.thelook_ecommerce`, confirms a real finding, and
   persists it as an `Incident` plus an `incident_created` audit event.
2. **Metric Governance** opens the Catalog page for a metric whose
   `approved_source_tables` includes the affected table. Its trust score
   visibly drops, and the UI shows exactly which incident and which table
   produced that drop -- never a bare number with no evidence trail.
3. **Loupe** is asked a question that would use that same metric/table. It
   surfaces a source-health warning *before* narrating any conclusion, and
   the narration itself only ever references structured query results,
   metric metadata, reporting scope, and source health -- never a fabricated
   number or a silently-upgraded certification claim.
4. Someone resolves the incident in Triage. Governance's trust score and
   Loupe's warning both clear on their next read -- no manual sync step, no
   stale cache, because both read the same `incidents` table live.
5. A human reviewer certifies the metric in Metric Governance's UI
   (recording `reviewer`, `validation_evidence`, `reviewed_at`, and
   `change_reason` up front) -- the only path anywhere in this codebase that
   can move a metric out of `pending_validation`.

`tools/phase6e_ops/live_integration_validation.py` proves steps 1, 2 (via
Governance's persistence functions directly), 3 (via Loupe's persistence
functions directly), 4, and the `pending_validation` half of step 5 (never
certifying) against real BigQuery. Steps 2/3's actual Streamlit UI rendering
and step 5's human certification action are demonstrated by running the
three apps locally, not by this script -- see `docs/architecture.md` and
each app's own `docs/*.md` for UI-level detail.
