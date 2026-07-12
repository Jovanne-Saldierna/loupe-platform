# Locked UI implementation mapping

This mapping translates the approved reference into code without changing its design.

| Reference concept | Shared implementation | Loupe | Governance | Triage |
|---|---|---|---|---|
| Product shell | `@loupe/ui` application frame | bright Signal Intelligence | precise Violet Ledger | dark Midnight Command |
| Navigation | icon + label items, responsive grid on mobile | Overview, Ask Loupe, Performance, Customers, Products, Scenarios | Overview, Catalog, SQL Review, Definition Diff, Lineage, Audit | Warehouse, Tables, Checks, Incidents, Timeline |
| Accent | semantic `--accent` token | signal blue | ledger green/violet context | operational violet |
| Cards | quiet surface, restrained border, 16px radius | approachable KPI and evidence cards | denser SQL and contract cards | dark operational cards |
| Status | shared badge variants | BigQuery/source health | trust/certification | severity/lifecycle |
| Data | typed FastAPI responses only | live BigQuery overview | persisted catalog and deterministic review | persisted incidents/checks |
| Failure | shared unavailable state | never substitutes mock KPIs | never substitutes local catalog | never substitutes fictional incidents |

The top application switcher in the approved HTML is a reference-only comparison control. Production navigation uses ordinary links between three separately built and deployed applications.
