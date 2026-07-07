"""Session management for concurrent research queries."""

import uuid
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, List
import logging

from src.budgets import SessionBudgetRegistry
from src.telemetry import telemetry_registry
from src.text_normalization import normalize_report_text

logger = logging.getLogger(__name__)


class ReferenceSource(str, Enum):
    """Supported reference source types."""
    TAVILY = "tavily"
    ARXIV = "arxiv"
    KNOWLEDGE_BASE = "knowledge_base"


@dataclass
class Reference:
    """A reference collected during research."""
    identifier: str  # URL, arxiv ID, file path, or document ID
    source_type: ReferenceSource  # Source of the reference
    title: Optional[str] = None


@dataclass
class SessionEvent:
    """An event representing a state change in a session."""
    event_type: str
    timestamp: datetime
    data: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for SSE serialization."""
        return {
            "event": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


class SessionState(str, Enum):
    """Research session states."""
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_VALIDATION = "awaiting_validation"
    RESEARCHING = "researching"
    WRITING = "writing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageType(str, Enum):
    """Message types for session-aware XMPP communication."""
    PLAN_REQUEST = "plan_request"
    PLAN_RESPONSE = "plan_response"
    PLAN_FEEDBACK = "plan_feedback"
    RESEARCH_REQUEST = "research_request"
    RESEARCH_RESPONSE = "research_response"
    WRITE_REQUEST = "write_request"
    WRITE_RESPONSE = "write_response"
    REVIEW_REQUEST = "review_request"
    REVIEW_RESPONSE = "review_response"
    LLM = "llm"
    SYSTEM = "system"
    ERROR = "error"


@dataclass
class ResearchSession:
    """A research session with all its state."""
    
    session_id: str
    initial_query: str
    current_query: str
    language: str = "English"
    state: SessionState = SessionState.CREATED
    
    # Research artifacts
    current_plan: Optional[Dict] = None
    research_context: Optional[str] = None
    current_report: Optional[str] = None

    # Workflow options
    disable_critic: bool = False
    
    # Tracking
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)
    retry_counts: Dict[str, int] = field(default_factory=dict)
    chat_sender: Optional[str] = None
    
    # Arbitrary plugin data keyed by plugin name
    extensions: Dict[str, Any] = field(default_factory=dict)
    
    # References collected during research
    references: List[Reference] = field(default_factory=list)

    # Feedback for rewrite routing
    _pending_writing_feedback: Optional[List[str]] = field(default=None)
    _pending_research_gaps: Optional[List[str]] = field(default=None)
    _latest_research_reference_offset: int = 0
    
    # SSE event streaming
    _event_subscribers: List[asyncio.Queue] = field(default_factory=list)
    
    def add_reference(self, identifier: str, source_type: ReferenceSource, title: Optional[str] = None) -> None:
        """Add a reference if not already present (dedupe by identifier)."""
        if not any(r.identifier == identifier for r in self.references):
            self.references.append(Reference(identifier, source_type, title))
            telemetry_registry.record_reference(self.session_id, source_type.value, identifier, title)
    
    def subscribe(self) -> asyncio.Queue:
        """Subscribe to session events. Returns a queue that receives SessionEvent objects."""
        queue: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(queue)
        return queue
    
    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from session events."""
        if queue in self._event_subscribers:
            self._event_subscribers.remove(queue)
    
    def _broadcast_event(self, event_type: str, extra_data: Optional[Dict[str, Any]] = None) -> None:
        """Broadcast an event to all subscribers."""
        data = {
            "session_id": self.session_id,
            "status": self.state.value,
            "query": self.initial_query,
        }
        if extra_data:
            data.update(extra_data)
        
        event = SessionEvent(
            event_type=event_type,
            timestamp=datetime.now(),
            data=data,
        )
        
        for queue in self._event_subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"[Session {self.session_id[:8]}] Event queue full, dropping event")
    
    def update_state(self, new_state: SessionState) -> None:
        """Update session state and timestamp."""
        old_state = self.state
        self.state = new_state
        self.updated_at = datetime.now()
        logger.debug(f"[Session {self.session_id[:8]}] State -> {new_state.value}")
        telemetry_registry.record_state_transition(
            self.session_id,
            previous_status=old_state.value,
            new_status=new_state.value,
            query=self.initial_query,
            timestamp=self.updated_at,
        )
        
        # Broadcast state change event
        self._broadcast_event("state_change", {
            "previous_status": old_state.value,
        })
    
    def broadcast_progress(self, message: str, phase: Optional[str] = None) -> None:
        """Broadcast a progress update event."""
        extra = {"message": message}
        if phase:
            extra["phase"] = phase
        telemetry_registry.record_progress(self.session_id, message, phase)
        self._broadcast_event("progress", extra)
    
    def get_retry_count(self, phase: str) -> int:
        return self.retry_counts.get(phase, 0)
    
    def increment_retry(self, phase: str) -> int:
        self.retry_counts[phase] = self.get_retry_count(phase) + 1
        return self.retry_counts[phase]
    
    def mark_complete(self) -> None:
        if self.current_report is not None:
            self.current_report = normalize_report_text(self.current_report)
        self.update_state(SessionState.COMPLETED)
        telemetry_registry.record_report(self.session_id, self.current_report or "")
        telemetry_registry.record_terminal_state(
            self.session_id,
            status=SessionState.COMPLETED.value,
            details={"completed_at": self.updated_at.isoformat()},
        )
        # Broadcast completion with report
        self._broadcast_event("completed", {"report": self.current_report})
        self.completion_event.set()
    
    def mark_failed(self) -> None:
        self.update_state(SessionState.FAILED)
        telemetry_registry.record_terminal_state(
            self.session_id,
            status=SessionState.FAILED.value,
            details={"failed_at": self.updated_at.isoformat()},
        )
        self._broadcast_event("failed")
        self.completion_event.set()
    
    def mark_cancelled(self) -> None:
        """Mark the session as cancelled."""
        self.update_state(SessionState.CANCELLED)
        telemetry_registry.record_terminal_state(
            self.session_id,
            status=SessionState.CANCELLED.value,
            details={"cancelled_at": self.updated_at.isoformat()},
        )
        self._broadcast_event("cancelled")
        self.cancellation_event.set()
        self.completion_event.set()
        logger.info(f"[Session {self.session_id[:8]}] Cancelled")
    
    @property
    def is_cancelled(self) -> bool:
        """Check if the session has been cancelled."""
        return self.cancellation_event.is_set()
    
    @property
    def is_active(self) -> bool:
        """Check if the session is still active (not completed, failed, or cancelled)."""
        return self.state not in (
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.CANCELLED
        )


class SessionManager:
    """Manager for concurrent research sessions."""
    
    def __init__(self):
        self._sessions: Dict[str, ResearchSession] = {}
    
    def create_session(
        self,
        query: str,
        chat_sender: Optional[str] = None,
        extensions: Optional[Dict[str, Any]] = None,
        disable_critic: bool = False,
    ) -> ResearchSession:
        """Create a new research session.
        
        Args:
            query: The research query
            chat_sender: Optional JID of the chat sender
            extensions: Optional plugin data keyed by plugin name
            disable_critic: If True, skip the critic review loop
        """
        session_id = str(uuid.uuid4())
        session = ResearchSession(
            session_id=session_id,
            initial_query=query,
            current_query=query,
            chat_sender=chat_sender,
            extensions=extensions or {},
            disable_critic=disable_critic,
        )
        self._sessions[session_id] = session
        telemetry_registry.initialize_session(
            session_id=session_id,
            query=query,
            status=session.state.value,
            created_at=session.created_at,
        )
        logger.info(f"[SessionManager] Created session {session_id[:8]}")
        return session
    
    def get_session(self, session_id: str) -> Optional[ResearchSession]:
        return self._sessions.get(session_id)
    
    def remove_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]
            SessionBudgetRegistry.clear_session(session_id)
            telemetry_registry.clear_session_memory(session_id)
            logger.info(f"[SessionManager] Removed session {session_id[:8]}")
    
    def get_active_sessions(self) -> Dict[str, ResearchSession]:
        """Get all active (non-completed) sessions."""
        return {
            sid: s for sid, s in self._sessions.items()
            if s.is_active
        }
    
    def cancel_session(self, session_id: str) -> bool:
        """Cancel a session if it's still active.
        
        Returns True if the session was cancelled, False if not found or not active.
        """
        session = self.get_session(session_id)
        if not session:
            return False
        
        if not session.is_active:
            return False
        
        session.mark_cancelled()
        SessionBudgetRegistry.clear_session(session_id)
        telemetry_registry.clear_session_memory(session_id)
        return True
    
    def get_all_sessions(self) -> Dict[str, ResearchSession]:
        return dict(self._sessions)
    
    async def wait_for_completion(self, session_id: str, timeout: Optional[float] = None) -> bool:
        """Wait for a session to complete."""
        session = self.get_session(session_id)
        if not session:
            return False
        try:
            await asyncio.wait_for(session.completion_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
