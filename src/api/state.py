"""Application state management."""

import asyncio
import time
from typing import Optional, Dict, Tuple

from pyjabber.server import Server

from src.config.retrieval import RetrievalConfig
from src.agents import AgentManager
from src.orchestrator import DeepResearchAgent


# Idempotency key TTL in seconds (24 hours)
IDEMPOTENCY_KEY_TTL = 86400


class AppState:
    """Holds references to the agent system components.
    
    This singleton-like class maintains the state of all agent system
    components during the application lifecycle.
    """
    
    def __init__(self):
        self.orchestrator: Optional[DeepResearchAgent] = None
        self.agent_manager: Optional[AgentManager] = None
        self.retrieval_config: Optional[RetrievalConfig] = None
        self.xmpp_server: Optional[Server] = None
        self.xmpp_task: Optional[asyncio.Task] = None
        
        # Idempotency key storage: key -> (session_id, timestamp)
        self._idempotency_keys: Dict[str, Tuple[str, float]] = {}
    
    @property
    def is_ready(self) -> bool:
        """Check if the application is ready to handle requests."""
        return self.orchestrator is not None
    
    def store_idempotency_key(self, key: str, session_id: str) -> None:
        """Store an idempotency key mapping to a session ID.
        
        Keys expire after IDEMPOTENCY_KEY_TTL seconds.
        """
        self._cleanup_expired_keys()
        self._idempotency_keys[key] = (session_id, time.time())
    
    def get_idempotency_session(self, key: str) -> Optional[str]:
        """Get the session ID for an idempotency key if it exists and hasn't expired.
        
        Returns None if the key doesn't exist or has expired.
        """
        self._cleanup_expired_keys()
        
        entry = self._idempotency_keys.get(key)
        if entry is None:
            return None
        
        session_id, timestamp = entry
        if time.time() - timestamp > IDEMPOTENCY_KEY_TTL:
            del self._idempotency_keys[key]
            return None
        
        return session_id
    
    def _cleanup_expired_keys(self) -> None:
        """Remove expired idempotency keys."""
        current_time = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self._idempotency_keys.items()
            if current_time - timestamp > IDEMPOTENCY_KEY_TTL
        ]
        for key in expired_keys:
            del self._idempotency_keys[key]
    
    def reset(self) -> None:
        """Reset all state references."""
        self.orchestrator = None
        self.agent_manager = None
        self.retrieval_config = None
        self.xmpp_server = None
        self.xmpp_task = None
        self._idempotency_keys.clear()


# Global application state instance
app_state = AppState()
