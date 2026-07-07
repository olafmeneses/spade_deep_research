"""Tests for src.api.state — AppState idempotency and readiness."""

import time

import pytest

from src.api.state import AppState, IDEMPOTENCY_KEY_TTL


@pytest.fixture()
def state() -> AppState:
    s = AppState()
    yield s
    s.reset()


class TestAppStateIdempotency:
    def test_store_and_retrieve(self, state: AppState):
        state.store_idempotency_key("key-1", "session-abc")
        assert state.get_idempotency_session("key-1") == "session-abc"

    def test_missing_key_returns_none(self, state: AppState):
        assert state.get_idempotency_session("no-such") is None

    def test_expired_key_returns_none(self, state: AppState):
        state.store_idempotency_key("key-1", "session-abc")
        # Simulate time passing beyond TTL
        state._idempotency_keys["key-1"] = ("session-abc", time.time() - IDEMPOTENCY_KEY_TTL - 1)
        assert state.get_idempotency_session("key-1") is None

    def test_cleanup_removes_expired(self, state: AppState):
        past = time.time() - IDEMPOTENCY_KEY_TTL - 10
        state._idempotency_keys["old"] = ("s1", past)
        state._idempotency_keys["new"] = ("s2", time.time())
        state._cleanup_expired_keys()
        assert "old" not in state._idempotency_keys
        assert "new" in state._idempotency_keys


class TestAppStateReadiness:
    def test_not_ready_by_default(self, state: AppState):
        assert state.is_ready is False

    def test_ready_when_orchestrator_set(self, state: AppState):
        state.orchestrator = object()  # truthy
        assert state.is_ready is True


class TestAppStateReset:
    def test_reset_clears_all(self, state: AppState):
        state.orchestrator = object()
        state.store_idempotency_key("k", "s")
        state.reset()
        assert state.orchestrator is None
        assert state.is_ready is False
        assert state.get_idempotency_session("k") is None
