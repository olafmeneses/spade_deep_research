"""Deep Research API module.

This module provides a modular FastAPI application for the Deep Research service.
"""

from src.api.app import create_app

app = create_app()

__all__ = ["app", "create_app"]
