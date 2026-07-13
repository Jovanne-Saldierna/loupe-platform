from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_client
from api.models import (
    ErrorResponse,
    IncidentTransitionRequest,
    IncidentTransitionResponse,
    TriageHelperRequest,
    TriageHelperResponse,
    TriageWarehouseResponse,
)
from api.services.triage_helper import answer_triage_question
from api.services.triage_warehouse import build_warehouse_health, transition_incident
from shared.config import load_platform_config

router = APIRouter(prefix="/api/v1/triage", tags=["Data Quality Triage"])


@router.get("/warehouse", response_model=TriageWarehouseResponse, responses={503: {"model": ErrorResponse}})
def warehouse(client=Depends(get_client)) -> TriageWarehouseResponse:
    try:
        return build_warehouse_health(client, load_platform_config())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Live warehouse health is temporarily unavailable.") from exc


@router.post(
    "/incidents/{incident_id}/transition",
    response_model=IncidentTransitionResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def transition(incident_id: str, payload: IncidentTransitionRequest, client=Depends(get_client)) -> IncidentTransitionResponse:
    try:
        return transition_incident(
            client,
            load_platform_config(),
            incident_id=incident_id,
            target_status=payload.target_status,
            expected_current_status=payload.expected_current_status,
            resolution_notes=payload.resolution_notes,
            actor="data_quality_triage.api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Stale state and persistence contention are intentionally generic at
        # the HTTP boundary; the client refreshes the authoritative row.
        raise HTTPException(status_code=409, detail="The incident changed or the transition could not be committed. Refresh and retry.") from exc


@router.post(
    "/helper",
    response_model=TriageHelperResponse,
    responses={503: {"model": ErrorResponse}},
)
def helper(payload: TriageHelperRequest) -> TriageHelperResponse:
    """Answer a question about the currently selected incident, grounded
    only in the incident context the client sends (see
    api/services/triage_helper.py). No BigQuery client dependency: this
    never re-queries the warehouse, it only narrates the deterministic
    incident record the caller already has."""

    try:
        return TriageHelperResponse(answer=answer_triage_question(payload))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Loupe could not produce a grounded answer right now.") from exc
