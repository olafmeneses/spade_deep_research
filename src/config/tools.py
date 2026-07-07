"""Tool configurations for research agents."""

import logging
import asyncio
import time
from typing import Dict, Any, Literal, Optional
from threading import Lock

from tavily import TavilyClient
from spade_llm.tools import LLMTool
from spade_llm.providers import LLMProvider
from src.telemetry import telemetry_registry
from src.utils.summarizer import summarize_content
from src.references import reference_registry
from src.session import ReferenceSource
from src.tools.session_aware import SessionAwareToolMixin
from src.budgets import SessionBudgetRegistry
from src.config.settings import settings
from src.tavily_cache import TavilySearchCache

logger = logging.getLogger(__name__)
tavily_client = TavilyClient()
tavily_cache = TavilySearchCache(
    path=settings.TAVILY_CACHE_PATH,
    ttl_days=settings.TAVILY_CACHE_TTL_DAYS,
    enabled=settings.ENABLE_TAVILY_CACHE,
)

MIN_SUMMARY_CONTENT_LENGTH = 500
MAX_SUMMARY_INPUT_LENGTH = 15000
DEFAULT_CONCURRENCY_LIMIT = 3
_cache_key_locks: dict[str, asyncio.Lock] = {}
_cache_key_locks_guard = Lock()


def _get_cache_lock(query: str, topic: str) -> asyncio.Lock:
    key = f"{topic}:{' '.join(query.split())}"
    with _cache_key_locks_guard:
        lock = _cache_key_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _cache_key_locks[key] = lock
        return lock


def _register_tavily_references(session_id: Optional[str], items: list[Dict[str, Any]]) -> None:
    if not session_id:
        return

    for result in items:
        url = result.get("url", "")
        title = result.get("title")
        if url:
            reference_registry.register(
                session_id=session_id,
                identifier=url,
                source_type=ReferenceSource.TAVILY,
                title=title,
            )


def _format_tavily_output(items: list[Dict[str, Any]]) -> str:
    if not items:
        return "No results found."

    output = []
    for i, result in enumerate(items, 1):
        summary = result.get("summary", result.get("content", "N/A"))
        output.append(
            f"{i}. **{result.get('title', 'N/A')}**\n"
            f"   URL: {result.get('url', 'N/A')}\n"
            f"   {summary}"
        )

    return "\n\n".join(output)


def _deduplicate_tavily_items(items: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    unique: dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(items):
        key = item.get("url") or f"missing-url-{index}"
        unique[key] = item
    return list(unique.values())


class SessionAwareTavilyTool(SessionAwareToolMixin, LLMTool):
    """Tavily search tool with session-aware reference collection."""
    
    def __init__(self, summary_provider: Optional[LLMProvider] = None,
                 concurrency_limit: int = DEFAULT_CONCURRENCY_LIMIT,
                 owner_id: Optional[str] = None,
                 max_calls_per_agent: Optional[int] = None,
                 max_calls_per_session: Optional[int] = None):
        self.summary_provider = summary_provider
        self.sem = asyncio.Semaphore(concurrency_limit)
        self.owner_id = owner_id
        self.max_calls_per_agent = max_calls_per_agent
        self.max_calls_per_session = max_calls_per_session
        
        super().__init__(
            name="tavily_search",
            description="Search the web for current information using Tavily.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results (default: 5)", "default": 5},
                    "topic": {"type": "string", "enum": ["general", "news", "finance"], "default": "general"}
                },
                "required": ["query"]
            },
            func=self._search
        )
    
    async def _search(self, query: str, max_results: int = 5,
                      topic: Literal["general", "news", "finance"] = "general") -> str:
        """Execute search and register references."""
        logger.info("Tavily search: %s", query)
        started_at = time.perf_counter()

        allowed, reason = SessionBudgetRegistry.try_consume_tavily_call(
            session_id=self._session_id,
            agent_id=self.owner_id,
            max_calls_per_session=self.max_calls_per_session,
            max_calls_per_agent=self.max_calls_per_agent,
        )
        if not allowed:
            logger.warning("Skipping Tavily search for %s: %s", self.owner_id or "unknown-agent", reason)
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.TAVILY.value,
                tool_name=self.name,
                success=False,
                duration_seconds=time.perf_counter() - started_at,
                error=reason,
            )
            return (
                f"Search budget reached. {reason} "
                "Use the evidence already collected and provide the best answer you can without more web searches."
            )

        try:
            cache_lock = _get_cache_lock(query, topic)
            cached_entry = tavily_cache.get(query, topic)
            if cached_entry and cached_entry.requested_max_results >= max_results:
                items = cached_entry.items[:max_results]
                _register_tavily_references(self._session_id, items)
                telemetry_registry.record_tool_call(
                    session_id=self._session_id,
                    source_family=ReferenceSource.TAVILY.value,
                    tool_name=self.name,
                    success=True,
                    duration_seconds=time.perf_counter() - started_at,
                )
                return _format_tavily_output(items)

            async with cache_lock:
                cached_entry = tavily_cache.get(query, topic)
                if cached_entry and cached_entry.requested_max_results >= max_results:
                    items = cached_entry.items[:max_results]
                    _register_tavily_references(self._session_id, items)
                    telemetry_registry.record_tool_call(
                        session_id=self._session_id,
                        source_family=ReferenceSource.TAVILY.value,
                        tool_name=self.name,
                        success=True,
                        duration_seconds=time.perf_counter() - started_at,
                    )
                    return _format_tavily_output(items)

                results = await asyncio.to_thread(
                    tavily_client.search,
                    query=query,
                    max_results=max_results,
                    topic=topic,
                    include_raw_content=bool(self.summary_provider),
                    include_images=False
                )

                items = results.get("results", [])
                if not items:
                    telemetry_registry.record_tool_call(
                        session_id=self._session_id,
                        source_family=ReferenceSource.TAVILY.value,
                        tool_name=self.name,
                        success=True,
                        duration_seconds=time.perf_counter() - started_at,
                    )
                    return "No results found."

                unique = _deduplicate_tavily_items(items)

                if self.summary_provider:
                    await _summarize_results(
                        {result.get("url", f"result-{idx}"): result for idx, result in enumerate(unique)},
                        self.summary_provider,
                        query,
                        self.sem,
                    )

                tavily_cache.set(
                    query=query,
                    topic=topic,
                    requested_max_results=max_results,
                    items=unique,
                )
                items = unique[:max_results]

            _register_tavily_references(self._session_id, items)
            if not items:
                return "No results found."
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.TAVILY.value,
                tool_name=self.name,
                success=True,
                duration_seconds=time.perf_counter() - started_at,
            )
            return _format_tavily_output(items)
            
        except Exception as e:
            logger.exception("Tavily search failed for query: %s", query)
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.TAVILY.value,
                tool_name=self.name,
                success=False,
                duration_seconds=time.perf_counter() - started_at,
                error=str(e),
            )
            return f"Search error: {e}"


def create_tavily_search_tool(
    summary_provider: Optional[LLMProvider] = None,
    concurrency_limit: int = DEFAULT_CONCURRENCY_LIMIT,
    owner_id: Optional[str] = None,
    max_calls_per_agent: Optional[int] = None,
    max_calls_per_session: Optional[int] = None,
) -> LLMTool:
    """Create a session-aware Tavily search tool with optional summarization."""
    return SessionAwareTavilyTool(
        summary_provider,
        concurrency_limit,
        owner_id=owner_id,
        max_calls_per_agent=max_calls_per_agent,
        max_calls_per_session=max_calls_per_session,
    )


async def _summarize_results(results: Dict[str, Any], provider: LLMProvider, query: str, sem: asyncio.Semaphore) -> None:
    """Summarize long content in results using LLM."""
    
    async def process_item(url: str, result: Dict[str, Any]):
        async with sem:
            raw_content = result.get("raw_content", "")
            
            if raw_content and len(raw_content) > MIN_SUMMARY_CONTENT_LENGTH:
                try:
                    prompt = f"Extract key findings from this text related to the research query: '{query}'"
                    summary = await summarize_content(provider, raw_content[:MAX_SUMMARY_INPUT_LENGTH], prompt)
                    if summary:
                        result["summary"] = summary
                except Exception as e:
                    logger.exception("Failed to summarize %s", url)
            
            if "summary" not in result:
                result["summary"] = result.get("content", "No summary available.")

    tasks = [process_item(url, result) for url, result in results.items()]
    await asyncio.gather(*tasks)

    return
