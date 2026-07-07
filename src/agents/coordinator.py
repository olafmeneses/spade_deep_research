"""Session-aware Coordinator Agent."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set, Union

from spade.message import Message
from spade_llm.agent.coordinator_agent import CoordinatorAgent as BaseCoordinatorAgent
from spade_llm.providers import LLMProvider
from spade_llm.routing.types import RoutingResponse
from spade_llm.tools import LLMTool

from src.telemetry import telemetry_registry
from src.budgets import SessionBudgetRegistry
from src.config.prompts import COORDINATOR_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


def _message_session_id(message: Message) -> Optional[str]:
    """Extract the session identifier from message metadata or thread."""
    if hasattr(message, "get_metadata"):
        session_id = message.get_metadata("session_id")
        if session_id:
            return session_id
    return getattr(message, "thread", None)


class SessionAwareCoordinatorAgent(BaseCoordinatorAgent):
    """Coordinator with session tracking and configurable parallel limit."""

    def __init__(
        self,
        jid: str,
        password: str,
        subagent_ids: List[str],
        provider: LLMProvider,
        max_parallel: int = 3,
        soft_timeout: Optional[float] = None,
        hard_timeout: Optional[float] = None,
        max_delegations: Optional[int] = None,
        return_raw_findings: bool = False,
        **kwargs,
    ):
        self.return_raw_findings = return_raw_findings
        if "system_prompt" not in kwargs:
            agent_list = ", ".join(subagent_ids)
            delegation_limit = str(max_delegations) if max_delegations is not None else "unlimited"
            kwargs["system_prompt"] = COORDINATOR_PROMPT_TEMPLATE.format(
                agent_list=agent_list,
                max_parallel=max_parallel,
                delegation_limit=delegation_limit,
                completion_mode=(
                    "RAW FINDINGS MODE:\n"
                    "- Do not synthesize or summarize the subagent findings yourself.\n"
                    "- After gathering enough evidence, call complete_task() with no arguments.\n"
                    "- Do not provide final narrative, conclusions, or a post-tool synthesis; "
                    "the system will return the collected raw findings."
                    if return_raw_findings
                    else
                    "SUMMARY MODE:\n"
                    "- After gathering information, call complete_task with a comprehensive findings summary.\n"
                    "- Your final response to the original requester should be that synthesized findings summary."
                ),
            )

        super().__init__(
            jid=jid,
            password=password,
            subagent_ids=subagent_ids,
            provider=provider,
            **kwargs,
        )
        self._session_map: Dict[str, str] = {}
        self._subagent_ids = {jid.split("/")[0] for jid in subagent_ids}
        self.max_parallel = max_parallel
        self.soft_timeout = soft_timeout
        self.hard_timeout = hard_timeout
        self.max_delegations = max_delegations
        self._session_started_at: Dict[str, float] = {}
        self._closed_to_subagent_replies: Set[str] = set()
        self._coordination_turn_by_session: Dict[str, int] = {}
        self._raw_findings_by_turn: Dict[str, Dict[str, Any]] = {}
        self._raw_findings_sent_sessions: Set[str] = set()

    def _create_coordination_routing(self):
        base_routing = super()._create_coordination_routing()

        def coordination_routing(
            msg: Message,
            response: str,
            context: Dict[str, Any],
        ) -> Union[str, RoutingResponse]:
            if not getattr(self, "return_raw_findings", False):
                return base_routing(msg, response, context)

            sender_str = str(msg.sender)
            if sender_str not in self.subagent_ids and self._original_requester is None:
                self._original_requester = sender_str

            if self._task_completed and self._original_requester is not None:
                original = self._original_requester
                bundle = self._format_raw_findings_bundle(self._get_active_session_id())
                self._task_completed = False
                self._original_requester = None
                if not hasattr(self, "_raw_findings_sent_sessions"):
                    self._raw_findings_sent_sessions = set()
                self._raw_findings_sent_sessions.add(self._get_active_session_id())
                return RoutingResponse(recipients=original, transform=lambda _text: bundle)

            if sender_str in self.subagent_ids:
                return str(self.jid)

            return str(msg.sender)

        return coordination_routing

    def _set_coordination_session(self, session_id: str) -> None:
        """Update the coordination session to use the external session_id."""
        if self.coordination_session != session_id:
            logger.info(f"[{self.jid}] Setting coordination session to {session_id[:8]}...")
            self.coordination_session = session_id

            if hasattr(self, "context"):
                if hasattr(self.context, "coordination_session"):
                    self.context.coordination_session = session_id
                self.context._current_conversation_id = session_id

    def _get_active_session_id(self) -> str:
        return self.coordination_session

    def _get_coordination_budget_key(self, session_id: str) -> str:
        turn = self._coordination_turn_by_session.setdefault(session_id, 1)
        return f"{session_id}:coordination:{turn}"

    def _get_coordination_timeout_key(self, session_id: str) -> str:
        return self._get_coordination_budget_key(session_id)

    def _get_findings_bundle(self, session_id: str) -> Dict[str, Any]:
        turn_key = self._get_coordination_budget_key(session_id)
        return self._raw_findings_by_turn.setdefault(
            turn_key,
            {"notices": [], "responses": []},
        )

    def _source_family_for_agent(self, agent_id: str) -> str:
        local = str(agent_id).split("@", 1)[0]
        if "_" in local:
            prefix, suffix = local.rsplit("_", 1)
            if suffix.isdigit():
                return prefix
        return local

    def _record_coordination_notice(self, session_id: str, notice: str) -> None:
        if not getattr(self, "return_raw_findings", False) or not session_id or not notice:
            return
        self._get_findings_bundle(session_id)["notices"].append(notice)

    def _record_subagent_response(self, session_id: str, agent_id: str, task: str, body: str) -> None:
        if not getattr(self, "return_raw_findings", False) or not session_id:
            return
        self._get_findings_bundle(session_id)["responses"].append(
            {
                "source_family": self._source_family_for_agent(agent_id),
                "task": task,
                "body": body or "",
            }
        )

    def _reset_findings_bundle(self, session_id: str) -> None:
        if not session_id:
            return
        turn_key = self._get_coordination_budget_key(session_id)
        self._raw_findings_by_turn[turn_key] = {"notices": [], "responses": []}

    async def _send_raw_findings_to_original_requester(self, session_id: str) -> bool:
        original = getattr(self, "_original_requester", None)
        if not original or not session_id:
            return False

        msg = Message(to=original)
        msg.body = self._format_raw_findings_bundle(session_id)
        msg.thread = session_id
        msg.set_metadata("message_type", "llm")
        msg.set_metadata("request_type", "research_response")
        msg.set_metadata("session_id", session_id)
        await self.llm_behaviour.send(msg)

        self._task_completed = False
        self._original_requester = None
        if not hasattr(self, "_raw_findings_sent_sessions"):
            self._raw_findings_sent_sessions = set()
        self._raw_findings_sent_sessions.add(session_id)
        return True

    def _format_raw_findings_bundle(self, session_id: str) -> str:
        bundle = self._get_findings_bundle(session_id)
        lines = [
            "# Unsummarized Subagent Research Evidence",
            "",
            "This is internal research context collected during the current coordination turn. "
            "It is not a final report or synthesis.",
        ]

        notices = bundle.get("notices", [])
        if notices:
            lines.extend(["", "## Coordination Notices"])
            for notice in notices:
                lines.append(f"- {notice}")

        responses = bundle.get("responses", [])
        lines.extend(["", "## Raw Findings"])
        if responses:
            for index, item in enumerate(responses, start=1):
                lines.extend(
                    [
                        "",
                        f"### Research Response {index} ({item['source_family']})",
                        "",
                        "**Delegated task:**",
                        item["task"],
                        "",
                        "**Raw findings:**",
                        item["body"],
                    ]
                )
        else:
            lines.extend(["", "No subagent findings were collected in this coordination turn."])

        return "\n".join(lines).strip()

    def _close_to_subagent_replies(self, session_id: str) -> None:
        """Ignore late subagent replies for a completed coordination turn."""
        if session_id:
            self._closed_to_subagent_replies.add(session_id)

    def _reopen_for_external_request(self, session_id: str) -> None:
        """Allow a new coordinator turn for the same research session."""
        if session_id:
            if session_id in self._closed_to_subagent_replies:
                self._coordination_turn_by_session[session_id] = (
                    self._coordination_turn_by_session.get(session_id, 1) + 1
                )
                self._closed_to_subagent_replies.discard(session_id)
                if hasattr(self, "_reset_findings_bundle"):
                    self._reset_findings_bundle(session_id)
            else:
                self._coordination_turn_by_session.setdefault(session_id, 1)

    def clear_session_tracking(self, session_id: str) -> None:
        """Drop cached routing state for a completed/cancelled session."""
        self._session_started_at.pop(session_id, None)
        for timeout_key in list(self._session_started_at.keys()):
            if timeout_key.startswith(f"{session_id}:coordination:"):
                self._session_started_at.pop(timeout_key, None)
        self._closed_to_subagent_replies.discard(session_id)
        for turn_key in list(self._raw_findings_by_turn.keys()):
            if turn_key.startswith(f"{session_id}:coordination:"):
                self._raw_findings_by_turn.pop(turn_key, None)
        if hasattr(self, "_raw_findings_sent_sessions"):
            self._raw_findings_sent_sessions.discard(session_id)
        self._coordination_turn_by_session.pop(session_id, None)
        stale_senders = [
            sender for sender, mapped_session_id in self._session_map.items()
            if mapped_session_id == session_id
        ]
        for sender in stale_senders:
            self._session_map.pop(sender, None)

        if self.coordination_session == session_id:
            self.coordination_session = ""
            if hasattr(self, "context"):
                if hasattr(self.context, "coordination_session"):
                    self.context.coordination_session = ""
                self.context._current_conversation_id = None

    def _get_session_started_at(self, session_id: str) -> float:
        timeout_key = self._get_coordination_timeout_key(session_id)
        started_at = self._session_started_at.get(timeout_key)
        if started_at is None:
            started_at = asyncio.get_running_loop().time()
            self._session_started_at[timeout_key] = started_at
        return started_at

    def _seconds_remaining(self, timeout: Optional[float], session_id: str) -> Optional[float]:
        if timeout is None:
            return None
        started_at = self._get_session_started_at(session_id)
        return timeout - (asyncio.get_running_loop().time() - started_at)

    def _deadline_message(self, session_id: str) -> Optional[str]:
        hard_remaining = self._seconds_remaining(self.hard_timeout, session_id)
        if hard_remaining is not None and hard_remaining <= 0:
            if getattr(self, "return_raw_findings", False):
                return (
                    "Coordinator hard timeout reached. Do not launch new work. "
                    "Call complete_task() so the raw findings gathered so far can be returned."
                )
            return (
                "Coordinator hard timeout reached. Do not launch new work. "
                "Return a final synthesis immediately using the evidence already gathered."
            )

        soft_remaining = self._seconds_remaining(self.soft_timeout, session_id)
        if soft_remaining is not None and soft_remaining <= 0:
            if getattr(self, "return_raw_findings", False):
                return (
                    "Coordinator soft timeout reached. Stop launching new work and call complete_task() "
                    "so the raw findings gathered so far can be returned."
                )
            return (
                "Coordinator soft timeout reached. Stop launching new work and synthesize the findings already in context. "
                "If information is missing, state that it remains unresolved."
            )

        return None

    def _remaining_wait_budget(self, session_id: str) -> float:
        limits = [self.subagent_response_timeout]
        for timeout in (self.soft_timeout, self.hard_timeout):
            remaining = self._seconds_remaining(timeout, session_id)
            if remaining is not None:
                limits.append(max(0.0, remaining))
        return min(limits)

    def _mark_pending_exhausted(
        self,
        pending_agents: Dict[str, float],
        responses: Dict[str, str],
        message: str,
    ) -> None:
        for agent_id in list(pending_agents.keys()):
            self.agent_status[agent_id] = "idle"
            responses[agent_id] = message
            del pending_agents[agent_id]

    def _register_launches(self, session_id: str, requested_launches: int) -> Optional[str]:
        budget_key = self._get_coordination_budget_key(session_id)
        allowed, state = SessionBudgetRegistry.try_register_coordinator_launches(
            session_id=budget_key,
            requested_launches=requested_launches,
            max_launches=self.max_delegations,
        )
        if allowed:
            telemetry_registry.record_coordinator_wave(session_id, requested_launches)
            return None

        if getattr(self, "return_raw_findings", False):
            return (
                f"Coordinator delegation budget exhausted ({state.launches}/{self.max_delegations}). "
                "Do not launch new work. Call complete_task() so the raw findings gathered so far can be returned."
            )
        return (
            f"Coordinator delegation budget exhausted ({state.launches}/{self.max_delegations}). "
            "Do not launch new work. Synthesize the evidence already gathered."
        )

    async def setup(self) -> None:
        await super().setup()
        if hasattr(self, "llm_behaviour"):
            self._apply_session_aware_tracking()

    def _apply_session_aware_tracking(self) -> bool:
        """Apply session tracking that also updates the coordination session."""
        behaviour = self.llm_behaviour
        session_map = self._session_map
        agent_jid = str(self.jid)
        context = self.context

        original_add_message = context.add_message

        def patched_add_message(message, conversation_id=None):
            session_id = _message_session_id(message)

            if session_id:
                self._set_coordination_session(session_id)
                self._get_session_started_at(session_id)
                conversation_id = session_id
                logger.debug(f"[{agent_jid}] Context add_message: Using session {session_id[:8]} as conversation_id")

            original_add_message(message, conversation_id)

        context.add_message = patched_add_message

        original_receive = behaviour.receive
        original_send = behaviour.send

        async def patched_receive(timeout=None):
            msg = await original_receive(timeout=timeout)
            if msg:
                session_id = _message_session_id(msg)
                sender = str(msg.sender).split("/")[0]
                if session_id and sender in self._subagent_ids and session_id in self._closed_to_subagent_replies:
                    logger.info(
                        f"[{agent_jid}] Ignoring late subagent reply from {sender} "
                        f"for completed coordination turn {session_id[:8]}"
                    )
                    return None

                if session_id:
                    if sender not in self._subagent_ids:
                        if getattr(self, "return_raw_findings", False) and self._original_requester is None:
                            self._original_requester = sender
                        self._reopen_for_external_request(session_id)
                    session_map[sender] = session_id
                    self._get_session_started_at(session_id)
                    self._set_coordination_session(session_id)
                    logger.debug(f"[{agent_jid}] Captured session {session_id[:8]} from {sender}")
            return msg

        async def patched_send(msg):
            recipient = str(msg.to).split("/")[0]
            session_id = _message_session_id(msg) or session_map.get(recipient) or self.coordination_session
            if session_id:
                self._get_session_started_at(session_id)
                msg.set_metadata("session_id", session_id)
                if not msg.thread:
                    msg.thread = session_id
                logger.debug(f"[{agent_jid}] Added session {session_id[:8]} to {recipient}")
            await original_send(msg)

        behaviour.receive = patched_receive
        behaviour.send = patched_send

        logger.info(f"[{agent_jid}] Session-aware coordination tracking enabled")
        return True

    def _create_send_to_agent_tool(self) -> LLMTool:
        agent = self
        record_notice = getattr(agent, "_record_coordination_notice", lambda *args, **kwargs: None)
        record_response = getattr(agent, "_record_subagent_response", lambda *args, **kwargs: None)

        async def send_to_agent(agent_id: str, message: str) -> str:
            if agent_id not in agent.subagent_ids:
                return f"Error: {agent_id} is not a registered subagent"

            session_id = agent._get_active_session_id()
            deadline_message = agent._deadline_message(session_id)
            if deadline_message:
                record_notice(session_id, deadline_message)
                return deadline_message

            launch_message = agent._register_launches(session_id, 1)
            if launch_message:
                record_notice(session_id, launch_message)
                return launch_message

            msg = Message(to=agent_id)
            msg.set_metadata("message_type", "llm")
            msg.set_metadata("coordination_session", session_id)
            msg.thread = session_id
            msg.body = message

            await agent.llm_behaviour.send(msg)
            agent.agent_status[agent_id] = "working"

            while True:
                deadline_message = agent._deadline_message(session_id)
                if deadline_message:
                    agent.agent_status[agent_id] = "idle"
                    notice = (
                        f"No response received yet from {agent_id}. {deadline_message} "
                        "Proceed with the information already available."
                    )
                    record_notice(session_id, notice)
                    return notice

                remaining_wait = agent._remaining_wait_budget(session_id)
                if remaining_wait <= 0:
                    agent.agent_status[agent_id] = "idle"
                    notice = (
                        f"Stopped waiting for {agent_id} because the coordination budget was exhausted. "
                        "Proceed with the information already available."
                    )
                    record_notice(session_id, notice)
                    return notice

                response_msg = await agent.llm_behaviour.receive(timeout=min(0.1, remaining_wait))
                if response_msg:
                    sender_str = str(response_msg.sender).split("/")[0]
                    if sender_str == agent_id:
                        agent.agent_status[agent_id] = "idle"
                        record_response(session_id, agent_id, message, response_msg.body)
                        return f"Response from {agent_id}: {response_msg.body}"

                    logger.debug(
                        f"Received message from {sender_str} while waiting for {agent_id}; "
                        "leaving it out of the coordinator LLM context"
                    )

                await asyncio.sleep(0.05)

        return LLMTool(
            name="send_to_agent",
            description="Delegate a task to a specific subagent and wait for their response. Use for sequential workflows where you need the result before proceeding.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "The JID of the target subagent"},
                    "message": {
                        "type": "string",
                        "description": "The task, question, or request to send to the subagent",
                    },
                },
                "required": ["agent_id", "message"],
            },
            func=send_to_agent,
        )

    def _create_complete_task_tool(self) -> LLMTool:
        """Create a completion tool that tolerates model-provided summaries."""
        agent = self

        async def complete_task(summary: str = "", **kwargs: str) -> str:
            agent._task_completed = True
            agent._close_to_subagent_replies(agent._get_active_session_id())
            if getattr(agent, "return_raw_findings", False):
                logger.info("Task completed; raw findings mode will route compiled subagent evidence.")
                send_raw = getattr(agent, "_send_raw_findings_to_original_requester", None)
                sent = await send_raw(agent._get_active_session_id()) if send_raw else False
                if sent:
                    return "Task completed. The collected raw findings have been returned to the original requester."
                return "Task completed. The collected raw findings will be returned to the original requester."
            final_summary = (
                summary
                or kwargs.get("findings")
                or kwargs.get("findings_summary")
                or kwargs.get("final_summary")
                or kwargs.get("task_summary")
                or ""
            )
            logger.info("Task completed.")
            if final_summary:
                return (
                    "Task completed. Use this final findings summary in your next response "
                    f"to the original requester:\n\n{final_summary}"
                )
            return "Task completed. Send the final findings summary to the original requester now."

        return LLMTool(
            name="complete_task",
            description=(
                "Signal that all coordination work is finished. Optionally provide the final "
                "findings summary as 'summary'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Optional final findings summary to send to the original requester",
                    }
                },
                "required": [],
            },
            func=complete_task,
        )

    def _create_send_to_agents_parallel_tool(self) -> LLMTool:
        agent = self
        record_notice = getattr(agent, "_record_coordination_notice", lambda *args, **kwargs: None)
        record_response = getattr(agent, "_record_subagent_response", lambda *args, **kwargs: None)

        async def send_to_agents_parallel(tasks: List[Dict[str, str]]) -> str:
            invalid_agents = [task["agent_id"] for task in tasks if task["agent_id"] not in agent.subagent_ids]
            if invalid_agents:
                return f"Error: {', '.join(invalid_agents)} are not registered subagents"

            seen_agents: Set[str] = set()
            duplicate_agents: List[str] = []
            for task in tasks:
                agent_id = task["agent_id"]
                if agent_id in seen_agents and agent_id not in duplicate_agents:
                    duplicate_agents.append(agent_id)
                seen_agents.add(agent_id)
            if duplicate_agents:
                return (
                    "Error: send_to_agents_parallel received multiple tasks for "
                    f"{', '.join(duplicate_agents)}. Each agent can only receive one task per "
                    "parallel call. Re-plan with one task per agent, or call the same agent "
                    "sequentially after it responds."
                )

            session_id = agent._get_active_session_id()
            deadline_message = agent._deadline_message(session_id)
            if deadline_message:
                record_notice(session_id, deadline_message)
                return deadline_message

            tasks_to_run = tasks[:agent.max_parallel]
            skipped_count = max(0, len(tasks) - len(tasks_to_run))
            if not tasks_to_run:
                return "No valid tasks to run."

            launch_message = agent._register_launches(session_id, len(tasks_to_run))
            if launch_message:
                record_notice(session_id, launch_message)
                return launch_message

            pending_agents: Dict[str, float] = {}
            current_time = asyncio.get_running_loop().time()
            for task in tasks_to_run:
                msg = Message(to=task["agent_id"])
                msg.set_metadata("message_type", "llm")
                msg.set_metadata("coordination_session", session_id)
                msg.thread = session_id
                msg.body = task["message"]

                await agent.llm_behaviour.send(msg)
                agent.agent_status[task["agent_id"]] = "working"
                pending_agents[task["agent_id"]] = current_time

            responses: Dict[str, str] = {}

            while pending_agents:
                deadline_message = agent._deadline_message(session_id)
                if deadline_message:
                    notice = f"Stopped waiting because coordination time budget was exhausted. {deadline_message}"
                    record_notice(session_id, notice)
                    agent._mark_pending_exhausted(pending_agents, responses, notice)
                    break

                current_time = asyncio.get_running_loop().time()
                timed_out_agents = []
                for agent_id, start_time in pending_agents.items():
                    if current_time - start_time > agent.subagent_response_timeout:
                        timed_out_agents.append(agent_id)

                for agent_id in timed_out_agents:
                    agent.agent_status[agent_id] = "timeout"
                    notice = f"Error: {agent_id} did not respond within {agent.subagent_response_timeout} seconds"
                    responses[agent_id] = notice
                    record_notice(session_id, notice)
                    del pending_agents[agent_id]

                if not pending_agents:
                    break

                remaining_wait = agent._remaining_wait_budget(session_id)
                if remaining_wait <= 0:
                    notice = "Stopped waiting because the coordination time budget was exhausted."
                    record_notice(session_id, notice)
                    agent._mark_pending_exhausted(pending_agents, responses, notice)
                    break

                response_msg = await agent.llm_behaviour.receive(timeout=min(0.1, remaining_wait))
                if response_msg:
                    sender_str = str(response_msg.sender).split("/")[0]
                    if sender_str in pending_agents:
                        agent.agent_status[sender_str] = "idle"
                        responses[sender_str] = response_msg.body
                        task_message = next(
                            task["message"] for task in tasks_to_run if task["agent_id"] == sender_str
                        )
                        record_response(session_id, sender_str, task_message, response_msg.body)
                        del pending_agents[sender_str]
                    else:
                        logger.debug(
                            f"Received message from {sender_str} while waiting for parallel responses; "
                            "leaving it out of the coordinator LLM context"
                        )

                await asyncio.sleep(0.05)

            result_parts = []
            if skipped_count:
                notice = (
                    f"Only the first {len(tasks_to_run)} tasks were launched because max_parallel={agent.max_parallel}. "
                    f"{skipped_count} additional task(s) were not started."
                )
                record_notice(session_id, notice)
                result_parts.append(notice)

            for task in tasks_to_run:
                agent_id = task["agent_id"]
                result_parts.append(f"Response from {agent_id}: {responses.get(agent_id, 'No response')}")

            return "\n\n".join(result_parts)

        return LLMTool(
            name="send_to_agents_parallel",
            description="Delegate tasks to multiple subagents in parallel and wait for all responses. Use when tasks are independent and can run concurrently for faster execution.",
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent_id": {"type": "string", "description": "The JID of the target subagent"},
                                "message": {"type": "string", "description": "The task, question, or request to send"},
                            },
                            "required": ["agent_id", "message"],
                        },
                        "description": "List of tasks to delegate in parallel, each with agent_id and message",
                    }
                },
                "required": ["tasks"],
            },
            func=send_to_agents_parallel,
        )


# Backwards compatibility
CoordinatorAgent = SessionAwareCoordinatorAgent
