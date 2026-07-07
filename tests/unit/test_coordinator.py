"""Unit tests for session-aware coordinator tools."""

import asyncio
from types import SimpleNamespace

import pytest
from spade.message import Message
from spade_llm.routing.types import RoutingResponse

from src.agents.coordinator import SessionAwareCoordinatorAgent
from src.budgets import SessionBudgetRegistry
from src.config.prompts import COORDINATOR_PROMPT_TEMPLATE


class _FakeBehaviour:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)

    async def receive(self, timeout=None):
        if self.responses:
            return self.responses.pop(0)
        return None


class _FakeContext:
    def __init__(self):
        self.added_messages = []
        self.coordination_session = ""
        self._current_conversation_id = None

    def add_message(self, message, conversation_id=None):
        self.added_messages.append((message, conversation_id))


@pytest.mark.asyncio
async def test_parallel_tool_keeps_subagent_replies_out_of_llm_context():
    response = Message(
        sender="researcher@localhost",
        to="coordinator@localhost",
        thread="session-1",
    )
    response.body = "Collected findings"

    fake_agent = SimpleNamespace(
        subagent_ids={"researcher@localhost"},
        max_parallel=3,
        subagent_response_timeout=1.0,
        agent_status={"researcher@localhost": "idle"},
        llm_behaviour=_FakeBehaviour([response]),
        context=_FakeContext(),
        _get_active_session_id=lambda: "session-1",
        _deadline_message=lambda session_id: None,
        _remaining_wait_budget=lambda session_id: 1.0,
        _register_launches=lambda session_id, requested_launches: None,
        _mark_pending_exhausted=SessionAwareCoordinatorAgent._mark_pending_exhausted,
    )

    tool = SessionAwareCoordinatorAgent._create_send_to_agents_parallel_tool(fake_agent)

    result = await tool.execute(
        tasks=[
            {
                "agent_id": "researcher@localhost",
                "message": "Research this",
            }
        ]
    )

    assert "Response from researcher@localhost: Collected findings" in result
    assert fake_agent.context.added_messages == []


@pytest.mark.asyncio
async def test_complete_task_accepts_model_supplied_summary():
    closed_sessions = set()
    fake_agent = SimpleNamespace(
        _task_completed=False,
        _get_active_session_id=lambda: "session-1",
        _close_to_subagent_replies=lambda session_id: closed_sessions.add(session_id),
    )

    tool = SessionAwareCoordinatorAgent._create_complete_task_tool(fake_agent)

    result = await tool.execute(findings_summary="Final evidence synthesis")

    assert fake_agent._task_completed is True
    assert closed_sessions == {"session-1"}
    assert "Final evidence synthesis" in result


@pytest.mark.asyncio
async def test_complete_task_ignores_model_summary_in_raw_findings_mode():
    closed_sessions = set()
    fake_agent = SimpleNamespace(
        _task_completed=False,
        return_raw_findings=True,
        _get_active_session_id=lambda: "session-1",
        _close_to_subagent_replies=lambda session_id: closed_sessions.add(session_id),
    )

    tool = SessionAwareCoordinatorAgent._create_complete_task_tool(fake_agent)

    result = await tool.execute(summary="LLM summary that should not be used")

    assert fake_agent._task_completed is True
    assert closed_sessions == {"session-1"}
    assert "LLM summary" not in result
    assert "raw findings" in result.lower()


def test_raw_findings_routing_replaces_llm_text_with_compiled_bundle():
    agent = SessionAwareCoordinatorAgent.__new__(SessionAwareCoordinatorAgent)
    agent.return_raw_findings = True
    agent.subagent_ids = {"tavily_1@localhost"}
    agent._original_requester = "orchestrator@localhost"
    agent._task_completed = True
    agent.jid = "coordinator@localhost"
    agent.coordination_session = "session-1"
    agent._coordination_turn_by_session = {"session-1": 1}
    agent._raw_findings_by_turn = {}
    agent._record_subagent_response(
        "session-1",
        "tavily_1@localhost",
        "Find market data",
        "Raw source-backed findings",
    )

    route = agent._create_coordination_routing()
    msg = Message(sender="coordinator@localhost", to="coordinator@localhost", thread="session-1")

    result = route(msg, "LLM summary text", {})

    assert isinstance(result, RoutingResponse)
    assert result.recipients == "orchestrator@localhost"
    routed_body = result.transform("LLM summary text")
    assert "Unsummarized Subagent Research Evidence" in routed_body
    assert "Raw source-backed findings" in routed_body
    assert "LLM summary text" not in routed_body


@pytest.mark.asyncio
async def test_complete_task_sends_raw_findings_immediately_to_requester():
    agent = SessionAwareCoordinatorAgent.__new__(SessionAwareCoordinatorAgent)
    agent.return_raw_findings = True
    agent._task_completed = False
    agent._original_requester = "orchestrator@localhost"
    agent.coordination_session = "session-1"
    agent._coordination_turn_by_session = {"session-1": 1}
    agent._raw_findings_by_turn = {}
    agent._raw_findings_sent_sessions = set()
    agent._closed_to_subagent_replies = set()
    agent.llm_behaviour = _FakeBehaviour([])
    agent._record_subagent_response(
        "session-1",
        "tavily_1@localhost",
        "Find market data",
        "Raw source-backed findings",
    )

    tool = agent._create_complete_task_tool()

    result = await tool.execute()

    assert "have been returned" in result
    assert agent._task_completed is False
    assert agent._original_requester is None
    assert agent._raw_findings_sent_sessions == {"session-1"}
    assert len(agent.llm_behaviour.sent) == 1
    sent = agent.llm_behaviour.sent[0]
    assert str(sent.to) == "orchestrator@localhost"
    assert sent.get_metadata("request_type") == "research_response"
    assert "Unsummarized Subagent Research Evidence" in sent.body
    assert "Raw source-backed findings" in sent.body


@pytest.mark.asyncio
async def test_raw_findings_records_sequential_and_parallel_responses():
    sequential_response = Message(
        sender="tavily_1@localhost",
        to="coordinator@localhost",
        thread="session-1",
    )
    sequential_response.body = "Sequential findings"
    parallel_response = Message(
        sender="arxiv_1@localhost",
        to="coordinator@localhost",
        thread="session-1",
    )
    parallel_response.body = "Parallel findings"

    agent = SessionAwareCoordinatorAgent.__new__(SessionAwareCoordinatorAgent)
    agent.return_raw_findings = True
    agent.subagent_ids = {"tavily_1@localhost", "arxiv_1@localhost"}
    agent.max_parallel = 2
    agent.subagent_response_timeout = 1.0
    agent.agent_status = {"tavily_1@localhost": "idle", "arxiv_1@localhost": "idle"}
    agent.llm_behaviour = _FakeBehaviour([sequential_response, parallel_response])
    agent.coordination_session = "session-1"
    agent._coordination_turn_by_session = {}
    agent._raw_findings_by_turn = {}
    agent._deadline_message = lambda session_id: None
    agent._remaining_wait_budget = lambda session_id: 1.0
    agent._register_launches = lambda session_id, requested_launches: None

    sequential_tool = agent._create_send_to_agent_tool()
    parallel_tool = agent._create_send_to_agents_parallel_tool()

    await sequential_tool.execute(agent_id="tavily_1@localhost", message="Find current evidence")
    await parallel_tool.execute(
        tasks=[{"agent_id": "arxiv_1@localhost", "message": "Find academic evidence"}]
    )

    bundle = agent._format_raw_findings_bundle("session-1")
    assert "Research Response 1 (tavily)" in bundle
    assert "Find current evidence" in bundle
    assert "Sequential findings" in bundle
    assert "Research Response 2 (arxiv)" in bundle
    assert "Find academic evidence" in bundle
    assert "Parallel findings" in bundle


def test_raw_findings_follow_up_research_starts_fresh_bundle():
    agent = SessionAwareCoordinatorAgent.__new__(SessionAwareCoordinatorAgent)
    agent.return_raw_findings = True
    agent._coordination_turn_by_session = {}
    agent._raw_findings_by_turn = {}
    agent._closed_to_subagent_replies = set()

    agent._record_subagent_response("session-1", "tavily_1@localhost", "Initial task", "Old findings")
    agent._close_to_subagent_replies("session-1")
    agent._reopen_for_external_request("session-1")
    agent._record_subagent_response("session-1", "arxiv_1@localhost", "Follow-up task", "New findings")

    bundle = agent._format_raw_findings_bundle("session-1")
    assert "New findings" in bundle
    assert "Follow-up task" in bundle
    assert "Old findings" not in bundle


@pytest.mark.asyncio
async def test_parallel_tool_rejects_duplicate_agent_tasks():
    fake_agent = SimpleNamespace(
        subagent_ids={"researcher@localhost"},
        max_parallel=3,
        subagent_response_timeout=1.0,
        agent_status={"researcher@localhost": "idle"},
        llm_behaviour=_FakeBehaviour([]),
        context=_FakeContext(),
        _get_active_session_id=lambda: "session-1",
        _deadline_message=lambda session_id: None,
        _remaining_wait_budget=lambda session_id: 1.0,
        _register_launches=lambda session_id, requested_launches: None,
        _mark_pending_exhausted=SessionAwareCoordinatorAgent._mark_pending_exhausted,
    )

    tool = SessionAwareCoordinatorAgent._create_send_to_agents_parallel_tool(fake_agent)

    result = await tool.execute(
        tasks=[
            {"agent_id": "researcher@localhost", "message": "Research this"},
            {"agent_id": "researcher@localhost", "message": "Research that"},
        ]
    )

    assert "multiple tasks for researcher@localhost" in result
    assert fake_agent.llm_behaviour.sent == []


@pytest.mark.asyncio
async def test_completed_turn_ignores_late_subagent_reply_and_reopens_on_external_request():
    late_reply = Message(
        sender="researcher@localhost",
        to="coordinator@localhost",
        thread="session-1",
    )
    late_reply.body = "Late findings"

    external_request = Message(
        sender="orchestrator@localhost",
        to="coordinator@localhost",
        thread="session-1",
    )
    external_request.body = "More research needed"

    fake_agent = SimpleNamespace(
        jid="coordinator@localhost",
        llm_behaviour=_FakeBehaviour([late_reply, external_request]),
        context=_FakeContext(),
        _session_map={},
        _subagent_ids={"researcher@localhost"},
        _closed_to_subagent_replies={"session-1"},
        coordination_session="session-1",
        _set_coordination_session=lambda session_id: None,
        _get_session_started_at=lambda session_id: 0.0,
    )
    fake_agent._reopen_for_external_request = (
        lambda session_id: fake_agent._closed_to_subagent_replies.discard(session_id)
    )

    SessionAwareCoordinatorAgent._apply_session_aware_tracking(fake_agent)

    assert await fake_agent.llm_behaviour.receive(timeout=0) is None
    assert await fake_agent.llm_behaviour.receive(timeout=0) is external_request
    assert fake_agent._closed_to_subagent_replies == set()


def test_coordinator_delegation_budget_resets_per_coordination_turn():
    SessionBudgetRegistry.reset_all()
    fake_agent = SimpleNamespace(
        max_delegations=1,
        _coordination_turn_by_session={},
    )
    fake_agent._get_coordination_budget_key = (
        lambda session_id: SessionAwareCoordinatorAgent._get_coordination_budget_key(fake_agent, session_id)
    )

    assert SessionAwareCoordinatorAgent._register_launches(fake_agent, "session-1", 1) is None
    exhausted = SessionAwareCoordinatorAgent._register_launches(fake_agent, "session-1", 1)
    assert "delegation budget exhausted" in exhausted.lower()

    fake_agent._closed_to_subagent_replies = {"session-1"}
    SessionAwareCoordinatorAgent._reopen_for_external_request(fake_agent, "session-1")

    assert SessionAwareCoordinatorAgent._register_launches(fake_agent, "session-1", 1) is None


@pytest.mark.asyncio
async def test_coordinator_timeouts_reset_per_coordination_turn():
    agent = SessionAwareCoordinatorAgent.__new__(SessionAwareCoordinatorAgent)
    agent.soft_timeout = 1.0
    agent.hard_timeout = None
    agent.return_raw_findings = False
    agent._session_started_at = {}
    agent._coordination_turn_by_session = {}
    agent._closed_to_subagent_replies = set()
    agent._raw_findings_by_turn = {}

    first_turn_key = agent._get_coordination_timeout_key("session-1")
    agent._session_started_at[first_turn_key] = asyncio.get_running_loop().time() - 2.0

    assert "soft timeout reached" in agent._deadline_message("session-1").lower()

    agent._close_to_subagent_replies("session-1")
    agent._reopen_for_external_request("session-1")

    assert agent._get_coordination_timeout_key("session-1") == "session-1:coordination:2"
    assert agent._deadline_message("session-1") is None


def test_coordinator_prompt_formats_in_both_completion_modes():
    common = {
        "agent_list": "tavily_1@localhost, arxiv_1@localhost",
        "max_parallel": 2,
        "delegation_limit": "unlimited",
    }

    summary_prompt = COORDINATOR_PROMPT_TEMPLATE.format(
        **common,
        completion_mode="SUMMARY MODE: call complete_task with a findings summary.",
    )
    raw_prompt = COORDINATOR_PROMPT_TEMPLATE.format(
        **common,
        completion_mode="RAW FINDINGS MODE: call complete_task() with no arguments.",
    )

    assert "SUMMARY MODE" in summary_prompt
    assert "RAW FINDINGS MODE" in raw_prompt
