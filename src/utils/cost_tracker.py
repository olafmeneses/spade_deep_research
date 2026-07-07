"""Cost tracking for LLM API calls using LiteLLM callbacks.

This module provides a global cost tracker callback that accumulates costs per 
session. Call cost_tracker.register() at startup, then include a session_id
in your LLM call metadata to track costs.
"""

import logging
from threading import Lock
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

import litellm
from litellm.integrations.custom_logger import CustomLogger

from src.telemetry import telemetry_registry

logger = logging.getLogger(__name__)


@dataclass
class SessionCostInfo:
    """Cost information for a single session."""
    total_cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    call_count: int = 0
    pricing_model: Optional[str] = None
    last_response_model: Optional[str] = None
    cost_source: str = "litellm_model_pricing_table"
    last_updated: datetime = field(default_factory=datetime.now)


class CostTrackerCallback(CustomLogger):
    """Custom LiteLLM callback handler for tracking costs per session.
    
    This class extends CustomLogger to receive callbacks for both sync and async
    LLM completions. It extracts session_id from the metadata and accumulates
    costs, tokens, and call counts.
    """
    
    def __init__(self):
        super().__init__()
        self._session_costs: Dict[str, SessionCostInfo] = {}
        self._costs_lock = Lock()
        self._registered = False
        logger.info("CostTrackerCallback initialized")
    
    def _extract_and_track(
        self,
        kwargs: Dict[str, Any],
        response_obj: Any,
    ) -> None:
        """Extract session_id and track costs from a completion response."""
        try:
            # Get session_id from litellm_params.metadata
            litellm_params = kwargs.get("litellm_params", {})
            metadata = litellm_params.get("metadata", {})
            requested_model = kwargs.get("model")
            hidden_params = getattr(response_obj, "_hidden_params", {}) or {}
            
            session_id = metadata.get("session_id")
            
            logger.debug("[CostTracker] Callback triggered")
            
            if not session_id:
                logger.debug("[CostTracker] No session_id in metadata, skipping")
                return
            
            provider_response_cost = hidden_params.get("response_cost")

            # Prefer the provider/gateway-reported LiteLLM response_cost when
            # available, and fall back to local pricing-table estimation.
            try:
                if provider_response_cost is not None:
                    response_cost = float(provider_response_cost)
                    cost_source = "litellm_response_cost"
                else:
                    # Prefer the requested model for pricing. Some OpenAI-compatible
                    # proxies return a different model alias in the response, which
                    # causes LiteLLM to select the wrong price table.
                    response_cost = litellm.completion_cost(
                        completion_response=response_obj,
                        base_model=requested_model,
                    )
                    cost_source = "litellm_model_pricing_table"
            except Exception as e:
                logger.debug(f"[CostTracker] Could not calculate cost: {e}")
                response_cost = kwargs.get("response_cost", 0.0) or 0.0
                cost_source = "litellm_callback_fallback"
            
            # Get token usage from response
            usage = getattr(response_obj, "usage", None)
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            response_model = getattr(response_obj, "model", None)
            
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
                cache_creation_input_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

                prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
                if prompt_tokens_details:
                    cache_read_input_tokens = (
                        getattr(prompt_tokens_details, "cached_tokens", None)
                        or cache_read_input_tokens
                    )
            
            # Accumulate costs for this session
            with self._costs_lock:
                if session_id not in self._session_costs:
                    self._session_costs[session_id] = SessionCostInfo()
                
                info = self._session_costs[session_id]
                info.total_cost += response_cost or 0.0
                info.prompt_tokens += prompt_tokens
                info.completion_tokens += completion_tokens
                info.total_tokens += total_tokens
                info.cache_creation_input_tokens += cache_creation_input_tokens
                info.cache_read_input_tokens += cache_read_input_tokens
                info.call_count += 1
                if requested_model:
                    info.pricing_model = requested_model
                if response_model:
                    info.last_response_model = response_model
                info.cost_source = cost_source
                info.last_updated = datetime.now()
            
            logger.info(
                f"[CostTracker] Session {session_id[:8]}: "
                f"${response_cost:.6f} (total: ${info.total_cost:.6f}, "
                f"calls: {info.call_count}, tokens: {info.total_tokens})"
            )
            telemetry_registry.record_cost(
                session_id=session_id,
                total_cost=info.total_cost,
                prompt_tokens=info.prompt_tokens,
                completion_tokens=info.completion_tokens,
                total_tokens=info.total_tokens,
                cache_creation_input_tokens=info.cache_creation_input_tokens,
                cache_read_input_tokens=info.cache_read_input_tokens,
                call_count=info.call_count,
                pricing_model=info.pricing_model,
                last_response_model=info.last_response_model,
                cost_source=info.cost_source,
            )
            
        except Exception as e:
            logger.warning(f"[CostTracker] Error tracking cost: {e}", exc_info=True)
    
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Sync callback for successful completions."""
        logger.debug("[CostTracker] log_success_event called")
        self._extract_and_track(kwargs, response_obj)
    
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Async callback for successful completions (used by acompletion)."""
        logger.debug("[CostTracker] async_log_success_event called")
        self._extract_and_track(kwargs, response_obj)
    
    def register(self) -> None:
        """Register this callback with LiteLLM.
        
        This should be called once during application startup.
        """
        if self._registered:
            logger.warning("CostTracker callback already registered")
            return
            
        if self not in litellm.callbacks:
            litellm.callbacks.append(self)
            self._registered = True
            logger.info("CostTracker callback registered with LiteLLM")
    
    def get_session_cost(self, session_id: str) -> Optional[SessionCostInfo]:
        """Get cost information for a specific session."""
        with self._costs_lock:
            return self._session_costs.get(session_id)
    
    def clear_session(self, session_id: str) -> None:
        """Clear cost tracking for a session."""
        with self._costs_lock:
            if session_id in self._session_costs:
                del self._session_costs[session_id]
                logger.debug(f"[CostTracker] Cleared session {session_id[:8]}")
    
    def get_all_sessions(self) -> Dict[str, SessionCostInfo]:
        """Get cost information for all tracked sessions."""
        with self._costs_lock:
            return dict(self._session_costs)


# Global cost tracker instance
cost_tracker = CostTrackerCallback()
