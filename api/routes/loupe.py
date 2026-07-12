from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_client
from api.models import ErrorResponse, LoupeOverviewResponse
from api.services.loupe_overview import build_loupe_overview

router = APIRouter(prefix="/api/v1/loupe", tags=["Loupe"])


@router.get(
    "/overview",
    response_model=LoupeOverviewResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def overview(
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    end_date: date = Query(default_factory=date.today),
    client=Depends(get_client),
) -> LoupeOverviewResponse:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date.")
    if (end_date - start_date).days > 365:
        raise HTTPException(status_code=400, detail="Date ranges may not exceed 366 days.")
    try:
        return build_loupe_overview(client, start_date, end_date)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc
