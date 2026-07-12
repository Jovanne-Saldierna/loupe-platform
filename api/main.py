import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.loupe import router as loupe_router
from api.routes.governance import router as governance_router
from api.routes.triage import router as triage_router

app = FastAPI(
    title="Loupe Platform API",
    version="1.0.0",
    description="Typed delivery boundary for Loupe, Governance, and Triage.",
)

# Separate local Next.js applications; deployed origins are supplied later.
_LOCAL_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:3002",
]
_configured_origins = [origin.strip() for origin in os.getenv("LOUPE_ALLOWED_ORIGINS", "").split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_origins or _LOCAL_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(loupe_router)
app.include_router(governance_router)
app.include_router(triage_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
