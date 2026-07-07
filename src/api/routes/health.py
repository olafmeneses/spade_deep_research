"""Health check endpoints."""

from fastapi import APIRouter, Depends

from src.api.state import AppState
from src.api.schemas import HealthResponse
from src.api.dependencies import get_app_state

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Check the health status of the Deep Research API service.",
)
async def health_check(state: AppState = Depends(get_app_state)) -> HealthResponse:
    """Health check endpoint.
    
    Returns the current health status of the service, including:
    - Overall service status
    - XMPP server connection status
    - Number of active research sessions
    """
    if not state.is_ready:
        return HealthResponse(status="starting")
    
    # Get active session count
    active_sessions = 0
    if state.orchestrator:
        active = await state.orchestrator.get_active_sessions()
        active_sessions = len(active)
    
    return HealthResponse(
        status="healthy",
        xmpp_connected=state.xmpp_server is not None,
        active_sessions=active_sessions,
    )


@router.get(
    "/ready",
    summary="Readiness check",
    description="Check if the service is ready to accept requests.",
)
async def readiness_check(state: AppState = Depends(get_app_state)) -> dict:
    """Readiness probe for Kubernetes-style deployments."""
    if not state.is_ready:
        return {"ready": False, "reason": "Service still initializing"}
    return {"ready": True}
