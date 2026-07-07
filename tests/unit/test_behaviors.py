"""Tests for src.behaviors — session-aware message utilities."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

from spade.message import Message
from spade.template import Template

from src.behaviors import (
    _message_session_id,
    create_session_metadata,
    create_response_message,
    create_session_template,
    _set_session_id_on_tools,
    _collect_tools,
    apply_session_tracking,
)


# ── create_session_metadata ──────────────────────────────────────────


class TestCreateSessionMetadata:
    def test_keys_present(self):
        meta = create_session_metadata("s1", "plan_request")
        assert meta["session_id"] == "s1"
        assert meta["message_type"] == "plan_request"
        assert "timestamp" in meta

    def test_timestamp_is_iso(self):
        meta = create_session_metadata("s1", "plan_request")
        # Should not raise
        datetime.fromisoformat(meta["timestamp"])


# ── create_response_message ──────────────────────────────────────────


class TestCreateResponseMessage:
    def test_message_fields(self):
        msg = create_response_message("user@localhost", "hello", "s1", "plan_response")
        assert str(msg.to) == "user@localhost"
        assert msg.body == "hello"
        assert msg.thread == "s1"
        assert msg.get_metadata("session_id") == "s1"
        assert msg.get_metadata("message_type") == "plan_response"


# ── create_session_template ──────────────────────────────────────────


class TestCreateSessionTemplate:
    def test_with_both(self):
        tmpl = create_session_template(session_id="s1", message_type="plan_request")
        assert isinstance(tmpl, Template)

    def test_session_only(self):
        tmpl = create_session_template(session_id="s1")
        assert isinstance(tmpl, Template)

    def test_empty(self):
        tmpl = create_session_template()
        assert isinstance(tmpl, Template)


# ── _set_session_id_on_tools ─────────────────────────────────────────


class TestSetSessionIdOnTools:
    def test_sets_on_session_aware_tools(self):
        tool1 = MagicMock(spec=["set_session_id"])
        tool2 = MagicMock(spec=[])  # no set_session_id
        updated = _set_session_id_on_tools([tool1, tool2], "s1")
        assert updated == 1
        tool1.set_session_id.assert_called_once_with("s1")

    def test_empty_tools(self):
        assert _set_session_id_on_tools([], "s1") == 0

    def test_none_tools(self):
        assert _set_session_id_on_tools(None, "s1") == 0

    def test_empty_session_id(self):
        assert _set_session_id_on_tools([MagicMock()], "") == 0


# ── _collect_tools ───────────────────────────────────────────────────


class TestCollectTools:
    def test_deduplication(self):
        shared_tool = MagicMock()
        agent = MagicMock()
        agent.tools = [shared_tool]
        behaviour = MagicMock()
        behaviour.tools = [shared_tool]
        collected = _collect_tools(agent, behaviour)
        assert len(collected) == 1

    def test_merge_unique(self):
        t1 = MagicMock()
        t2 = MagicMock()
        agent = MagicMock()
        agent.tools = [t1]
        behaviour = MagicMock()
        behaviour.tools = [t2]
        collected = _collect_tools(agent, behaviour)
        assert len(collected) == 2

    def test_none_sources(self):
        agent = MagicMock(spec=[])  # no tools attr
        behaviour = MagicMock(spec=[])
        collected = _collect_tools(agent, behaviour)
        assert collected == []


class TestMessageSessionId:
    def test_prefers_metadata(self):
        msg = Message()
        msg.thread = "thread-session"
        msg.set_metadata("session_id", "meta-session")
        assert _message_session_id(msg) == "meta-session"

    def test_falls_back_to_thread(self):
        msg = Message()
        msg.thread = "thread-session"
        assert _message_session_id(msg) == "thread-session"


class TestApplySessionTracking:
    def test_send_preserves_existing_thread_session(self):
        sent_messages = []

        async def original_receive(timeout=None):
            return None

        async def original_send(msg):
            sent_messages.append(msg)

        behaviour = MagicMock()
        behaviour.receive = original_receive
        behaviour.send = original_send
        behaviour.tools = []

        agent = MagicMock()
        agent.jid = "agent@localhost"
        agent.llm_behaviour = behaviour
        agent._session_map = {"recipient@localhost": "wrong-session"}
        agent.tools = []

        assert apply_session_tracking(agent)

        msg = Message(to="recipient@localhost")
        msg.thread = "right-session"

        asyncio.run(behaviour.send(msg))

        assert len(sent_messages) == 1
        assert sent_messages[0].thread == "right-session"
        assert sent_messages[0].get_metadata("session_id") == "right-session"

    def test_receive_recovers_session_from_thread(self):
        incoming = Message(sender="sender@localhost")
        incoming.thread = "thread-session"

        async def original_receive(timeout=None):
            return incoming

        async def original_send(msg):
            return None

        behaviour = MagicMock()
        behaviour.receive = original_receive
        behaviour.send = original_send
        behaviour.tools = []

        agent = MagicMock()
        agent.jid = "agent@localhost"
        agent.llm_behaviour = behaviour
        agent._session_map = {}
        agent.tools = []
        agent._current_session_id = None

        assert apply_session_tracking(agent)

        received = asyncio.run(behaviour.receive())

        assert received is incoming
        assert agent._session_map["sender@localhost"] == "thread-session"
        assert agent._current_session_id == "thread-session"
