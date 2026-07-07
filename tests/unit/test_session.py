"""Tests for src.session — ResearchSession, SessionManager, and related types."""

import asyncio

import pytest

from src.session import (
    ResearchSession,
    SessionManager,
    SessionState,
    SessionEvent,
    MessageType,
    ReferenceSource,
)


# ── ResearchSession state transitions ─────────────────────────────────


class TestResearchSessionState:
    """State machine and lifecycle tests for ResearchSession."""

    def test_initial_state_is_created(self, sample_session: ResearchSession):
        assert sample_session.state is SessionState.CREATED

    def test_update_state(self, sample_session: ResearchSession):
        sample_session.update_state(SessionState.PLANNING)
        assert sample_session.state is SessionState.PLANNING

    def test_update_state_updates_timestamp(self, sample_session: ResearchSession):
        old_ts = sample_session.updated_at
        sample_session.update_state(SessionState.RESEARCHING)
        assert sample_session.updated_at >= old_ts

    def test_mark_complete(self, sample_session: ResearchSession):
        sample_session.mark_complete()
        assert sample_session.state is SessionState.COMPLETED
        assert sample_session.completion_event.is_set()

    def test_mark_failed(self, sample_session: ResearchSession):
        sample_session.mark_failed()
        assert sample_session.state is SessionState.FAILED
        assert sample_session.completion_event.is_set()

    def test_mark_cancelled(self, sample_session: ResearchSession):
        sample_session.mark_cancelled()
        assert sample_session.state is SessionState.CANCELLED
        assert sample_session.cancellation_event.is_set()
        assert sample_session.completion_event.is_set()

    @pytest.mark.parametrize(
        "state,expected",
        [
            (SessionState.CREATED, True),
            (SessionState.PLANNING, True),
            (SessionState.RESEARCHING, True),
            (SessionState.WRITING, True),
            (SessionState.REVIEWING, True),
            (SessionState.COMPLETED, False),
            (SessionState.FAILED, False),
            (SessionState.CANCELLED, False),
        ],
    )
    def test_is_active(self, sample_session: ResearchSession, state, expected):
        sample_session.state = state
        assert sample_session.is_active is expected

    def test_is_cancelled_false_by_default(self, sample_session: ResearchSession):
        assert sample_session.is_cancelled is False

    def test_is_cancelled_after_cancel(self, sample_session: ResearchSession):
        sample_session.mark_cancelled()
        assert sample_session.is_cancelled is True


# ── References ────────────────────────────────────────────────────────


class TestResearchSessionReferences:
    def test_add_reference(self, sample_session: ResearchSession):
        sample_session.add_reference("http://example.com", ReferenceSource.TAVILY, "Example")
        assert len(sample_session.references) == 1
        assert sample_session.references[0].identifier == "http://example.com"
        assert sample_session.references[0].source_type is ReferenceSource.TAVILY

    def test_add_reference_dedup(self, sample_session: ResearchSession):
        sample_session.add_reference("http://example.com", ReferenceSource.TAVILY)
        sample_session.add_reference("http://example.com", ReferenceSource.TAVILY)
        assert len(sample_session.references) == 1

    def test_add_different_references(self, sample_session: ResearchSession):
        sample_session.add_reference("http://a.com", ReferenceSource.TAVILY)
        sample_session.add_reference("http://b.com", ReferenceSource.ARXIV)
        assert len(sample_session.references) == 2


# ── Event broadcasting ────────────────────────────────────────────────


class TestResearchSessionEvents:
    async def test_subscribe_receives_state_change(self, sample_session: ResearchSession):
        queue = sample_session.subscribe()
        sample_session.update_state(SessionState.PLANNING)
        assert not queue.empty()
        event = queue.get_nowait()
        assert event.event_type == "state_change"
        assert event.data["status"] == "planning"

    async def test_unsubscribe(self, sample_session: ResearchSession):
        queue = sample_session.subscribe()
        sample_session.unsubscribe(queue)
        sample_session.update_state(SessionState.PLANNING)
        assert queue.empty()

    async def test_broadcast_progress(self, sample_session: ResearchSession):
        queue = sample_session.subscribe()
        sample_session.broadcast_progress("Processing...", phase="researching")
        event = queue.get_nowait()
        assert event.event_type == "progress"
        assert event.data["message"] == "Processing..."
        assert event.data["phase"] == "researching"

    async def test_mark_complete_broadcasts_completed(self, sample_session: ResearchSession):
        sample_session.current_report = "# Report"
        queue = sample_session.subscribe()
        sample_session.mark_complete()
        # 2 events: state_change from update_state + completed from mark_complete broadcast
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        types = [e.event_type for e in events]
        assert "state_change" in types
        assert "completed" in types

    async def test_mark_cancelled_broadcasts(self, sample_session: ResearchSession):
        queue = sample_session.subscribe()
        sample_session.mark_cancelled()
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        types = [e.event_type for e in events]
        assert "cancelled" in types


# ── Retry counts ──────────────────────────────────────────────────────


class TestResearchSessionRetry:
    def test_get_retry_count_default(self, sample_session: ResearchSession):
        assert sample_session.get_retry_count("research") == 0

    def test_increment_retry(self, sample_session: ResearchSession):
        assert sample_session.increment_retry("research") == 1
        assert sample_session.increment_retry("research") == 2
        assert sample_session.get_retry_count("research") == 2


# ── SessionEvent ──────────────────────────────────────────────────────


class TestSessionEvent:
    def test_to_dict(self):
        from datetime import datetime

        now = datetime(2026, 1, 1, 12, 0, 0)
        event = SessionEvent(event_type="state_change", timestamp=now, data={"foo": 1})
        d = event.to_dict()
        assert d["event"] == "state_change"
        assert d["timestamp"] == "2026-01-01T12:00:00"
        assert d["data"] == {"foo": 1}


# ── SessionManager ───────────────────────────────────────────────────


class TestSessionManager:
    def test_create_session(self, session_manager: SessionManager):
        session = session_manager.create_session("test query")
        assert session.initial_query == "test query"
        assert session.session_id in session_manager.get_all_sessions()

    def test_create_session_with_extensions(self, session_manager: SessionManager):
        ext = {"custom_source": {"domain_id": 1}}
        session = session_manager.create_session("q", extensions=ext)
        assert session.extensions == ext

    def test_create_session_disable_critic_default(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        assert session.disable_critic is False

    def test_create_session_disable_critic_true(self, session_manager: SessionManager):
        session = session_manager.create_session("q", disable_critic=True)
        assert session.disable_critic is True

    def test_get_session_found(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        assert session_manager.get_session(session.session_id) is session

    def test_get_session_not_found(self, session_manager: SessionManager):
        assert session_manager.get_session("nonexistent") is None

    def test_remove_session(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        session_manager.remove_session(session.session_id)
        assert session_manager.get_session(session.session_id) is None

    def test_get_active_sessions(self, session_manager: SessionManager):
        s1 = session_manager.create_session("q1")
        s2 = session_manager.create_session("q2")
        s2.mark_complete()
        active = session_manager.get_active_sessions()
        assert s1.session_id in active
        assert s2.session_id not in active

    def test_cancel_session_active(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        assert session_manager.cancel_session(session.session_id) is True
        assert session.state is SessionState.CANCELLED

    def test_cancel_session_not_found(self, session_manager: SessionManager):
        assert session_manager.cancel_session("nonexistent") is False

    def test_cancel_session_already_done(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        session.mark_complete()
        assert session_manager.cancel_session(session.session_id) is False

    async def test_wait_for_completion_succeeds(self, session_manager: SessionManager):
        session = session_manager.create_session("q")

        async def complete_later():
            await asyncio.sleep(0.05)
            session.mark_complete()

        asyncio.get_event_loop().create_task(complete_later())
        result = await session_manager.wait_for_completion(session.session_id, timeout=2.0)
        assert result is True

    async def test_wait_for_completion_timeout(self, session_manager: SessionManager):
        session = session_manager.create_session("q")
        result = await session_manager.wait_for_completion(session.session_id, timeout=0.05)
        assert result is False

    async def test_wait_for_completion_missing(self, session_manager: SessionManager):
        result = await session_manager.wait_for_completion("nope", timeout=0.05)
        assert result is False

    def test_get_all_sessions(self, session_manager: SessionManager):
        session_manager.create_session("q1")
        session_manager.create_session("q2")
        assert len(session_manager.get_all_sessions()) == 2


# ── Enum sanity checks ───────────────────────────────────────────────


class TestEnums:
    def test_reference_source_values(self):
        assert ReferenceSource.TAVILY.value == "tavily"
        assert ReferenceSource.ARXIV.value == "arxiv"
        assert ReferenceSource.KNOWLEDGE_BASE.value == "knowledge_base"

    def test_session_state_values(self):
        assert SessionState.CREATED.value == "created"
        assert SessionState.COMPLETED.value == "completed"

    def test_message_type_values(self):
        assert MessageType.PLAN_REQUEST.value == "plan_request"
        assert MessageType.ERROR.value == "error"
