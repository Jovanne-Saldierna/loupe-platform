# Loupe AI Analytics Platform Family

Loupe is a three-layer analytics platform built around one operating rule:

> Live warehouse data, governed logic, deterministic controls, and plain-English AI explanation.

AI is not the source of truth. BigQuery results, certified metric definitions, deterministic quality checks, parsed SQL, and audit logs are authoritative. An LLM may explain those facts, but it must not invent or independently determine them.

## Platform layers

| Layer | Project | Responsibility |
|---|---|---|
| Assistant | Loupe E-Commerce Agent | Answers business questions from live query results |
| Definition | Metric Governance Copilot | Certifies definitions, reviews SQL, and records governance activity |
| Reliability | Data Quality Incident Triage Agent | Detects deterministic data issues and creates incident playbooks |

All three projects use the public BigQuery dataset `bigquery-public-data.thelook_ecommerce` for demonstrations.

## Documentation

- [Platform architecture](docs/architecture.md)
- [Loupe E-Commerce Agent](docs/loupe-agent.md)
- [Metric Governance Copilot](docs/metric-governance.md)
- [Data Quality Incident Triage Agent](docs/data-quality-triage.md)
- [Metrics and trust contracts](docs/contracts.md)
- [Development and operations](docs/development.md)

## Repository layout

This is a monorepo. One shared BigQuery gateway backs three independent Streamlit apps:

```text
shared/            One BigQuery gateway used by every app (see below)
apps/
  loupe_agent/          Assistant layer -- Loupe E-Commerce Agent
  metric_governance/    Definition layer -- Metric Governance Copilot
  data_quality_triage/  Reliability layer -- Data Quality Incident Triage Agent
tests/             Mirrors apps/ and shared/, one test package per module
docs/              This documentation set
```

Each app under `apps/<name>/` follows the same internal shape:

- one or more business-logic modules (e.g. `metrics.py`, `review.py`, `checks.py`) that call into `shared/` and never construct a BigQuery client themselves
- `chat.py`: the only module that talks to the LLM; narrates structured evidence produced by the business-logic modules, never queries or decides on its own
- `ui.py`: renders application state and calls the app's own service functions; contains no direct warehouse queries or duplicated business logic
- `main.py`: a thin Streamlit entry point that builds a client, builds state, and hands off to `ui.py`

## Shared implementation boundaries

- `shared/data_service.py`: the **only** module in the monorepo allowed to construct a `bigquery.Client` or execute SQL/metadata calls. Every app-level query function routes through `shared.data_service.run_query()` (sqlglot-enforced read-only SQL, named-parameter binding, byte-billed ceiling, timeout) or `shared.data_service.get_bigquery_client()` (Application Default Credentials -- no service-account JSON keys).
- `shared/metric_catalog.py`: the certified metric catalog. As of this writing all five entries (revenue, margin, return_rate, margin_leakage, channel_mix) carry `certification_status="pending_validation"` -- no app may describe them as certified in code, UI text, or documentation until that status is deliberately changed.
- `shared/incidents.py`, `shared/trust_scoring.py`: deterministic incident classification and trust scoring, consumed by the Reliability layer and by source-health lookups elsewhere.
- `shared/audit.py`: structured audit-event writes.
- `shared/models.py`: cross-app data contracts (e.g. the `SourceHealthStatus` literal `"healthy" | "degraded" | "critical"`) shared by all three apps.

Direct BigQuery calls do not belong in any `apps/*/ui.py`, `chat.py`, or `main.py`.

### Source-health status

Real source-health derivation (`shared.data_service.derive_source_health()`) depends on a `loupe_platform.incidents` table that does not exist yet -- that table is Phase 6 (persistence) work, which has not started. Until it exists, any app surfacing source health reports an explicit `"unknown"` state rather than silently implying data is healthy, and awards no trust benefit for it. See `apps/loupe_agent/source_health.py` for the current implementation of that honesty guarantee.

## Shared stack

Python, Streamlit, Google BigQuery, Pandas, Plotly, LangChain, Anthropic Claude, sqlglot, and pytest. See `pyproject.toml` for pinned version ranges; test/dev-only tooling lives in the `dev` optional-dependency group.

