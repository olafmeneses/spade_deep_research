"""Tests for API route handlers with mocked orchestrator dependencies."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from fastapi.responses import StreamingResponse

from src.api.routes.health import health_check, readiness_check
from src.api.routes.research import (
    create_research_session,
    delete_research_session,
    get_research_session,
    list_research_sessions,
    stop_research_session,
    stream_research_session,
)
from src.api.schemas import ResearchRequest, SessionStatusFilter
from src.api.state import AppState
from src.telemetry import telemetry_registry
from src.session import ResearchSession, SessionManager


def _make_mock_orchestrator(sessions=None):
    mgr = SessionManager()
    if sessions:
        for sid, sess in sessions.items():
            mgr._sessions[sid] = sess

    orch = AsyncMock()
    orch.session_manager = mgr

    async def _start_session(query, chat_sender=None, extensions=None, disable_critic=False):
        return mgr.create_session(query, chat_sender=chat_sender, extensions=extensions, disable_critic=disable_critic)

    async def _get_session(sid):
        return mgr.get_session(sid)

    async def _stop_session(sid):
        return mgr.cancel_session(sid)

    async def _get_active_sessions():
        return mgr.get_active_sessions()

    orch.start_session = _start_session
    orch.get_session = _get_session
    orch.stop_session = _stop_session
    orch.get_active_sessions = _get_active_sessions
    return orch


@pytest.fixture()
def orchestrator():
    return _make_mock_orchestrator()


@pytest.fixture()
def app_state_ready(orchestrator):
    state = AppState()
    state.orchestrator = orchestrator
    return state


class TestCreateResearch:
    async def test_create_session(self, orchestrator):
        resp = await create_research_session(ResearchRequest(query="What is AI?"), Response(), orchestrator, None)
        assert resp.created is True
        assert resp.status == "created"
        assert resp.session_id

    async def test_idempotency_key_creates_once(self, orchestrator):
        from src.api.routes import research as research_routes

        original_state = research_routes.app_state
        state = AppState()
        try:
            research_routes.app_state = state
            response1 = Response()
            r1 = await create_research_session(ResearchRequest(query="Q"), response1, orchestrator, "unique-key-1")
            response2 = Response()
            r2 = await create_research_session(ResearchRequest(query="Q"), response2, orchestrator, "unique-key-1")
        finally:
            research_routes.app_state = original_state

        assert r1.created is True
        assert r2.created is False
        assert r2.session_id == r1.session_id
        assert response2.status_code == 200


class TestListResearch:
    async def test_list_empty(self, orchestrator):
        resp = await list_research_sessions(orchestrator, limit=20, offset=0, status=SessionStatusFilter.ALL)
        assert resp.total == 0
        assert resp.sessions == []

    async def test_list_after_create(self, orchestrator):
        await orchestrator.start_session("Q1")
        await orchestrator.start_session("Q2")
        resp = await list_research_sessions(orchestrator, limit=20, offset=0, status=SessionStatusFilter.ALL)
        assert resp.total == 2

    async def test_list_with_status_filter(self, orchestrator):
        s1 = orchestrator.session_manager.create_session("q1")
        s2 = orchestrator.session_manager.create_session("q2")
        s2.mark_complete()
        resp = await list_research_sessions(orchestrator, limit=20, offset=0, status=SessionStatusFilter.COMPLETED)
        assert resp.total == 1
        assert resp.sessions[0].status == "completed"

    async def test_list_pagination(self, orchestrator):
        for i in range(5):
            orchestrator.session_manager.create_session(f"q{i}")
        resp = await list_research_sessions(orchestrator, limit=2, offset=0, status=SessionStatusFilter.ALL)
        assert len(resp.sessions) == 2
        assert resp.has_more is True


class TestGetSession:
    async def test_found(self, orchestrator):
        session = orchestrator.session_manager.create_session("test query")
        resp = await get_research_session(session.session_id, orchestrator)
        assert resp.query == "test query"

    async def test_not_found(self, orchestrator):
        with pytest.raises(HTTPException) as exc:
            await get_research_session("nonexistent-id", orchestrator)
        assert exc.value.status_code == 404


class TestStopSession:
    async def test_stop_active(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        resp = await stop_research_session(session.session_id, orchestrator)
        assert resp.status == "cancelled"

    async def test_stop_already_completed(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        session.mark_complete()
        with pytest.raises(HTTPException) as exc:
            await stop_research_session(session.session_id, orchestrator)
        assert exc.value.status_code == 400

    async def test_stop_not_found(self, orchestrator):
        with pytest.raises(HTTPException) as exc:
            await stop_research_session("nope", orchestrator)
        assert exc.value.status_code == 404


class TestDeleteSession:
    async def test_delete_completed(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        session.mark_complete()
        await delete_research_session(session.session_id, orchestrator)
        assert orchestrator.session_manager.get_session(session.session_id) is None

    async def test_delete_keeps_archived_artifacts(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        session.current_report = "# Report"
        session.mark_complete()
        archive_path = telemetry_registry.archive_store.root / session.session_id / "session.json"
        assert archive_path.exists()

        await delete_research_session(session.session_id, orchestrator)
        assert archive_path.exists()

    async def test_delete_active_rejected(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        with pytest.raises(HTTPException) as exc:
            await delete_research_session(session.session_id, orchestrator)
        assert exc.value.status_code == 400

    async def test_delete_not_found(self, orchestrator):
        with pytest.raises(HTTPException) as exc:
            await delete_research_session("nope", orchestrator)
        assert exc.value.status_code == 404


class TestHealth:
    async def test_healthy(self, app_state_ready):
        resp = await health_check(app_state_ready)
        assert resp.status == "healthy"

    async def test_starting(self, orchestrator):
        state = AppState()
        resp = await health_check(state)
        assert resp.status == "starting"


class TestReady:
    async def test_ready(self, app_state_ready):
        resp = await readiness_check(app_state_ready)
        assert resp["ready"] is True

    async def test_not_ready(self):
        resp = await readiness_check(AppState())
        assert resp["ready"] is False


class TestStreamSSE:
    async def test_stream_completed_session(self, orchestrator):
        session = orchestrator.session_manager.create_session("q")
        session.current_report = "# Report"
        session.mark_complete()

        resp = await stream_research_session(session.session_id, orchestrator)
        assert isinstance(resp, StreamingResponse)
        body = ""
        async for chunk in resp.body_iterator:
            body += chunk if isinstance(chunk, str) else chunk.decode("utf-8")
        assert "event: connected" in body
        assert "event: completed" in body

    async def test_stream_not_found(self, orchestrator):
        with pytest.raises(HTTPException) as exc:
            await stream_research_session("nope", orchestrator)
        assert exc.value.status_code == 404
