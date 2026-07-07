"""FastAPI application factory."""

import logging

from fastapi import FastAPI

from src.api.lifespan import lifespan
from src.api.routes import research_router, health_router

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("src").setLevel(logging.DEBUG)
# Suppress XMPP verbose logs
logging.getLogger("spade").setLevel(logging.WARNING)
logging.getLogger("pyjabber").setLevel(logging.WARNING)
logging.getLogger("slixmpp").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Suppress loguru to prevent duplicate output (pyjabber uses loguru internally)
try:
    from loguru import logger as _loguru
    _loguru.remove()
except ImportError:
    pass

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.
    
    This factory function creates the FastAPI app with:
    - Lifespan management for agent system startup/shutdown
    - All API routes registered
    - OpenAPI documentation configured
    
    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="Deep Research API",
        description=(
            "Multi-agent research system powered by SPADE."
        ),
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    
    # Register routers
    app.include_router(health_router)
    app.include_router(research_router)
    
    logger.info("FastAPI application created")
    
    return app
