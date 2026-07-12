# Phase 5 Handoff — Loupe AI Analytics Platform Family

Status: Phase 4 approved. Phase 5 (Loupe E-Commerce Agent migration) has **not** started. This document is the handoff for whoever (or whichever session) picks up Phase 5, since the session that completed Phase 4 is near its usage limit.

Monorepo root: `/Users/jovannesaldierna/Projects/loupe`

## 1. Final architecture and module ownership

Operating rule for the whole platform: AI is not the source of truth. BigQuery results, certified metric definitions, deterministic quality checks, parsed SQL, and audit logs are authoritative. An LLM may explain those facts; it must never invent, redefine, or independently decide them.

```text
Business question or SQL
          |
          v
Metric definitions + SQL review + source health
          |
          v
Parameterized BigQuery query or deterministic check
          |
          v
Structured evidence, findings, and trust score
          |
          v
Plain-English explanation and recommended action
          |
          v
Audit event
```

### `shared/` — cross-app contracts and the one BigQuery gateway

- `shared/data_service.py` — the **only** module in the monorepo allowed to construct a `bigquery.Client` or execute SQL/metadata calls ("one gateway" principle). Owns `run_query()` (parameterized, read-only enforced via sqlglot, bytes-billed ceiling, timeout), `get_table_metadata()` (row count, modified time, columns, and now `column_types`), `list_tables()`, incident CRUD (`get_incident`, `list_active_incidents_for_table`, `apply_incident_transition`), and `derive_source_health()`.
- `shared/models.py` — the cross-app dataclasses: `Incident`, `MetricDefinition`, `AuditEvent`, `SourceHealth`, `TrustScoreFactor`/`TrustScoreResult`, plus the `Severity` (high/medium/low), `IncidentStatus`, `SourceHealthStatus`, `CertificationStatus`, `TrustBand` literal vocabularies.
- `shared/incidents.py` — the incident status lifecycle (`ALLOWED_TRANSITIONS`, `ACTIVE_INCIDENT_STATUSES`, `is_active_status`), and the authoritative "detected vs. open" definition every app must follow.
- `shared/audit.py` — `build_event()` / `write_event()`; rejects secret-looking keys in `context`. `write_event()`'s actual persistence is contract-only (Phase 6).
- `shared/trust_scoring.py` — `compute_trust_score()`: deterministic, itemized, versioned. Two independent conditions force `band="do_not_rely"` regardless of the arithmetic score: `source_health.status == "critical"` or `high_severity_finding_count > 0`.
- `shared/metric_catalog.py` — the certified `MetricDefinition` catalog (currently 5 entries: `revenue`, `margin`, `return_rate`, `margin_leakage`, `channel_mix`) and `definitions_referencing_table()`, which normalizes any BigQuery identifier form (`project.dataset.table`, `dataset.table`, bare table, backtick-quoted) via `_normalize_table_identifier()` before matching.

### `apps/metric_governance/` — Definition layer (complete)

`models.py`, `definition_diff.py`, `review.py` (sqlglot-based SQL review, structured findings, no LLM decision-making), `remediation.py`, `explanations.py` (Claude narration of already-computed findings only), `chat.py`, `ui.py`, `main.py`.

### `apps/data_quality_triage/` — Reliability layer (complete)

`models.py` (`TableFinding`, `CheckDefinition`, `SchemaSnapshot`, `IncidentExplanation`, and the `CheckSeverity`/`CheckStatus` local vocabularies), `profiling.py` (`TableProfile` built from `shared.data_service` metadata only), `checks.py` (all deterministic checks, the Guardrails catalog, and the one severity-collapse + `TableFinding → Incident` promotion point), `anomaly_engine.py` (live ratio-query checks + query-exception isolation), `incident_lifecycle.py`, `remediation.py`, `explanations.py`, `chat.py`, `ui.py`, `main.py`.

### `apps/loupe_agent/` — Assistant layer (Phase 5 target, currently empty)

Only `apps/loupe_agent/__init__.py` and `tests/loupe_agent/__init__.py` exist. No behavior has been migrated yet. See §6 and §7.

### UI boundary (enforced in both completed apps, must hold for Loupe too)

`ui.py` renders state and calls service/app functions only — it must never call BigQuery directly or duplicate certified business logic. Direct warehouse access belongs exclusively to `shared/data_service.py`.

## 2. Completed work, Phases 1–4

- **Phase 0** — Architecture review; monorepo repo plan; old-path → new-path file mapping approved by the user before any migration began.
- **Phase 1** — Package skeleton (`apps/`, `shared/`, `tests/`, mirrored per-app test structure); shared contracts (`shared/models.py`) written first so both apps could depend on the same vocabulary; compatibility-import shims; a credential-hygiene sweep of the original Metric Governance Copilot and Data Quality Incident Triage Agent folders (no committed service-account keys found beyond a `.env.example` in Governance).
- **Phase 2** — Built `shared/data_service.py`, `shared/audit.py`, `shared/trust_scoring.py`, `shared/metric_catalog.py`, `shared/incidents.py`. Followed by a 7-item correction round (requested by the user before Phase 3 was allowed to start) that hardened query safety, audit-context secret rejection, and trust-scoring edge cases. Full item-by-item detail lives in the project conversation history, not reproduced here to avoid misquoting it.
- **Phase 3** — Built out `apps/metric_governance/` end to end (models, definition-diff, review, remediation, explanations, chat, UI, main) plus tests and fixtures. Followed by a correction pass on definition-diff semantics and source-health honesty (conditionally-approved-then-corrected, same pattern as Phase 4 below).
- **Phase 4** — Extended `shared/data_service.py` for table metadata (`TableMetadata.column_types`), then built out `apps/data_quality_triage/` end to end (models, profiling, checks, anomaly engine, incident lifecycle, remediation, explanations, chat, UI, main) plus tests and fixtures. Initial suite: 348 passed. Conditionally approved pending 4 correction items (§3). All 4 completed and verified; final suite: **385 passed, 0 failed**. Phase 4 approved by the user.

## 3. Corrections and decisions made (Phase 4 correction round — full detail, since this is the most recent and most relevant work)

1. **Schema drift + query exceptions** (previously-undocumented check categories, now implemented):
   - `checks.check_schema_drift(profile, baseline)` deterministically compares `TableProfile.column_types` against a caller-supplied `SchemaSnapshot`. Always returns a `TableFinding` (never `None`, unlike the other metadata checks): `not_evaluated` with no baseline, `pass` if unchanged, `warn` on additions only, `fail` on any removal/rename/type-change. Rename detection is a conservative name+type-match heuristic, reported as "renamed (candidate)", never asserted as certain; each column participates in at most one pairing.
   - Baseline **persistence** (capturing and storing a snapshot after each run) is explicitly deferred to Phase 6 — every run without a supplied baseline honestly reports `not_evaluated` rather than guessing.
   - `anomaly_engine.py` wraps every live check's `run_query()` call in `_safe_run_query()`. Any exception is classified deterministically by `_classify_exception()` (type name + safe keyword match, never an LLM) into `timeout` / `permission_denied` / `malformed_query` / `execution_failure`, and converted into a `status="error"` `TableFinding` via `_query_exception_finding()`. The raw exception message is **never** echoed into a finding — only the exception's type name and a fixed category label are ever surfaced, so credentials, bound parameter values, or embedded query text can't leak.
   - `evaluate_profiles()` gained an optional `schema_baselines: dict[str, SchemaSnapshot]` parameter (default `None`).
   - `GUARDRAILS_CATALOG` grew from 6 to 8 entries (added "Schema Drift" and "Query Exception"), now covering all six documented check categories.

2. **Severity mapping, end to end**: `CheckSeverity` (local, 4-level: low/medium/high/critical) collapses to `shared.models.Severity` (3-level: high/medium/low) at exactly one point — `checks.collapse_severity()`, called only inside `build_incident_from_finding()`. Proven with a new integration test (`tests/data_quality_triage/test_severity_trust_integration.py`) that chains: local `critical` finding → shared `high` incident → `derive_source_health()` returns `critical` → `compute_trust_score()` forces `do_not_rely`. A second test confirms there is no unreachable "critical incident" branch in `shared/trust_scoring.py`: its override is keyed on `SourceHealth.status == "critical"` (a distinct, correctly-computed enum), never on `Incident.severity` (which cannot be `"critical"` post-collapse). `checks.build_audit_event_for_incident()` retains the original local severity (e.g. `"critical"`) in `AuditEvent.context["local_severity"]`, distinct from `context["collapsed_severity"]`, so nothing is discarded by the collapse.

3. **Detected vs. open**: documented in `shared/incidents.py` and `checks.py`'s module docstrings — `"detected"` is reserved for a raw, unconfirmed monitoring signal; every finding this codebase promotes into an `Incident` already came from a completed deterministic check that breached its rule, so `build_incident_from_finding()` always starts incidents at `"open"`, never `"detected"`. Proven with an explicit test against `shared.incidents.ACTIVE_INCIDENT_STATUSES`.

4. **Table identifier normalization**: `shared/metric_catalog.py`'s `definitions_referencing_table()` now normalizes both the caller's `table_id` and the catalog's own stored `approved_source_tables` entries via `_normalize_table_identifier()` before comparing, so `project.dataset.table`, `dataset.table`, backtick-quoted forms, and the catalog's bare stored names all resolve to the same match. Covered by 5 dedicated tests in `tests/shared/test_metric_catalog.py`.

## 4. Current test command and result

```bash
cd /Users/jovannesaldierna/Projects/loupe
python -m pytest -q tests/
```

**385 passed, 0 failed** (1 unrelated `FutureWarning` from `google.api_core` about Python 3.10 end-of-life — not a test failure).

## 5. Remaining Phase 5 and Phase 6 work

### Phase 5 (next)

- Migrate the Loupe E-Commerce Agent into `apps/loupe_agent/`, following the same pattern as Governance and Triage: focused files per responsibility, no direct BigQuery access outside `shared/data_service.py`, certified metrics read from `shared/metric_catalog.py` rather than redefined inline, trust/source-health surfaced via `shared/trust_scoring.py` and `shared/data_service.derive_source_health()`, and any LLM narration grounded strictly in already-computed structured results (mirroring `apps/metric_governance/explanations.py` and `apps/data_quality_triage/explanations.py`).
- Read the full source files (not just signatures) before migrating — the source repo has not been read line-by-line yet, only scanned for `def`/`class`/import signatures to write this handoff.
- Re-architect (not line-for-line port) the LangChain/`ChatAnthropic` agent-tool wiring so Claude only explains structured evidence, per `docs/loupe-agent.md`'s evidence contract and response structure.
- Build out models, tests, and fixtures for the new app, same as Phases 3 and 4.
- Expect (based on the Phase 2/3/4 pattern) a conditional-approval correction round before Phase 5 is fully accepted — budget for it.
- Rerun the full suite and report pass/fail before requesting Phase 6 approval.

### Phase 6 (deferred throughout Phases 1–4, not started)

- Real persistence for `shared/audit.write_event()` and incident writes against a live `loupe_platform` BigQuery dataset — currently contract-shaped and only unit-tested against fakes.
- A schema-baseline snapshot storage/retrieval pipeline, so `checks.check_schema_drift()` can move beyond the default `not_evaluated` verdict.
- Production scheduling for Data Quality Triage checks (and likely Loupe agent refreshes) — everything currently runs synchronously, on demand, from Streamlit.
- Likely a production credential/deployment story beyond local Application Default Credentials.

## 6. Loupe source repository path

`/Users/jovannesaldierna/Projects/ecommerce-analytics-agent`

Top-level contents relevant to migration: `main.py`, `app.py`, `requirements.txt`, `README.md`, `.env` (contains real environment values — see §8), and a local `venv/` (not part of the source, not migrated).

## 7. Files that Phase 5 is expected to migrate

- **`main.py`** (~27K) — currently constructs its own `bigquery.Client` via `google.oauth2.service_account` directly (`get_bq_client()`), and defines the agent's tool functions: `get_category_metrics`, `get_company_benchmark`, `get_multi_category_comparison`, `get_state_metrics`, `get_multi_state_comparison`, `get_returns_leakage`, `get_channel_mix_trend`, `get_lever_baseline`, `simulate_scenario`, and `run_agent` (the LangChain/`ChatAnthropic` entry point). This needs to be split across new `apps/loupe_agent/` files (e.g. a models module, a metrics/query module built on `shared.data_service.run_query()`, and a `chat.py` for agent orchestration), with all direct BigQuery/service-account construction replaced by `shared.data_service` calls.
- **`app.py`** (~30K) — the Streamlit UI: `icon()`, `kpi_card()`, `section_label()`, `return_rate_pill()`, `parse_single_metrics()`, `parse_pipe_table()`, plus page rendering, imported from `main.py`. Maps to `apps/loupe_agent/ui.py` (+ `main.py` entry point), following the same "no direct warehouse calls in UI" rule already enforced elsewhere.
- **`requirements.txt`** — reconcile with the monorepo's `pyproject.toml` dependency set (this project already uses `langchain-anthropic`-equivalent tooling, `google-cloud-bigquery`, `streamlit`, `pandas`, `plotly`, `sqlglot`, `pytest`; check for anything Loupe-specific like `markdown`, `python-dotenv`).
- **`README.md`** — reference only; not migrated verbatim, but useful for recovering intent/context if `main.py`/`app.py` comments are thin.
- **`.env`** — **not migrated as a file.** See §8.

## 8. Compatibility and backup constraints

- **The three original source repositories must never be modified, moved, or written to**: `metric-governance-copilot`, `data-quality-incident-triage-agent`, and `ecommerce-analytics-agent`. All access to them is read-only inspection, for the purpose of understanding what to migrate. This applies to Phase 5 exactly as it applied to Phases 3 and 4.
- Do not modify, move, or commit real service-account JSON key files, in the monorepo or the original repos.
- Use Application Default Credentials (ADC) for all BigQuery access inside the monorepo — never construct a `bigquery.Client` from a service-account file, matching the pattern `shared.data_service.get_bigquery_client()` already establishes.
- `ecommerce-analytics-agent/.env` exists and contains real environment values. It has **not been read** in the course of this project, specifically to avoid any chance of echoing a secret value. Phase 5 should ask the user which environment variable *names* (not values) matter, rather than reading the file directly.
- Never echo secret or API-key values in any response, commit, log, or document — including this one.
- Each phase requires explicit user approval before the next begins. After every phase or correction round, the full test suite must be rerun and the pass/fail count reported before requesting the next approval — do not skip this step for Phase 5.
- No shortcuts: separate, focused files per responsibility; production-grade, testable code; explicit interfaces.

## 9. Metric certification requirements

Per `docs/contracts.md`:

- A metric is certified only when its name, business meaning, formula, grain, time behavior, source tables, filters, owner, freshness expectation, and version are all recorded and approved. A label alone (e.g. two different queries both called "revenue") is not a contract — one might be gross booked revenue, the other delivered net revenue, and both can be legitimate but not interchangeable.
- `shared.models.MetricDefinition` is the shape that carries this. Newly extracted or proposed definitions must start at `"proposed"` or `"pending_validation"` — never silently `"certified"`.
- `shared/metric_catalog.py` currently has 5 certified definitions: `revenue`, `margin`, `return_rate`, `margin_leakage`, `channel_mix`. **Loupe (Phase 5) must read these from the catalog, not redefine its own formulas inline** — the original `ecommerce-analytics-agent/main.py` almost certainly computes these ad hoc in SQL/Python and will need to be reconciled against the certified definitions, flagging any disagreement rather than silently picking one.
- Every metric-bearing response must state which certified version it is (e.g. gross vs. net vs. delivered-only revenue) and its declared grain, per `docs/loupe-agent.md`'s business-logic requirements for revenue, return rate, margin, margin leakage, and channel mix.
- `shared.metric_catalog.definitions_referencing_table()` (hardened for identifier-normalization in the Phase 4 correction round) is how `affected_metrics` gets populated on incidents — Loupe's own trust/source-health warnings should reuse the same certified names, not invent parallel ones.

## 10. BigQuery and LLM-grounding requirements

- `shared/data_service.py` is the **only** module allowed to construct a `bigquery.Client` or execute SQL/metadata calls. Phase 5 code must call `shared.data_service` functions (`run_query`, `get_table_metadata`, `list_tables`, `derive_source_health`, incident CRUD) — never touch `bigquery` directly, and never re-implement `get_bq_client()`-style client construction inside `apps/loupe_agent/`.
- All queries go through `run_query()`, which enforces: sqlglot-parsed read-only statements only (SELECT/UNION/INTERSECT/EXCEPT — `UnsafeQueryError` otherwise); named parameter binding for all values (string interpolation of a *value* into SQL text is never allowed; identifiers already discovered via profiling/catalog lookups are the only thing assembled into SQL text directly); a bytes-billed ceiling (500 MB default) and timeout (30s default) always set on the query job.
- The LLM (Claude) may only explain or narrate already-computed structured evidence — BigQuery rows, certified metric definitions, deterministic check results, trust scores. It must never invent numbers, redefine a metric, decide data health, or alter a trust score. This is enforced by construction in Governance and Triage: `explanations.py`/`chat.py` modules take already-built domain objects as input and never get raw warehouse access themselves. Loupe's Phase 5 `chat.py`-equivalent must follow the same pattern — the agent's LangChain tools should call `shared.data_service`/`shared.metric_catalog` functions and return structured results; the LLM narrates those results, it does not decide what to query or how to interpret ambiguous cases on its own.
- Per `docs/loupe-agent.md`, every production Loupe response should contain: (1) a direct answer, (2) the metric definition and date range used, (3) the supporting result or visualization, (4) a source-health or trust warning when applicable, (5) query evidence or an audit reference.
- If a source table is degraded or under an active incident (per `shared.data_service.derive_source_health()`), the user must be warned before conclusions are presented — do not silently answer from a degraded or critical source.

## 11. Known placeholders and unresolved risks

- **Phase 6 persistence is not implemented.** `shared/audit.write_event()` and incident-table writes are contract-shaped (correct function signatures, correct data shape) but have only been unit-tested against fake BigQuery clients — never verified against a live `loupe_platform` dataset end-to-end.
- **Schema-drift baselines have no storage.** Every `check_schema_drift()` call defaults to `not_evaluated` until a real snapshot-capture-and-store pipeline exists (Phase 6). This is intentional and honest, not a bug — but Phase 5 should not assume schema-drift checks are "live" for any table yet.
- **No production scheduler exists.** Data Quality Triage checks and (once built) Loupe agent refreshes all currently run synchronously, on demand, from within a Streamlit session — there is no cron/Cloud Scheduler/Composer wiring yet.
- **`docs/architecture.md` and `README.md` are stale relative to the current file layout.** Both still describe a pre-migration single-repo `src/data_service.py` / `src/review.py` / `src/triage.py` / `src/ui.py` layout. The actual code now lives under `shared/` and `apps/{metric_governance,data_quality_triage}/`, split into more files than those docs describe. Phase 5 (or a dedicated documentation pass) should update these docs to avoid confusing future contributors — this handoff document reflects the *actual* current layout, not what those two files say.
- **`apps/loupe_agent/` and `tests/loupe_agent/` are empty** (only `__init__.py` in each). No Phase 5 code exists yet.
- **The Loupe source repo has only been signature-scanned, not fully read.** `main.py` and `app.py` function names/imports were inspected to write this handoff; a full line-by-line read is still needed before migration begins, per the project's "no shortcuts" rule.
- **`ecommerce-analytics-agent/.env` has deliberately never been opened.** Phase 5 will need to ask the user which environment variable names are actually required, since reading the file directly risks exposing secret values in a response or log.
- **The LangChain + `ChatAnthropic` tool-calling architecture in the source `main.py` needs redesign, not a direct port**, to fit the "LLM explains, never decides" boundary already enforced in Governance/Triage. Expect this to be the most judgment-heavy part of Phase 5.
- **Phase 2 and Phase 3 correction-round details are summarized, not fully itemized, in this document** (§2) — the itemized bullet lists exist in the project's conversation history if a future session needs the exact wording; they were deliberately not reconstructed from memory here to avoid misquoting them.

## 12. Reminder

The original repositories — `metric-governance-copilot`, `data-quality-incident-triage-agent`, and `ecommerce-analytics-agent` — must remain untouched: no writes, moves, renames, or commits, in this phase or any future one. They exist solely as read-only sources to migrate *from*.
