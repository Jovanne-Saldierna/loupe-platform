from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_client
from api.models import (
    CategoryBreakdown,
    ChannelMonth,
    ErrorResponse,
    LoupeAskRequest,
    LoupeAskResponse,
    LoupeBenchmarkResponse,
    LoupeCategoriesResponse,
    LoupeChannelMixResponse,
    LoupeOverviewResponse,
    LoupeReturnsLeakageResponse,
    LoupeStatesResponse,
    ReturnsLeakageRow,
    StateBreakdown,
)
from api.services.loupe_overview import build_loupe_overview

router = APIRouter(prefix="/api/v1/loupe", tags=["Loupe"])


def _validate_window(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date.")
    if (end_date - start_date).days > 365:
        raise HTTPException(status_code=400, detail="Date ranges may not exceed 366 days.")


@router.get(
    "/overview",
    response_model=LoupeOverviewResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def overview(
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    end_date: date = Query(default_factory=date.today),
    categories: list[str] | None = Query(default=None),
    states: list[str] | None = Query(default=None),
    client=Depends(get_client),
) -> LoupeOverviewResponse:
    _validate_window(start_date, end_date)
    try:
        return build_loupe_overview(client, start_date, end_date, categories, states)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.get(
    "/categories",
    response_model=LoupeCategoriesResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def categories(
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    end_date: date = Query(default_factory=date.today),
    states: list[str] | None = Query(default=None),
    client=Depends(get_client),
) -> LoupeCategoriesResponse:
    """Category leaderboard for the window/state filter -- thin wrapper over
    apps.loupe_agent.metrics.get_category_leaderboard_dashboard (already
    live-queried, unused by any route prior to this restoration)."""

    _validate_window(start_date, end_date)
    try:
        from apps.loupe_agent.metrics import get_category_leaderboard_dashboard

        rows = get_category_leaderboard_dashboard(client, start_date, end_date, states)
        return LoupeCategoriesResponse(
            start_date=start_date,
            end_date=end_date,
            categories=[CategoryBreakdown(**row) for row in rows],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.get(
    "/states",
    response_model=LoupeStatesResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def states(
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    end_date: date = Query(default_factory=date.today),
    categories: list[str] | None = Query(default=None),
    client=Depends(get_client),
) -> LoupeStatesResponse:
    """Revenue-by-state breakdown -- thin wrapper over
    apps.loupe_agent.metrics.get_state_breakdown_dashboard."""

    _validate_window(start_date, end_date)
    try:
        from apps.loupe_agent.metrics import get_state_breakdown_dashboard

        rows = get_state_breakdown_dashboard(client, start_date, end_date, categories)
        return LoupeStatesResponse(
            start_date=start_date,
            end_date=end_date,
            states=[StateBreakdown(**row) for row in rows],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.get(
    "/channel-mix",
    response_model=LoupeChannelMixResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def channel_mix(
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=29)),
    end_date: date = Query(default_factory=date.today),
    categories: list[str] | None = Query(default=None),
    states: list[str] | None = Query(default=None),
    client=Depends(get_client),
) -> LoupeChannelMixResponse:
    """Paid vs. organic monthly order mix -- thin wrapper over
    apps.loupe_agent.metrics.get_channel_mix_range. paid_share_pct is
    computed here from the same paid/total counts the query already
    returns (identical arithmetic to get_channel_mix_trend's own
    paid_share_pct, not a new metric)."""

    _validate_window(start_date, end_date)
    try:
        from apps.loupe_agent.metrics import get_channel_mix_range

        rows = get_channel_mix_range(client, start_date, end_date, categories, states)
        return LoupeChannelMixResponse(
            start_date=start_date,
            end_date=end_date,
            months=[
                ChannelMonth(
                    month=row["month"],
                    paid=row["paid"],
                    unpaid=row["unpaid"],
                    total=row["total"],
                    paid_share_pct=round((row["paid"] / row["total"]) * 100, 1) if row["total"] else 0.0,
                )
                for row in rows
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.get(
    "/returns-leakage",
    response_model=LoupeReturnsLeakageResponse,
    responses={503: {"model": ErrorResponse}},
)
def returns_leakage(client=Depends(get_client)) -> LoupeReturnsLeakageResponse:
    """Every category ranked by absolute margin dollars lost to returns --
    thin wrapper over apps.loupe_agent.metrics.get_returns_leakage
    (all-time by definition, matching the original Streamlit app's
    unfiltered leakage ranking)."""

    try:
        from apps.loupe_agent.metrics import get_returns_leakage as _get_returns_leakage

        rows = _get_returns_leakage(client)
        return LoupeReturnsLeakageResponse(categories=[ReturnsLeakageRow(**row) for row in rows])
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.get(
    "/benchmark",
    response_model=LoupeBenchmarkResponse,
    responses={503: {"model": ErrorResponse}},
)
def benchmark(client=Depends(get_client)) -> LoupeBenchmarkResponse:
    """Company-wide blended margin/return-rate averages -- thin wrapper
    over apps.loupe_agent.metrics.get_company_benchmark."""

    try:
        from apps.loupe_agent.metrics import get_company_benchmark

        return LoupeBenchmarkResponse(**get_company_benchmark(client))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Live governed warehouse data is temporarily unavailable.",
        ) from exc


@router.post("/ask", response_model=LoupeAskResponse, responses={503: {"model": ErrorResponse}})
def ask(payload: LoupeAskRequest, client=Depends(get_client)) -> LoupeAskResponse:
    try:
        from apps.loupe_agent.chat import run_agent

        result = run_agent(client, payload.question.strip())
        health = result.get("source_health") or {}
        return LoupeAskResponse(
            category=result.get("category", "general"),
            answer=result.get("answer") or "No grounded answer was produced.",
            source_health_status=health.get("status") if isinstance(health, dict) else None,
            source_health_warning=health.get("warning") if isinstance(health, dict) else None,
            raw_data=result.get("raw_data"),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Loupe could not produce a grounded answer right now.") from exc
