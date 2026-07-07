"""Pydantic schemas for API requests and responses."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Any, Dict

from pydantic import BaseModel, Field

from src.session import ReferenceSource
from src.extensions import extension_registry


# === Cost Tracking Schemas ===

class SessionCostResponse(BaseModel):
    """Cost information for a session."""
    total_cost: float = Field(0.0, description="Total cost in USD")
    prompt_tokens: int = Field(0, description="Total prompt tokens used")
    completion_tokens: int = Field(0, description="Total completion tokens used")
    total_tokens: int = Field(0, description="Total tokens used")
    cache_creation_input_tokens: int = Field(0, description="Tokens written into provider prompt cache, if reported")
    cache_read_input_tokens: int = Field(0, description="Prompt cache-hit tokens, if reported")
    call_count: int = Field(0, description="Number of LLM API calls")
    pricing_model: Optional[str] = Field(None, description="Model name used for local cost estimation")
    last_response_model: Optional[str] = Field(None, description="Last model name returned by the provider response")
    cost_source: str = Field("litellm_model_pricing_table", description="How the local USD cost estimate was computed")


# === Reference Schemas ===

class ReferenceResponse(BaseModel):
    """A reference from research sources."""
    identifier: str = Field(..., description="URL, arxiv ID, file path, or document ID")
    source_type: ReferenceSource = Field(..., description="Source type: tavily, arxiv, knowledge_base")
    title: Optional[str] = Field(None, description="Reference title if available")


# === Enums ===

class SessionStatusFilter(str, Enum):
    """Filter values for session status."""
    ALL = "all"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# === Research Session Schemas ===

# Built dynamically from registered extensions
ResearchRequest = extension_registry.build_research_request_model()


class ResearchCreateResponse(BaseModel):
    """Response for POST /research - session created."""
    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Current session status")
    created: bool = Field(True, description="Whether a new session was created (false if idempotency key matched)")


class ResearchStatusResponse(BaseModel):
    """Response for GET /research/{session_id} - session status."""
    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Current session status")
    query: str = Field(..., description="Original research query")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    report: Optional[str] = Field(None, description="Final research report (if complete)")
    cost: Optional[SessionCostResponse] = Field(None, description="Cost information for this session")
    references: List[ReferenceResponse] = Field(
        default_factory=list,
        description="References collected during research"
    )


class ResearchStopResponse(BaseModel):
    """Response for POST /research/{session_id}/stop - session stopped."""
    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="New session status (cancelled)")
    message: str = Field(..., description="Confirmation message")


class ResearchListResponse(BaseModel):
    """Response for GET /research - list all sessions."""
    sessions: List[ResearchStatusResponse] = Field(..., description="List of sessions (paginated)")
    total: int = Field(..., description="Total number of sessions matching filter")
    active: int = Field(..., description="Number of active sessions (across all)")
    limit: int = Field(..., description="Maximum number of sessions per page")
    offset: int = Field(..., description="Offset from the start of the results")
    has_more: bool = Field(..., description="Whether there are more sessions beyond this page")


# === SSE Event Schemas ===

class SSEEvent(BaseModel):
    """Schema for Server-Sent Events."""
    event: str = Field(..., description="Event type (state_change, completed, failed, cancelled)")
    timestamp: datetime = Field(..., description="When the event occurred")
    data: Dict[str, Any] = Field(..., description="Event payload")


# === Health Check Schemas ===

class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str = Field(..., description="Service health status")
    xmpp_connected: Optional[bool] = Field(None, description="XMPP server connection status")
    active_sessions: Optional[int] = Field(None, description="Number of active research sessions")
