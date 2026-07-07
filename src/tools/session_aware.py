"""Mixin for making tools session-aware."""

from typing import Optional


class SessionAwareToolMixin:
    """Mixin that adds session awareness to tools.
    
    Tools with this mixin can have their session_id set before execution,
    allowing them to register references with the correct session.
    """
    _session_id: Optional[str] = None
    
    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID for this tool."""
        self._session_id = session_id
    
    def get_session_id(self) -> Optional[str]:
        """Get the current session ID."""
        return self._session_id
