# Loupe Platform API

Typed FastAPI delivery boundary for the [Loupe AI Analytics Platform](../README.md). All three frontends (`loupe-web`, `governance-web`, `triage-web`) call this API rather than the warehouse directly.

## Structure

```text
api/
  main.py         FastAPI app, CORS configuration, health check
  models.py       Pydantic request/response models
  dependencies.py Shared request dependencies (e.g. warehouse client)
  routes/         One router per platform layer
    loupe.py         Business Performance Layer endpoints
    governance.py     BI Trust Layer endpoints
    triage.py         Engineering Reliability Layer endpoints
  services/       Business logic each route delegates to, calling into apps/ and shared/
```

Routes stay thin: request validation and delegation only. Deterministic logic (scoring, review, detection, safety checks) lives in `apps/` and `shared/`, not in `api/`.

## Run locally

```bash
pip install -e .
uvicorn api.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

## Testing

```bash
pytest tests/api
```

## Deployment

Deploys to Cloud Run as a single container built from `api/Dockerfile`, with a build context of the repository root (the image also needs `apps/` and `shared/`). See [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md) for the full deployment sequence, including CORS configuration for the three deployed frontends.
