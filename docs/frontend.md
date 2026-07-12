# Loupe production frontend

The final delivery layer is three separate Next.js applications over one typed FastAPI boundary:

| Application | Local URL | Visual identity |
|---|---:|---|
| Loupe | `http://localhost:3000` | Signal Intelligence |
| Metric Governance | `http://localhost:3001` | Violet Ledger |
| Data Quality Triage | `http://localhost:3002` | Midnight Command |
| Typed API | `http://localhost:8000` | Shared Python domain services |

The product switcher links between separate URLs. It does not merge the products into one application.

## Local configuration

The API uses Application Default Credentials and the existing persistence environment variables:

- `LOUPE_BQ_PROJECT`
- `LOUPE_DATASET`
- `LOUPE_BQ_LOCATION`
- `LOUPE_PERSISTENCE_MODE=persisted`
- `ANTHROPIC_API_KEY` only when grounded Loupe narration should use Claude
- `LOUPE_ALLOWED_ORIGINS`, a comma-separated list of the three deployed frontend origins

Each frontend uses the public variables documented in `frontend/.env.example`. No frontend receives a cloud credential or service-account key.

## Local development

Run the API with Uvicorn, then run each application independently from `frontend/` using its `dev:*` script. The default ports are 3000, 3001, and 3002.

## Data honesty

- Loupe KPIs and trends come from live BigQuery queries.
- Governance reads the persisted metric catalog and computes deterministic review/trust results.
- Triage reads persisted incidents and current source health, and its lifecycle actions use transactional persistence.
- Loading, unavailable, empty, and conflict states never substitute sample metrics, SQL reviews, or incidents.

## Visual acceptance

`docs/ui-reference/` remains the binding design pack. The approved HTML file is unchanged. Browser QA captures are written under `output/playwright/` and are not application runtime assets.
