"""Research session endpoints."""

import json
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, Header, Query
from fastapi.responses import StreamingResponse

from src.orchestrator import DeepResearchAgent
from src.session import SessionState
from src.api.schemas import (
    ResearchRequest,
    ResearchCreateResponse,
    ResearchStatusResponse,
    ResearchStopResponse,
    ResearchListResponse,
    SessionStatusFilter,
    SessionCostResponse,
    ReferenceResponse,
)
from src.api.dependencies import get_orchestrator
from src.api.state import app_state
from src.utils.cost_tracker import cost_tracker
from src.references import reference_registry
from src.extensions import extension_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["Research"])


def _get_session_cost(session_id: str) -> Optional[SessionCostResponse]:
    """Get cost information for a session from the cost tracker."""
    cost_info = cost_tracker.get_session_cost(session_id)
    if cost_info:
        return SessionCostResponse(
            total_cost=cost_info.total_cost,
            prompt_tokens=cost_info.prompt_tokens,
            completion_tokens=cost_info.completion_tokens,
            total_tokens=cost_info.total_tokens,
            cache_creation_input_tokens=cost_info.cache_creation_input_tokens,
            cache_read_input_tokens=cost_info.cache_read_input_tokens,
            call_count=cost_info.call_count,
            pricing_model=cost_info.pricing_model,
            last_response_model=cost_info.last_response_model,
            cost_source=cost_info.cost_source,
        )
    return None


def _get_session_references(session) -> list:
    """Get references from a session as ReferenceResponse objects.

    Includes pending references while the session is active.
    """
    # Start with committed references
    combined = {ref.identifier: ref for ref in session.references}

    # Include pending references during active sessions
    if session.is_active:
        for pending in reference_registry.peek(session.session_id):
            if pending.identifier not in combined:
                combined[pending.identifier] = pending

    return [
        ReferenceResponse(
            identifier=ref.identifier,
            source_type=ref.source_type,
            title=ref.title
        )
        for ref in combined.values()
    ]


@router.post(
    "",
    status_code=201,
    response_model=ResearchCreateResponse,
    summary="Start a new research session",
    description=(
        "Create a new research session with the given query. "
        "**Idempotency**: Provide an `X-Idempotency-Key` header to prevent duplicate "
        "sessions on retries. If the same key is used again, the existing session is returned."
    ),
)
async def create_research_session(
    request: ResearchRequest,  # type: ignore[valid-type]  # dynamic model from create_model()
    response: Response,
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
    x_idempotency_key: Optional[str] = Header(None, description="Unique key to prevent duplicate sessions"),
) -> ResearchCreateResponse:
    """Start a new research session.
    
    The research workflow runs asynchronously in the background.
    Poll GET /research/{session_id} or subscribe to GET /research/{session_id}/stream 
    for real-time updates.
    """
    # Check idempotency key
    if x_idempotency_key:
        existing_session_id = app_state.get_idempotency_session(x_idempotency_key)
        if existing_session_id:
            session = await orchestrator.get_session(existing_session_id)
            if session:
                logger.info(f"Idempotency key matched existing session {existing_session_id[:8]}")
                response.headers["Location"] = f"/research/{session.session_id}"
                response.status_code = 200  # Already exists
                return ResearchCreateResponse(
                    session_id=session.session_id,
                    status=session.state.value,
                    created=False,
                )
    
    # Extract extension configs (only configured integrations are present)
    extensions = extension_registry.extract_session_extensions(request)

    # Create new session
    session = await orchestrator.start_session(
        request.query,
        extensions=extensions,
        disable_critic=getattr(request, "disable_critic", False),
    )
    
    # Store idempotency key mapping
    if x_idempotency_key:
        app_state.store_idempotency_key(x_idempotency_key, session.session_id)
    
    # Set Location header for RESTful resource creation
    response.headers["Location"] = f"/research/{session.session_id}"
    
    return ResearchCreateResponse(
        session_id=session.session_id,
        status=session.state.value,
        created=True,
    )


@router.get(
    "",
    response_model=ResearchListResponse,
    summary="List research sessions",
    description=(
        "Get a paginated list of research sessions with optional status filtering.\n\n"
        "**Pagination**: Use `limit` and `offset` to paginate results.\n"
        "**Filtering**: Use `status` to filter by session status."
    ),
)
async def list_research_sessions(
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of sessions to return"),
    offset: int = Query(0, ge=0, description="Number of sessions to skip"),
    status: SessionStatusFilter = Query(SessionStatusFilter.ALL, description="Filter by session status"),
) -> ResearchListResponse:
    """Get research sessions with pagination and filtering."""
    all_sessions = orchestrator.session_manager.get_all_sessions()
    active_sessions = await orchestrator.get_active_sessions()
    
    # Apply status filter
    if status == SessionStatusFilter.ALL:
        filtered_sessions = list(all_sessions.values())
    elif status == SessionStatusFilter.ACTIVE:
        filtered_sessions = [s for s in all_sessions.values() if s.is_active]
    else:
        # Map filter value to SessionState
        state_map = {
            SessionStatusFilter.COMPLETED: SessionState.COMPLETED,
            SessionStatusFilter.FAILED: SessionState.FAILED,
            SessionStatusFilter.CANCELLED: SessionState.CANCELLED,
        }
        target_state = state_map.get(status)
        filtered_sessions = [s for s in all_sessions.values() if s.state == target_state]
    
    # Sort by created_at descending (newest first)
    filtered_sessions.sort(key=lambda s: s.created_at, reverse=True)
    
    # Apply pagination
    total_filtered = len(filtered_sessions)
    paginated_sessions = filtered_sessions[offset:offset + limit]
    
    sessions = [
        ResearchStatusResponse(
            session_id=session.session_id,
            status=session.state.value,
            query=session.initial_query,
            created_at=session.created_at,
            updated_at=session.updated_at,
            report=session.current_report,
            cost=_get_session_cost(session.session_id),
            references=_get_session_references(session),
        )
        for session in paginated_sessions
    ]
    
    return ResearchListResponse(
        sessions=sessions,
        total=total_filtered,
        active=len(active_sessions),
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total_filtered,
    )


@router.get(
    "/{session_id}",
    response_model=ResearchStatusResponse,
    summary="Get research session status",
    description="Get the current status and report of a specific research session.",
)
async def get_research_session(
    session_id: str,
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
) -> ResearchStatusResponse:
    """Get the status and report of a research session."""
    session = await orchestrator.get_session(session_id)
    
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found"
        )
    
    return ResearchStatusResponse(
        session_id=session.session_id,
        status=session.state.value,
        query=session.initial_query,
        created_at=session.created_at,
        updated_at=session.updated_at,
        report=session.current_report,
        cost=_get_session_cost(session.session_id),
        references=_get_session_references(session),
    )


@router.get(
    "/{session_id}/stream",
    summary="Stream research session events (SSE)",
    description=(
        "Subscribe to real-time updates for a research session using Server-Sent Events.\n\n"
        "**Event Types**:\n"
        "- `state_change`: Session moved to a new phase\n"
        "- `completed`: Research finished successfully (includes report)\n"
        "- `failed`: Research failed\n"
        "- `cancelled`: Session was cancelled\n\n"
        "**Connection**: Stream stays open until session completes or client disconnects.\n"
        "Initial event sends current state immediately upon connection."
    ),
    responses={
        200: {
            "description": "SSE event stream",
            "content": {"text/event-stream": {}},
        },
        404: {"description": "Session not found"},
    },
)
async def stream_research_session(
    session_id: str,
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
) -> StreamingResponse:
    """Stream real-time updates for a research session using Server-Sent Events."""
    session = await orchestrator.get_session(session_id)
    
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found"
        )
    
    async def event_generator():
        """Generate SSE events for the session."""
        queue = session.subscribe()
        
        try:
            # Send initial state immediately
            initial_data = {
                "session_id": session.session_id,
                "status": session.state.value,
                "query": session.initial_query,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            }
            if session.current_report:
                initial_data["report"] = session.current_report
            
            yield f"event: connected\ndata: {json.dumps(initial_data)}\n\n"
            
            # If session is already complete, send final event and close
            if not session.is_active:
                final_event = "completed" if session.state == SessionState.COMPLETED else session.state.value
                yield f"event: {final_event}\ndata: {json.dumps(initial_data)}\n\n"
                return
            
            # Stream events until session completes
            while True:
                try:
                    # Wait for event with timeout to allow checking connection
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    
                    event_data = event.to_dict()
                    yield f"event: {event_data['event']}\ndata: {json.dumps(event_data['data'])}\n\n"
                    
                    # Close stream when session is no longer active
                    if event.event_type in ("completed", "failed", "cancelled"):
                        break
                        
                except asyncio.TimeoutError:
                    # Send keepalive comment to maintain connection
                    yield ": keepalive\n\n"
                    
                    # Check if session completed while we were waiting
                    if not session.is_active:
                        break
                        
        except asyncio.CancelledError:
            logger.debug(f"SSE stream cancelled for session {session_id[:8]}")
        finally:
            session.unsubscribe(queue)
            logger.debug(f"SSE stream closed for session {session_id[:8]}")
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post(
    "/{session_id}/stop",
    response_model=ResearchStopResponse,
    summary="Stop a research session",
    description="Stop an active research session. The session will be marked as cancelled.",
)
async def stop_research_session(
    session_id: str,
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
) -> ResearchStopResponse:
    """Stop an active research session.
    
    This will cancel the ongoing research workflow and mark the session as cancelled.
    Any partial results will be preserved.
    """
    success = await orchestrator.stop_session(session_id)
    
    if not success:
        # Check if session exists
        session = await orchestrator.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{session_id}' not found"
            )
        # Session exists but couldn't be stopped (already complete/cancelled)
        raise HTTPException(
            status_code=400,
            detail=f"Session '{session_id}' cannot be stopped (status: {session.state.value})"
        )
    
    return ResearchStopResponse(
        session_id=session_id,
        status="cancelled",
        message=f"Research session '{session_id}' has been stopped",
    )


@router.delete(
    "/{session_id}",
    status_code=204,
    summary="Delete a research session",
    description="Delete a completed or cancelled research session from memory.",
)
async def delete_research_session(
    session_id: str,
    orchestrator: DeepResearchAgent = Depends(get_orchestrator),
) -> None:
    """Delete a research session.
    
    Only completed or cancelled sessions can be deleted.
    Active sessions must be stopped first.
    """
    session = await orchestrator.get_session(session_id)
    
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found"
        )
    
    # Check if session is still active
    if session.state not in (SessionState.COMPLETED, SessionState.FAILED, SessionState.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete active session. Stop it first (status: {session.state.value})"
        )
    
    # Remove from session manager
    orchestrator.session_manager.remove_session(session_id)
