"""Root conftest — environment setup and shared fixtures.

Tests set API credentials before any ``src`` import so runtime objects that
instantiate LLM/search clients see deterministic placeholder values.
"""

import os

# ── Set placeholder env vars BEFORE any src import ──────────────────
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("TAVILY_API_KEY", "test-key")
# Disable ChromaDB extensions for deterministic registry state
os.environ.pop("CHROMADB_PATH", None)

import pytest

from src.telemetry import telemetry_registry
from src.session import ResearchSession, SessionManager
from src.extensions import ExtensionRegistry
from src.references import reference_registry
from src.utils.cost_tracker import CostTrackerCallback


# ── Session fixtures ─────────────────────────────────────────────────

@pytest.fixture()
def session_manager() -> SessionManager:
    """A fresh ``SessionManager`` with no sessions."""
    return SessionManager()


@pytest.fixture()
def sample_session() -> ResearchSession:
    """A ``ResearchSession`` initialised with known test data."""
    return ResearchSession(
        session_id="test-session-001",
        initial_query="What is deep learning?",
        current_query="What is deep learning?",
    )


# ── Extension registry fixtures ──────────────────────────────────────

@pytest.fixture()
def fresh_registry() -> ExtensionRegistry:
    """An isolated ``ExtensionRegistry`` (not the global singleton)."""
    return ExtensionRegistry()


# ── Reference registry fixtures ──────────────────────────────────────

@pytest.fixture()
def _clear_reference_registry():
    """Clear the singleton ``reference_registry`` around each test."""
    reference_registry._refs.clear()
    yield
    reference_registry._refs.clear()


@pytest.fixture(autouse=True)
def _isolate_telemetry_registry(tmp_path):
    """Keep benchmark archive writes isolated per test."""
    telemetry_registry._records.clear()
    telemetry_registry.archive_store.root = tmp_path / "session_archive"
    telemetry_registry.archive_store.root.mkdir(parents=True, exist_ok=True)
    yield
    telemetry_registry._records.clear()


# ── Cost tracker fixtures ────────────────────────────────────────────

@pytest.fixture()
def cost_tracker_instance() -> CostTrackerCallback:
    """A fresh ``CostTrackerCallback`` (NOT the global singleton)."""
    return CostTrackerCallback()
