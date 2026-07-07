"""API route modules."""

from src.api.routes.research import router as research_router
from src.api.routes.health import router as health_router

__all__ = ["research_router", "health_router"]
