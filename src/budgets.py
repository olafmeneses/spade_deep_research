"""Session-scoped budget tracking for coordination and tool usage."""

from dataclasses import dataclass
from threading import Lock
from typing import Dict, Optional


@dataclass
class CoordinatorBudgetState:
    """Coordinator budget state for a single research session."""

    launches: int = 0
    waves: int = 0


class SessionBudgetRegistry:
    """In-memory session budget registry shared across agents in this process."""

    _lock = Lock()
    _coordinator: Dict[str, CoordinatorBudgetState] = {}
    _tavily_session_calls: Dict[str, int] = {}
    _tavily_agent_calls: Dict[str, int] = {}

    @classmethod
    def get_coordinator_state(cls, session_id: str) -> CoordinatorBudgetState:
        with cls._lock:
            state = cls._coordinator.get(session_id)
            if state is None:
                state = CoordinatorBudgetState()
                cls._coordinator[session_id] = state
            return state

    @classmethod
    def try_register_coordinator_launches(
        cls,
        session_id: str,
        requested_launches: int,
        max_launches: Optional[int],
    ) -> tuple[bool, CoordinatorBudgetState]:
        with cls._lock:
            state = cls._coordinator.get(session_id)
            if state is None:
                state = CoordinatorBudgetState()
                cls._coordinator[session_id] = state

            if max_launches is not None and state.launches + requested_launches > max_launches:
                return False, state

            state.launches += requested_launches
            state.waves += 1
            return True, state

    @classmethod
    def try_consume_tavily_call(
        cls,
        session_id: Optional[str],
        agent_id: Optional[str],
        max_calls_per_session: Optional[int],
        max_calls_per_agent: Optional[int],
    ) -> tuple[bool, Optional[str]]:
        with cls._lock:
            if session_id and max_calls_per_session is not None:
                session_calls = cls._tavily_session_calls.get(session_id, 0)
                if session_calls >= max_calls_per_session:
                    return (
                        False,
                        f"Tavily session call budget exhausted ({session_calls}/{max_calls_per_session}).",
                    )

            agent_key = None
            if agent_id:
                agent_key = f"{session_id}:{agent_id}" if session_id else agent_id
            if agent_key and max_calls_per_agent is not None:
                agent_calls = cls._tavily_agent_calls.get(agent_key, 0)
                if agent_calls >= max_calls_per_agent:
                    return (
                        False,
                        f"Tavily agent call budget exhausted for {agent_id} ({agent_calls}/{max_calls_per_agent}).",
                    )

            if session_id:
                cls._tavily_session_calls[session_id] = cls._tavily_session_calls.get(session_id, 0) + 1
            if agent_key:
                cls._tavily_agent_calls[agent_key] = cls._tavily_agent_calls.get(agent_key, 0) + 1
            return True, None

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        with cls._lock:
            cls._coordinator.pop(session_id, None)
            cls._tavily_session_calls.pop(session_id, None)
            for agent_key in [key for key in cls._tavily_agent_calls if key.startswith(f"{session_id}:")]:
                cls._tavily_agent_calls.pop(agent_key, None)

    @classmethod
    def clear_agent(cls, agent_id: str) -> None:
        with cls._lock:
            cls._tavily_agent_calls.pop(agent_id, None)

    @classmethod
    def reset_all(cls) -> None:
        with cls._lock:
            cls._coordinator.clear()
            cls._tavily_session_calls.clear()
            cls._tavily_agent_calls.clear()
