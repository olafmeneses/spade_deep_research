"""FastAPI dependency injection functions."""

from fastapi import HTTPException

from src.api.state import app_state
from src.orchestrator import DeepResearchAgent


def get_orchestrator() -> DeepResearchAgent:
    """Get the orchestrator instance or raise 503 if not ready.
    
    Use this as a FastAPI dependency to ensure the service is ready
    before processing requests.
    
    Raises:
        HTTPException: 503 if the service is not ready
    """
    if not app_state.is_ready or app_state.orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="Service not ready. The agent system is still initializing."
        )
    return app_state.orchestrator


def get_app_state():
    """Get the application state.
    
    Useful for health checks and status endpoints that need
    to check readiness without raising an error.
    """
    return app_state
