"""Tests for critic review routing in the session workflow."""

import json
from types import SimpleNamespace

import pytest
from spade.message import Message

from src.config import prompts
from src.config.settings import settings
from src.session import MessageType, ReferenceSource
from src.orchestrator import SessionWorkflowBehaviour


def _review_behaviour(sample_session, review_payload):
    behaviour = SessionWorkflowBehaviour.__new__(SessionWorkflowBehaviour)
    behaviour.session = sample_session
    behaviour.MAX_RETRIES = 1
    behaviour.agent_config = {"critic_jid": "critic@localhost"}
    behaviour.sent_prompts = []

    sample_session.current_report = (
        "# Executive Summary\n\n"
        "This is a short draft with one supported claim.\n\n"
        "## Conclusion\n\n"
        "It concludes."
    )
    sample_session.references = []

    async def send_request(jid, prompt, message_type):
        behaviour.sent_prompts.append(prompt)

    async def wait_for_response(message_types, timeout, sender):
        return SimpleNamespace(body=json.dumps(review_payload))

    behaviour.send_request = send_request
    behaviour.wait_for_response = wait_for_response
    return behaviour


@pytest.mark.asyncio
async def test_wait_for_response_ignores_same_session_wrong_sender(sample_session):
    behaviour = SessionWorkflowBehaviour.__new__(SessionWorkflowBehaviour)
    behaviour.session = sample_session
    behaviour.check_cancellation = lambda: None

    wrong = Message(
        sender="coordinator@localhost",
        to="orchestrator@localhost",
        thread=sample_session.session_id,
    )
    wrong.body = "late coordinator message"
    wrong.set_metadata("message_type", MessageType.LLM)
    wrong.set_metadata("session_id", sample_session.session_id)

    right = Message(
        sender="writer@localhost",
        to="orchestrator@localhost",
        thread=sample_session.session_id,
    )
    right.body = "writer report"
    right.set_metadata("message_type", MessageType.LLM)
    right.set_metadata("session_id", sample_session.session_id)

    responses = [wrong, right]

    async def receive(timeout=None):
        return responses.pop(0) if responses else None

    behaviour.receive = receive

    response = await behaviour.wait_for_response(
        [MessageType.WRITE_RESPONSE],
        timeout=1.0,
        from_jid="writer@localhost",
    )

    assert response is right


@pytest.mark.asyncio
async def test_sufficient_review_with_writing_improvements_routes_to_rewrite(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "SUFFICIENT",
            "feedback": "Strong enough, but improve presentation.",
            "issues": [],
            "missing_information": [],
            "writing_improvements": ["Add a synthesis table from existing evidence."],
        },
    )

    result = await behaviour.review_phase()

    assert result == "needs_rewrite"
    assert sample_session._pending_writing_feedback == [
        "Add a synthesis table from existing evidence."
    ]


@pytest.mark.asyncio
async def test_sufficient_review_with_missing_information_routes_to_research_and_keeps_rewrite_feedback(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "SUFFICIENT",
            "feedback": "Mostly strong, with one research gap and one rewrite fix.",
            "issues": [],
            "missing_information": ["Find current market-size estimates."],
            "writing_improvements": ["Make headings more finding-focused."],
        },
    )

    result = await behaviour.review_phase()

    assert result == "needs_research"
    assert sample_session.current_plan["research_questions"][0]["question"] == (
        "Find current market-size estimates."
    )
    assert sample_session._pending_writing_feedback == [
        "Make headings more finding-focused."
    ]


@pytest.mark.asyncio
async def test_review_prompt_includes_writer_requirements_and_report_metrics(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "SUFFICIENT",
            "feedback": "Approved.",
            "issues": [],
            "missing_information": [],
            "writing_improvements": [],
        },
    )

    result = await behaviour.review_phase()

    assert result == "approved"
    prompt = behaviour.sent_prompts[0]
    assert "Writer Requirements To Check" not in prompt
    assert "TASK ALIGNMENT AND COMPLETENESS" in prompts.CRITIC_SYSTEM_PROMPT
    assert "ANALYTICAL DEPTH AND SYNTHESIS" in prompts.CRITIC_SYSTEM_PROMPT
    assert "report-level directives and fine-grained fixes" in prompts.CRITIC_SYSTEM_PROMPT
    assert "larger structural, analytical, or explanatory weaknesses" in prompts.CRITIC_SYSTEM_PROMPT
    assert "central sections developed with enough explanation" in prompts.CRITIC_SYSTEM_PROMPT
    assert "table-only or bullet-only section" in prompts.CRITIC_SYSTEM_PROMPT
    assert "1,800-2,200 words for a broad task" in prompts.CRITIC_SYSTEM_PROMPT
    assert "senior developmental editor" in prompts.CRITIC_SYSTEM_PROMPT
    assert "8-15 actionable items" in prompts.CRITIC_SYSTEM_PROMPT
    assert "Actual Report Metrics" in prompt
    assert "- word_count:" in prompt
    assert "- citation_marker_count:" in prompt


@pytest.mark.asyncio
async def test_pending_rewrite_prompt_is_incremental_and_includes_revision_playbook(sample_session):
    behaviour = SessionWorkflowBehaviour.__new__(SessionWorkflowBehaviour)
    behaviour.session = sample_session
    behaviour.MAX_RETRIES = 1
    behaviour.agent_config = {"writer_jid": "writer@localhost"}
    behaviour.sent_prompts = []

    sample_session.research_context = "Collected evidence."
    sample_session.current_report = "# Previous draft"
    sample_session._pending_writing_feedback = ["Deepen the comparison."]

    async def send_request(jid, prompt, message_type):
        behaviour.sent_prompts.append((prompt, message_type))

    async def wait_for_response(message_types, timeout, sender):
        return SimpleNamespace(body="# Revised report")

    behaviour.send_request = send_request
    behaviour.wait_for_response = wait_for_response

    assert await behaviour.writing_phase()
    prompt, message_type = behaviour.sent_prompts[0]
    assert message_type is MessageType.WRITE_REQUEST
    assert "PASS 1 - REQUIREMENT AUDIT BEFORE WRITING" in prompt
    assert "PASS 5 - EVIDENCE AND CITATION DISCIPLINE" in prompt
    assert "PASS 6 - LENGTH, DENSITY, AND PRESERVATION" in prompt
    assert "Rebuild thin major sections into fully explained sections" in prompt
    assert "REPORT DEPTH AND DEVELOPMENT REQUIREMENTS" in prompt
    assert "Every important table needs surrounding prose" in prompt
    assert "what is changing, why it is changing" in prompt
    assert "Deepen the comparison." in prompt
    assert "Revision Instruction" in prompt
    assert "previous report is already available in the conversation context" in prompt
    assert "Previous Draft:" not in prompt
    assert "# Previous draft" not in prompt


@pytest.mark.asyncio
async def test_review_schema_issues_are_forwarded_to_writer_feedback(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "INSUFFICIENT",
            "feedback": "Needs stronger analysis.",
            "issues": ["Conclusion does not answer the task."],
            "missing_information": [],
            "writing_improvements": ["Conclusion does not answer the task.", "Add evidence discipline."],
        },
    )

    result = await behaviour.review_phase()

    assert result == "needs_rewrite"
    assert sample_session._pending_writing_feedback == [
        "Conclusion does not answer the task.",
        "Add evidence discipline.",
    ]


@pytest.mark.asyncio
async def test_insufficient_review_with_only_feedback_routes_to_rewrite(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "INSUFFICIENT",
            "feedback": "The report is too generic and does not address the requested comparison.",
            "issues": [],
            "missing_information": [],
            "writing_improvements": [],
        },
    )

    result = await behaviour.review_phase()

    assert result == "needs_rewrite"
    assert sample_session._pending_writing_feedback == [
        "The report is too generic and does not address the requested comparison."
    ]


@pytest.mark.asyncio
async def test_critic_context_is_cleared_before_review_when_manager_exists(sample_session):
    behaviour = _review_behaviour(
        sample_session,
        {
            "status": "SUFFICIENT",
            "feedback": "Approved.",
            "issues": [],
            "missing_information": [],
            "writing_improvements": [],
        },
    )

    class FakeContext:
        def __init__(self):
            self.cleared = []

        def clear_conversation(self, session_id):
            self.cleared.append(session_id)

    fake_context = FakeContext()
    fake_critic = SimpleNamespace(
        context=fake_context,
        _session_map={"writer@localhost": sample_session.session_id, "other": "other-session"},
    )
    fake_manager = SimpleNamespace(get_agent=lambda role: fake_critic if role == "critic" else None)
    behaviour.agent_manager = fake_manager

    result = await behaviour.review_phase()

    assert result == "approved"
    assert fake_context.cleared == [sample_session.session_id]
    assert fake_critic._session_map == {"other": "other-session"}


@pytest.mark.asyncio
async def test_followup_research_rewrite_prompt_includes_gaps_new_findings_and_new_references_only(sample_session):
    behaviour = SessionWorkflowBehaviour.__new__(SessionWorkflowBehaviour)
    behaviour.session = sample_session
    behaviour.MAX_RETRIES = 1
    behaviour.agent_config = {"writer_jid": "writer@localhost"}
    behaviour.sent_prompts = []

    sample_session.current_report = "# Existing report with [1]"
    sample_session.research_context = "New evidence about adoption barriers."
    sample_session._pending_research_gaps = ["Find adoption barrier evidence."]
    sample_session._latest_research_reference_offset = 2
    sample_session.references = []
    sample_session.add_reference("https://example.com/one", ReferenceSource.TAVILY, "Original One")
    sample_session.add_reference("https://example.com/two", ReferenceSource.TAVILY, "Original Two")
    sample_session.add_reference("https://example.com/three", ReferenceSource.TAVILY, "New Three")

    async def send_request(jid, prompt, message_type):
        behaviour.sent_prompts.append((prompt, message_type))

    async def wait_for_response(message_types, timeout, sender):
        return SimpleNamespace(body="# Revised report")

    behaviour.send_request = send_request
    behaviour.wait_for_response = wait_for_response

    assert await behaviour.writing_phase()
    prompt, _ = behaviour.sent_prompts[0]
    assert "REPORT DEPTH AND DEVELOPMENT REQUIREMENTS" in prompt
    assert "Previous Research Gaps" in prompt
    assert "Find adoption barrier evidence." in prompt
    assert "New Research Findings" in prompt
    assert "New evidence about adoption barriers." in prompt
    assert "New References" in prompt
    assert "[3] New Three - https://example.com/three" in prompt
    assert "[1] Original One" not in prompt
    assert "[2] Original Two" not in prompt
    assert "# Existing report" not in prompt


def _workflow_behaviour(sample_session, review_results):
    behaviour = SessionWorkflowBehaviour.__new__(SessionWorkflowBehaviour)
    behaviour.session = sample_session
    behaviour.agent_config = {}
    behaviour.input_func = None
    behaviour.agent = None
    behaviour.calls = {
        "planning": 0,
        "validation": 0,
        "research": 0,
        "writing": 0,
        "review": 0,
    }

    async def planning_phase():
        behaviour.calls["planning"] += 1
        sample_session.current_plan = {"research_questions": []}
        return True

    async def validation_phase():
        behaviour.calls["validation"] += 1
        return True

    async def research_phase():
        behaviour.calls["research"] += 1
        sample_session.research_context = f"research pass {behaviour.calls['research']}"
        return True

    async def writing_phase():
        behaviour.calls["writing"] += 1
        sample_session.current_report = f"# Report\n\nUsing {sample_session.research_context}."
        return True

    async def review_phase():
        behaviour.calls["review"] += 1
        index = behaviour.calls["review"] - 1
        return review_results[index] if index < len(review_results) else "approved"

    behaviour.check_cancellation = lambda: None
    behaviour.planning_phase = planning_phase
    behaviour.validation_phase = validation_phase
    behaviour.research_phase = research_phase
    behaviour.writing_phase = writing_phase
    behaviour.review_phase = review_phase
    return behaviour


@pytest.mark.asyncio
async def test_zero_critic_iterations_writes_once_without_review(sample_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CRITIC_ITERATIONS", 0)
    behaviour = _workflow_behaviour(sample_session, ["needs_rewrite"])

    await behaviour.run()

    assert behaviour.calls["research"] == 1
    assert behaviour.calls["writing"] == 1
    assert behaviour.calls["review"] == 0
    assert sample_session.current_report == "# Report\n\nUsing research pass 1."


@pytest.mark.asyncio
async def test_research_feedback_loop_is_followed_by_rewrite(sample_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CRITIC_ITERATIONS", 1)
    behaviour = _workflow_behaviour(sample_session, ["needs_research", "approved"])

    await behaviour.run()

    assert behaviour.calls["research"] == 2
    assert behaviour.calls["writing"] == 2
    assert behaviour.calls["review"] == 2
    assert sample_session.current_report == "# Report\n\nUsing research pass 2."


@pytest.mark.asyncio
async def test_rewrite_feedback_uses_same_critic_iteration_budget(sample_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CRITIC_ITERATIONS", 2)
    behaviour = _workflow_behaviour(
        sample_session,
        ["needs_rewrite", "needs_rewrite", "approved"],
    )

    await behaviour.run()

    assert behaviour.calls["research"] == 1
    assert behaviour.calls["writing"] == 3
    assert behaviour.calls["review"] == 3
