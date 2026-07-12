from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.loupe import router as loupe_router

app = FastAPI(
    title="Loupe Platform API",
    version="1.0.0",
    description="Typed delivery boundary for Loupe, Governance, and Triage.",
)

# Separate local Next.js applications; deployed origins are supplied later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(loupe_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
