# Deployment: Cloud Run (API) + Vercel (3 Next.js apps)

Target for this deployment: `ai-weekend-agent-501502` / `loupe_platform_test`
(US). This is the same project and dataset validated end-to-end by
`tools/phase6e_ops/live_integration_validation.py` (run `10b3fd502d`) — already
bootstrapped, seeded, and BigQuery-compatible. `loupe_platform` (production)
has not been bootstrapped and its bootstrap tool is intentionally guarded
against ever targeting it; that is a separate, later decision, not part of
this deployment.

Order matters: deploy the API first (frontends need its URL), then the three
Vercel apps (the API needs their URLs for CORS), then update the API's CORS
env var and redeploy.

## 1. Cloud Run: the FastAPI boundary

One-time: create a dedicated service account and grant it warehouse access
scoped to this dataset (no downloaded key — Cloud Run uses the attached SA's
identity as Application Default Credentials automatically).

```bash
gcloud iam service-accounts create loupe-api \
  --project ai-weekend-agent-501502 \
  --display-name "Loupe API (Cloud Run)"

gcloud projects add-iam-policy-binding ai-weekend-agent-501502 \
  --member "serviceAccount:loupe-api@ai-weekend-agent-501502.iam.gserviceaccount.com" \
  --role "roles/bigquery.jobUser"

bq add-iam-policy-binding \
  --member "serviceAccount:loupe-api@ai-weekend-agent-501502.iam.gserviceaccount.com" \
  --role "roles/bigquery.dataEditor" \
  ai-weekend-agent-501502:loupe_platform_test
```

Build and deploy (build context is the repo root — see `api/Dockerfile`):

```bash
cd /Users/jovannesaldierna/Projects/loupe

gcloud builds submit --tag gcr.io/ai-weekend-agent-501502/loupe-api \
  -f api/Dockerfile .

gcloud run deploy loupe-api \
  --project ai-weekend-agent-501502 \
  --region us-central1 \
  --image gcr.io/ai-weekend-agent-501502/loupe-api \
  --service-account loupe-api@ai-weekend-agent-501502.iam.gserviceaccount.com \
  --set-env-vars LOUPE_BQ_PROJECT=ai-weekend-agent-501502,LOUPE_DATASET=loupe_platform_test,LOUPE_BQ_LOCATION=US \
  --allow-unauthenticated
```

Note the service URL printed at the end (e.g.
`https://loupe-api-xxxxx-uc.a.run.app`) — you need it for step 2. Verify:

```bash
curl https://<loupe-api-url>/health
```

`LOUPE_ALLOWED_ORIGINS` is intentionally not set yet — until it is, the API
falls back to localhost-only CORS (see `api/main.py`), so the deployed
frontends can't call it yet. That's expected; fixed in step 3.

## 2. Vercel: three separate projects

Each Next.js app is its own Vercel project pointing at the same GitHub repo,
distinguished only by **Root Directory**. Vercel auto-detects the npm
workspaces monorepo (`frontend/package.json` → `workspaces`) and installs
from the workspace root automatically — no `vercel.json` needed.

Repeat for each app (`vercel.com/new` → import this repo → set Root
Directory → set env var → Deploy):

| App | Root Directory | Env var |
|---|---|---|
| Loupe | `frontend/apps/loupe-web` | `NEXT_PUBLIC_API_BASE_URL=https://<loupe-api-url>` |
| Governance | `frontend/apps/governance-web` | `NEXT_PUBLIC_API_BASE_URL=https://<loupe-api-url>` |
| Triage | `frontend/apps/triage-web` | `NEXT_PUBLIC_API_BASE_URL=https://<loupe-api-url>` |

CLI equivalent, run once per app directory:

```bash
cd frontend/apps/loupe-web && vercel link && vercel env add NEXT_PUBLIC_API_BASE_URL production
# paste the Cloud Run URL from step 1 when prompted, then:
vercel --prod
```

Record the three production URLs Vercel assigns (e.g.
`https://loupe-web.vercel.app`, `https://governance-web.vercel.app`,
`https://triage-web.vercel.app`).

## 3. Close the loop: CORS

Update the Cloud Run service with the three real Vercel origins:

```bash
gcloud run services update loupe-api \
  --project ai-weekend-agent-501502 \
  --region us-central1 \
  --set-env-vars LOUPE_ALLOWED_ORIGINS="https://loupe-web.vercel.app,https://governance-web.vercel.app,https://triage-web.vercel.app"
```

(This replaces all env vars in one call — re-include `LOUPE_BQ_PROJECT`,
`LOUPE_DATASET`, `LOUPE_BQ_LOCATION` from step 1, or use
`--update-env-vars` instead of `--set-env-vars` to only add this one.)

## 4. Verify the binding end-to-end story, live

1. Open the deployed Triage app, drive an incident through
   `open → acknowledged → investigating → resolved` (or leave one open).
2. Open the deployed Governance app for the affected metric — confirm the
   incident is reflected in source health / trust score with evidence.
3. Open the deployed Loupe app — confirm it surfaces the same warning before
   naming the affected metric.
4. Resolve the incident in Triage; confirm Governance and Loupe both return
   to healthy.

This mirrors live run `10b3fd502d` from `live_integration_validation.py`,
now exercised through the deployed UI instead of the script.
