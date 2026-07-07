"""Thread-safe reference registry for collecting references during tool execution."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Union
from threading import Lock
import logging

from src.telemetry import telemetry_registry
from src.session import ReferenceSource

logger = logging.getLogger(__name__)


@dataclass
class PendingReference:
    """A reference waiting to be collected."""
    identifier: str
    source_type: ReferenceSource
    title: Optional[str] = None


class ReferenceRegistry:
    """Singleton registry for collecting references during tool execution.
    
    Tools call register() during execution, orchestrator calls collect() after research.
    Thread-safe for concurrent tool executions.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._refs: Dict[str, List[PendingReference]] = {}
            cls._instance._lock = Lock()
        return cls._instance
    
    def register(self, session_id: str, identifier: str, 
                 source_type: Union[ReferenceSource, str], title: Optional[str] = None) -> None:
        """Register a reference for a session (thread-safe).
        
        Args:
            session_id: The session ID to register the reference for
            identifier: URL, arxiv ID, file path, or document ID
            source_type: Source type (ReferenceSource enum or string value)
            title: Optional title for the reference
        """
        if not session_id or not identifier:
            return
        
        # Convert string to enum if needed
        if isinstance(source_type, str):
            try:
                source_type = ReferenceSource(source_type)
            except ValueError:
                logger.warning(f"Unknown source type: {source_type}, skipping reference")
                return
            
        with self._lock:
            if session_id not in self._refs:
                self._refs[session_id] = []
            
            # Deduplicate by identifier
            if not any(r.identifier == identifier for r in self._refs[session_id]):
                self._refs[session_id].append(
                    PendingReference(identifier, source_type, title)
                )
                telemetry_registry.record_reference(session_id, source_type.value, identifier, title)
                logger.debug(f"Registered reference: {identifier[:50]} ({source_type.value})")
    
    def collect(self, session_id: str) -> List[PendingReference]:
        """Get and clear all references for a session."""
        with self._lock:
            refs = self._refs.pop(session_id, [])
            logger.info(f"Collected {len(refs)} references for session {session_id[:8]}")
            return refs
    
    def peek(self, session_id: str) -> List[PendingReference]:
        """Get references without clearing (for debugging)."""
        with self._lock:
            return list(self._refs.get(session_id, []))
    
    def clear(self, session_id: str) -> None:
        """Clear all references for a session without returning them."""
        with self._lock:
            self._refs.pop(session_id, None)


# Global singleton instance
reference_registry = ReferenceRegistry()
