"""Session-based Deep Research Orchestrator Agent.

Manages research sessions and coordinates work between specialized agents.
"""

import json
import asyncio
import inspect
import logging
import re
from datetime import datetime
from typing import Optional, Callable, List, Dict, Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from spade.template import Template

from src.session import SessionManager, ResearchSession, SessionState, MessageType, Reference, ReferenceSource
from src.references import reference_registry

from src.behaviors import create_response_message
from src.telemetry import telemetry_registry
from src.config.settings import settings
from src.config import prompts
from src.config.schemas import ResearchPlan, CriticReview
from src.agents.specialized import validate_report_citations, resolve_citations
from src.utils.json_utils import parse_and_validate_json
from src.text_normalization import normalize_report_text

class CancelledException(Exception):
    """Raised when a session is cancelled during execution."""
    pass

logger = logging.getLogger(__name__)


def _report_metrics(report: str) -> Dict[str, int]:
    """Return lightweight report metrics for critic context."""
    words = re.findall(r"\b[\w'-]+\b", report)
    headings = re.findall(r"(?m)^#{1,6}\s+", report)
    tables = re.findall(r"(?m)^\s*\|.*\|\s*$", report)
    citations = re.findall(r"\[\d+\]", report)
    return {
        "word_count": len(words),
        "character_count": len(report),
        "heading_count": len(headings),
        "table_row_count": len(tables),
        "citation_marker_count": len(citations),
    }


def _format_references(
    references: List[Reference],
    start_index: int = 0,
    heading: str = "Available References",
) -> str:
    """Format references using their actual 1-based global citation numbers."""
    if not references:
        return ""

    refs_text = f"\n\n{heading}:\n"
    for zero_based_index, ref in enumerate(references, start_index):
        title_str = ref.title or ""
        id_str = f" - {ref.identifier}"
        refs_text += f"[{zero_based_index + 1}] {title_str}{id_str}\n"
    return refs_text


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    """Return non-empty strings deduped in first-seen order."""
    seen = set()
    deduped = []
    for item in items:
        if not item:
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


class SessionWorkflowBehaviour(OneShotBehaviour):
    """Processes a single research session through its workflow.
    
    Flow: planning -> validation -> research -> writing -> review
    
    The workflow checks for cancellation at multiple points and will
    stop gracefully if the session is cancelled.
    """
    
    MAX_RETRIES = 3
    
    def __init__(self, session: ResearchSession, agent_config: Dict[str, Any],
                 input_func: Optional[Callable] = None, **kwargs):
        super().__init__(**kwargs)
        self.session = session
        self.agent_config = agent_config
        self.input_func = input_func
    
    def check_cancellation(self) -> None:
        """Check if the session has been cancelled and raise exception if so."""
        if self.session.is_cancelled:
            raise CancelledException(f"Session {self.session.session_id[:8]} was cancelled")
    
    async def send_request(self, to_jid: str, body: str, message_type: str) -> None:
        """Send a request with session metadata."""
        msg = Message(to=to_jid)
        msg.body = body
        msg.thread = self.session.session_id
        msg.set_metadata("message_type", "llm")
        msg.set_metadata("request_type", message_type)
        msg.set_metadata("session_id", self.session.session_id)
        msg.set_metadata("timestamp", datetime.now().isoformat())

        await self.send(msg)
        logger.debug(f"[{self.session.session_id[:8]}] Sent {message_type} to {to_jid}")
    
    async def wait_for_response(self, expected_types: List[str], timeout: float,
                                from_jid: Optional[str] = None) -> Optional[Message]:
        """Wait for a response matching this session.
        
        Also checks for cancellation periodically while waiting.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        
        while asyncio.get_event_loop().time() < deadline:
            # Check for cancellation
            self.check_cancellation()
            
            remaining = deadline - asyncio.get_event_loop().time()
            response = await self.receive(timeout=min(5, remaining))
            if not response:
                continue
                
            resp_session = response.get_metadata("session_id")
            resp_type = response.get_metadata("message_type")
            resp_request_type = response.get_metadata("request_type")
            sender_matches = (
                from_jid is None
                or str(response.sender).split('/')[0] == from_jid
            )
            
            # Match by session_id
            if resp_session == self.session.session_id:
                if sender_matches and (
                    resp_type in expected_types
                    or resp_request_type in expected_types
                    or resp_type == MessageType.LLM
                ):
                    return response
                if not sender_matches:
                    logger.debug(
                        f"[{self.session.session_id[:8]}] Ignoring response from "
                        f"{str(response.sender).split('/')[0]} while waiting for {from_jid}"
                    )
                    continue
            
            # Error for this session
            if resp_type == MessageType.ERROR and resp_session == self.session.session_id:
                logger.error(f"[{self.session.session_id[:8]}] Error: {response.body}")
                return None
            
            # Skip messages for other sessions
            if resp_session and resp_session != self.session.session_id:
                continue
            
            # Fallback: accept from expected JID without session_id
            if from_jid and str(response.sender).split('/')[0] == from_jid and not resp_session:
                logger.warning(f"[{self.session.session_id[:8]}] Accepting legacy message from {from_jid}")
                return response
        
        logger.warning(f"[{self.session.session_id[:8]}] Timeout waiting for {from_jid}")
        return None

    async def _clear_role_agent_context(self, role: str) -> None:
        """Clear one managed agent's LLM conversation state for this session if possible."""
        agent_manager = getattr(self, "agent_manager", None)
        owner_agent = getattr(self, "agent", None)
        if not agent_manager and owner_agent:
            agent_manager = getattr(owner_agent, "agent_manager", None)
        if not agent_manager or not hasattr(agent_manager, "get_agent"):
            return

        role_agent = agent_manager.get_agent(role)
        if not role_agent:
            return

        context = getattr(role_agent, "context", None)
        session_id = self.session.session_id
        clear_candidates = (
            (context, "clear_conversation", (session_id,)),
            (context, "clear", (session_id,)),
            (context, "reset_conversation", (session_id,)),
            (context, "reset", (session_id,)),
            (getattr(role_agent, "llm_behaviour", None), "reset_conversation", (session_id,)),
            (getattr(role_agent, "llm_behaviour", None), "reset", (session_id,)),
        )

        for target, method_name, args in clear_candidates:
            method = getattr(target, method_name, None) if target else None
            if not callable(method):
                continue
            try:
                result = method(*args)
            except TypeError:
                try:
                    result = method()
                except Exception:
                    logger.debug(
                        f"[{session_id[:8]}] Failed to clear {role} context via {method_name}",
                        exc_info=True,
                    )
                    continue
            except Exception:
                logger.debug(
                    f"[{session_id[:8]}] Failed to clear {role} context via {method_name}",
                    exc_info=True,
                )
                continue

            if inspect.isawaitable(result):
                await result
            break

        session_map = getattr(role_agent, "_session_map", None)
        if isinstance(session_map, dict):
            stale_keys = [key for key, value in session_map.items() if value == session_id]
            for key in stale_keys:
                session_map.pop(key, None)
    
    async def planning_phase(self) -> bool:
        """Execute planning and store language from plan."""
        self.session.update_state(SessionState.PLANNING)
        
        prompt = self.session.current_query
        
        for attempt in range(self.MAX_RETRIES):
            await self.send_request(
                self.agent_config["planner_jid"],
                prompt,
                MessageType.PLAN_REQUEST,
            )
            
            response = await self.wait_for_response(
                [MessageType.PLAN_RESPONSE],
                settings.PLANNER_TIMEOUT,
                self.agent_config["planner_jid"],
            )
            
            if response and response.body:
                plan = parse_and_validate_json(response.body, ResearchPlan)
                if plan:
                    self.session.current_plan = plan.model_dump()
                    self.session.language = plan.detected_language
                    logger.info(f"[{self.session.session_id[:8]}] Plan received (lang: {self.session.language})")
                    self.session.broadcast_progress(
                        f"Research plan created with {len(plan.research_questions)} research questions",
                        phase="planning"
                    )
                    return True
            
            logger.warning(f"[{self.session.session_id[:8]}] Plan attempt {attempt + 1}/{self.MAX_RETRIES} failed")
        
        return False
    
    async def validation_phase(self) -> bool:
        """Get user validation for the plan."""
        self.session.update_state(SessionState.AWAITING_VALIDATION)
        
        if self.input_func is None:
            return True
        
        print(f"\n[Session {self.session.session_id[:8]}] Proposed Plan:")
        print(json.dumps(self.session.current_plan, indent=2))
        
        choice = await self.input_func("\nApprove plan? (y/n/modify): ")
        
        if choice.lower().startswith('y'):
            return True
        
        feedback = await self.input_func("Enter feedback: ")
        self.session.current_query = (
            f"Original request: {self.session.initial_query}\n"
            f"Previous Plan: {json.dumps(self.session.current_plan)}\n"
            f"Feedback: {feedback}\nPlease update the plan."
        )
        return False
    
    async def research_phase(self) -> bool:
        """Execute research via coordinator."""
        self.session.update_state(SessionState.RESEARCHING)
        
        prompt = json.dumps(self.session.current_plan, indent=2)
        
        for attempt in range(self.MAX_RETRIES):
            await self.send_request(
                self.agent_config["coordinator_jid"],
                prompt,
                MessageType.RESEARCH_REQUEST,
            )
            
            response = await self.wait_for_response(
                [MessageType.RESEARCH_RESPONSE],
                settings.RESEARCH_TIMEOUT,
                self.agent_config["coordinator_jid"],
            )
            
            if response:
                self.session.research_context = response.body
                
                # Collect references from the registry
                reference_offset = len(self.session.references)
                pending_refs = reference_registry.collect(self.session.session_id)
                for ref in pending_refs:
                    self.session.add_reference(
                        identifier=ref.identifier,
                        source_type=ref.source_type,
                        title=ref.title
                    )
                self.session._latest_research_reference_offset = reference_offset
                logger.info(f"[{self.session.session_id[:8]}] Research completed, {len(self.session.references)} references collected")
                
                self.session.broadcast_progress(
                    "Research data collected, preparing report",
                    phase="researching"
                )
                return True
            
            logger.warning(f"[{self.session.session_id[:8]}] Research attempt {attempt + 1} failed")
        
        return False
    
    async def writing_phase(self) -> bool:
        """Generate the report."""
        self.session.update_state(SessionState.WRITING)

        is_revision = bool(self.session.current_report)
        prompt_parts = [f"Query: {self.session.initial_query}"]
        prompt_parts.append(f"\n\n{prompts.WRITER_DEPTH_BRIEF}")

        if not is_revision:
            prompt_parts.append(f"\n\nResearch Context:\n{self.session.research_context}")
            prompt_parts.append(_format_references(self.session.references))
        else:
            prompt_parts.append(
                "\n\nRevision Instruction:\n"
                "The previous report is already available in the conversation context. "
                "Return a full revised report, not a patch, changelog, or commentary."
            )

            if self.session._pending_research_gaps:
                prompt_parts.append("\n\nPrevious Research Gaps:\n")
                for gap in self.session._pending_research_gaps:
                    prompt_parts.append(f"- {gap}\n")
                prompt_parts.append(
                    f"\nNew Research Findings:\n{self.session.research_context}"
                )
                new_refs = self.session.references[self.session._latest_research_reference_offset:]
                prompt_parts.append(
                    _format_references(
                        new_refs,
                        start_index=self.session._latest_research_reference_offset,
                        heading="New References",
                    )
                )

        if self.session._pending_writing_feedback:
            feedback_section = "\n\nPREVIOUS DRAFT ISSUES TO FIX:\n"
            for issue in self.session._pending_writing_feedback:
                feedback_section += f"- {issue}\n"
            feedback_section += f"\n{prompts.WRITER_REVISION_GUIDANCE}"
            feedback_section += "\nPlease revise the report addressing ALL listed issues."
            prompt_parts.append(feedback_section)
            self.session._pending_writing_feedback = None
        elif is_revision:
            prompt_parts.append(f"\n\n{prompts.WRITER_REVISION_GUIDANCE}")
        
        if not self.session.language.lower().startswith("english"):
            prompt_parts.append(f"\n\nTarget Language: {self.session.language}")
        
        prompt = "".join(prompt_parts)
        
        for attempt in range(self.MAX_RETRIES):
            await self.send_request(
                self.agent_config["writer_jid"],
                prompt,
                MessageType.WRITE_REQUEST,
            )
            
            response = await self.wait_for_response(
                [MessageType.WRITE_RESPONSE],
                settings.WRITER_TIMEOUT,
                self.agent_config["writer_jid"],
            )
            
            if response:
                self.session.current_report = normalize_report_text(response.body)
                self.session._pending_research_gaps = None
                telemetry_registry.record_report(self.session.session_id, self.session.current_report or "")
                logger.info(f"[{self.session.session_id[:8]}] Report drafted")
                self.session.broadcast_progress(
                    "Draft report generated, sending for review",
                    phase="writing"
                )
                return True
            
            logger.warning(f"[{self.session.session_id[:8]}] Writing attempt {attempt + 1} failed")
        
        return False
    
    async def review_phase(self) -> str:
        """Review the report. Returns: 'approved', 'needs_research', 'needs_rewrite', or 'failed'."""
        self.session.update_state(SessionState.REVIEWING)
        await self._clear_role_agent_context("critic")

        # Run non-LLM validation first
        validation_issues, invalid_citations = validate_report_citations(
            self.session.current_report or "",
            self.session.references
        )

        metrics = _report_metrics(self.session.current_report or "")
        metrics_text = "\n".join(
            f"- {name}: {value}" for name, value in metrics.items()
        )

        # Send complete review context. The critic system prompt carries the review rubric.
        prompt_parts = [
            f"Original Query: {self.session.initial_query}",
            f"\n\nActual Report Metrics:\n{metrics_text}",
            f"\nReport:\n{self.session.current_report}"
        ]

        prompt_parts.append(_format_references(self.session.references))

        if validation_issues or invalid_citations:
            validation_text = "\n\nAUTOMATED VALIDATION FINDINGS:\n"
            for issue in validation_issues:
                validation_text += f"- WARNING: {issue}\n"
            for issue in invalid_citations:
                validation_text += f"- ERROR: {issue}\n"
            validation_text += "\nThese issues MUST be addressed in writing_improvements.\n"
            prompt_parts.append(validation_text)

        prompt = "".join(prompt_parts)
        
        for attempt in range(self.MAX_RETRIES):
            await self.send_request(
                self.agent_config["critic_jid"],
                prompt,
                MessageType.REVIEW_REQUEST,
            )
            
            response = await self.wait_for_response(
                [MessageType.REVIEW_RESPONSE],
                settings.CRITIC_TIMEOUT,
                self.agent_config["critic_jid"],
            )
            
            if response and response.body:
                critique = parse_and_validate_json(response.body, CriticReview)
                if critique:
                    automated_issues = validation_issues + invalid_citations
                    actionable_writing_feedback = _dedupe_preserve_order(
                        critique.issues
                        + critique.writing_improvements
                        + automated_issues
                    )
                    if automated_issues and critique.status == "SUFFICIENT":
                        critique.status = "INSUFFICIENT"

                    if (
                        critique.status == "INSUFFICIENT"
                        and not actionable_writing_feedback
                        and not critique.missing_information
                        and critique.feedback
                    ):
                        actionable_writing_feedback = [critique.feedback]

                    if actionable_writing_feedback:
                        self.session._pending_writing_feedback = actionable_writing_feedback

                    if critique.missing_information:
                        self.session._pending_research_gaps = critique.missing_information
                        self.session.current_plan = {
                            "research_goal": "Address missing information",
                            "research_questions": [
                                {
                                    "question": topic,
                                    "source": "tavily",
                                    "description": "Gap filling"
                                }
                                for topic in critique.missing_information
                            ]
                        }
                        logger.info(f"[{self.session.session_id[:8]}] Needs more research")
                        self.session.broadcast_progress(
                            f"Additional research needed: {len(critique.missing_information)} gaps identified",
                            phase="reviewing"
                        )
                        return "needs_research"

                    if actionable_writing_feedback:
                        logger.info(
                            f"[{self.session.session_id[:8]}] Routing to writer: {len(actionable_writing_feedback)} improvements needed"
                        )
                        return "needs_rewrite"

                    if critique.status == "SUFFICIENT":
                        logger.info(f"[{self.session.session_id[:8]}] Report approved")
                        self.session.broadcast_progress(
                            "Report approved by reviewer",
                            phase="reviewing"
                        )
                        return "approved"

                    return "needs_rewrite"
            
            logger.warning(f"[{self.session.session_id[:8]}] Review attempt {attempt + 1} failed")
        
        return "failed"
    
    async def run(self):
        """Execute the complete research workflow."""
        logger.info(f"[{self.session.session_id[:8]}] Starting: {self.session.initial_query[:50]}...")
        
        try:
            # Check cancellation before starting
            self.check_cancellation()
            
            # Planning with optional user feedback
            while True:
                self.check_cancellation()
                if not await self.planning_phase():
                    self.session.mark_failed()
                    return
                self.check_cancellation()
                if await self.validation_phase():
                    break
            
            # Initial research and draft.
            self.check_cancellation()
            logger.info(f"[{self.session.session_id[:8]}] Initial research")
            if not await self.research_phase():
                self.session.mark_failed()
                return

            self.check_cancellation()
            if not await self.writing_phase():
                self.session.mark_failed()
                return

            if self.session.disable_critic or settings.MAX_CRITIC_ITERATIONS <= 0:
                logger.info(f"[{self.session.session_id[:8]}] Critic disabled, accepting report")
            else:
                result = await self.review_phase()

                for iteration in range(settings.MAX_CRITIC_ITERATIONS):
                    self.check_cancellation()

                    if result == "approved":
                        break

                    logger.info(
                        f"[{self.session.session_id[:8]}] Critic feedback loop "
                        f"{iteration + 1}/{settings.MAX_CRITIC_ITERATIONS}: {result}"
                    )

                    if result == "needs_research":
                        if not await self.research_phase():
                            self.session.mark_failed()
                            return
                    elif result != "needs_rewrite":
                        logger.warning(
                            f"[{self.session.session_id[:8]}] Review failed, accepting current report"
                        )
                        break

                    self.check_cancellation()
                    if not await self.writing_phase():
                        self.session.mark_failed()
                        return

                    self.check_cancellation()
                    result = await self.review_phase()

                if result != "approved":
                    logger.info(
                        f"[{self.session.session_id[:8]}] Critic feedback budget exhausted, accepting current report"
                    )
            
            # Resolve [n] citations into markdown links before finalizing
            if self.session.current_report and self.session.references:
                self.session.current_report = resolve_citations(
                    self.session.current_report, self.session.references
                )
                self.session.current_report = normalize_report_text(self.session.current_report)
                telemetry_registry.record_report(self.session.session_id, self.session.current_report)

            self.session.mark_complete()
            
            print(f"\n{'='*60}")
            print(f"FINAL REPORT (Session {self.session.session_id[:8]})")
            print("="*60)
            print(self.session.current_report)
            print("="*60)
            
            if self.session.chat_sender and self.session.current_report:
                await self.send(create_response_message(
                    self.session.chat_sender,
                    self.session.current_report,
                    self.session.session_id,
                    MessageType.LLM,
                ))
            
            logger.info(f"[{self.session.session_id[:8]}] Workflow complete!")
        
        except CancelledException:
            logger.info(f"[{self.session.session_id[:8]}] Workflow cancelled by user")
            # Session is already marked as cancelled by the stop_session call
            
        except Exception as e:
            logger.error(f"[{self.session.session_id[:8]}] Error: {e}", exc_info=True)
            self.session.mark_failed()
        finally:
            if self.agent and hasattr(self.agent, "finalize_session"):
                await self.agent.finalize_session(self.session.session_id)


class NewQueryListenerBehaviour(CyclicBehaviour):
    """Listens for new research queries.
    
    The template filters for messages with performative='request' metadata.
    """
    
    async def run(self) -> None:
        msg = await self.receive(timeout=10)
        
        if not msg or not msg.body:
            return
        
        query = msg.body.strip()
        if not query:
            return
        
        if self.agent is None:
            return
        
        orchestrator: DeepResearchAgent = self.agent  # type: ignore
        sender_jid = str(msg.sender).split('/')[0]
        
        logger.info(f"[NewQueryListener] New query from {sender_jid}: {query[:50]}...")
        await orchestrator.start_session(query, str(msg.sender))


class DeepResearchAgent(Agent):
    """Session-aware Deep Research Orchestrator."""
    
    def __init__(self, jid: str, password: str, planner_jid: str, coordinator_jid: str,
                 writer_jid: str, critic_jid: str, researcher_jids: Optional[List[str]] = None,
                 initial_query: Optional[str] = None, input_func: Optional[Callable] = None,
                 **kwargs):
        super().__init__(jid, password, **kwargs)
        
        self.agent_config = {
            "planner_jid": planner_jid,
            "coordinator_jid": coordinator_jid,
            "writer_jid": writer_jid,
            "critic_jid": critic_jid,
            "researcher_jids": researcher_jids or [],
        }
        self.input_func = input_func
        self.initial_query = initial_query
        self.session_manager = SessionManager()
        self.agent_manager = None
        self._workflows: dict = {}
    
    async def start_session(
        self,
        query: str,
        chat_sender: Optional[str] = None,
        extensions: Optional[Dict[str, Any]] = None,
        disable_critic: bool = False,
    ) -> ResearchSession:
        """Start a new research session.
        
        Args:
            query: The research query
            chat_sender: Optional JID of the chat sender
            extensions: Optional plugin data keyed by plugin name
            disable_critic: If True, skip the critic review loop
        """
        session = self.session_manager.create_session(
            query,
            chat_sender=chat_sender,
            extensions=extensions,
            disable_critic=disable_critic,
        )
        
        workflow = SessionWorkflowBehaviour(
            session=session,
            agent_config=self.agent_config,
            input_func=self.input_func,
        )
        
        self.add_behaviour(workflow)
        self._workflows[session.session_id] = workflow
        
        logger.info(f"[DeepResearchAgent] Started session {session.session_id[:8]}")
        return session
    
    async def get_session(self, session_id: str) -> Optional[ResearchSession]:
        return self.session_manager.get_session(session_id)
    
    async def stop_session(self, session_id: str) -> bool:
        """Stop a research session.
        
        Cancels the session, signals the workflow to stop gracefully, and
        sends control messages to cancel any ongoing agent work.
        
        Args:
            session_id: The session ID to stop
            
        Returns:
            True if the session was cancelled, False if not found or already complete
        """
        success = self.session_manager.cancel_session(session_id)
        
        if success:
            logger.info(f"[DeepResearchAgent] Stopped session {session_id[:8]}")
            await self.finalize_session(session_id)
        
        return success

    async def finalize_session(self, session_id: str) -> None:
        """Tear down workflow/session routing state after a terminal outcome."""
        workflow = self._workflows.pop(session_id, None)
        if workflow and hasattr(workflow, "kill"):
            try:
                workflow.kill()
            except Exception:
                logger.debug(f"[DeepResearchAgent] Failed to kill workflow for {session_id[:8]}", exc_info=True)

        if not self.agent_manager:
            return

        coordinator = self.agent_manager.get_agent("coordinator")
        if not coordinator or not hasattr(coordinator, "llm_behaviour"):
            return

        await self._send_cancel_control_message(
            coordinator.llm_behaviour,
            str(coordinator.jid),
            session_id,
        )

        if hasattr(coordinator, "subagent_ids"):
            for agent_id in coordinator.subagent_ids:
                await self._send_cancel_control_message(
                    coordinator.llm_behaviour,
                    agent_id,
                    session_id,
                )

        if hasattr(coordinator, "clear_session_tracking"):
            coordinator.clear_session_tracking(session_id)
    
    async def _send_cancel_control_message(self, behaviour, to_jid: str, session_id: str) -> None:
        """Send a hard cancel control message to an agent.
        
        Uses proper SPADE messaging instead of direct object references.
        
        Args:
            behaviour: The behaviour to send from
            to_jid: The target agent JID
            session_id: The session/conversation ID to cancel
        """
        from spade.message import Message
        
        msg = Message(to=to_jid)
        msg.set_metadata("message_type", "control")
        msg.set_metadata("control_action", "hard_cancel")
        msg.set_metadata("conversation_id", session_id)
        msg.set_metadata("session_id", session_id)
        msg.thread = session_id
        msg.body = "Cancel request: hard_cancel"
        
        try:
            await behaviour.send(msg)
            logger.debug(f"[DeepResearchAgent] Sent hard_cancel to {to_jid} for {session_id[:8]}")
        except Exception as e:
            logger.warning(f"[DeepResearchAgent] Failed to send cancel to {to_jid}: {e}")
    
    async def get_active_sessions(self):
        return self.session_manager.get_active_sessions()
    
    async def wait_for_session(self, session_id: str, timeout: Optional[float] = None) -> bool:
        return await self.session_manager.wait_for_completion(session_id, timeout)
    
    async def wait_for_all_sessions(self, timeout: Optional[float] = None) -> bool:
        sessions = self.session_manager.get_all_sessions()
        tasks = [self.session_manager.wait_for_completion(sid, timeout) for sid in sessions]
        if tasks:
            results = await asyncio.gather(*tasks)
            return all(results)
        return True
    
    @property
    def complete(self) -> bool:
        """Check if all sessions are complete (or cancelled/failed)."""
        for session in self.session_manager._sessions.values():
            if session.is_active:
                return False
        return len(self.session_manager._sessions) > 0
    
    async def setup(self):
        logger.info("DeepResearchAgent starting...")
        
        # Use FIPA standard: only accept messages with performative='request'
        # External clients should send messages with metadata performative='request'
        template = Template()
        template.set_metadata("performative", "request")
        
        listener = NewQueryListenerBehaviour()
        self.add_behaviour(listener, template)
        
        if self.initial_query:
            await self.start_session(self.initial_query)
