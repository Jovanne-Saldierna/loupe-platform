from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_client
from api.models import ErrorResponse, GovernanceCatalogResponse, GovernanceReviewRequest, GovernanceReviewResponse
from api.services.governance_review import (
    CatalogUnavailableError,
    MetricNotFoundError,
    build_governance_review,
    list_governed_metrics,
)

router = APIRouter(prefix="/api/v1/governance", tags=["Metric Governance"])


@router.get("/catalog", response_model=GovernanceCatalogResponse, responses={503: {"model": ErrorResponse}})
def catalog(client=Depends(get_client)) -> GovernanceCatalogResponse:
    try:
        return list_governed_metrics(client)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The persisted metric catalog is temporarily unavailable.") from exc


@router.post(
    "/sql-review",
    response_model=GovernanceReviewResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def sql_review(payload: GovernanceReviewRequest, client=Depends(get_client)) -> GovernanceReviewResponse:
    try:
        return build_governance_review(client, payload.sql, payload.metric_name)
    except MetricNotFoundError as exc:
        raise HTTPException(status_code=404, detail="The selected metric is not registered.") from exc
    except CatalogUnavailableError as exc:
        raise HTTPException(status_code=503, detail="The persisted metric catalog is temporarily unavailable.") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The deterministic SQL review is temporarily unavailable.") from exc
