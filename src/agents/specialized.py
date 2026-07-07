"""Specialized LLM agents with session tracking support.

All agents extend SessionAwareLLMAgent for automatic session tracking.
"""

import re
from typing import Dict, Optional, Tuple, List

from spade_llm.agent import LLMAgent
from spade_llm.providers import LLMProvider
from spade_llm.tools import LLMTool

from src.config import prompts
from src.config.mcp import get_arxiv_mcp_config
from src.config.mcp_adapters import wrap_arxiv_mcp_tools
from src.config.settings import settings
from src.config.tools import create_tavily_search_tool
from src.config.schemas import ResearchPlan, CriticReview, FindingsSummary
from src.behaviors import apply_session_tracking
from src.session import Reference
from src.extensions import extension_registry

import logging

logger = logging.getLogger(__name__)


def _build_agent_mention_pattern() -> re.Pattern:
    """Build a regex pattern matching any registered agent name in JID format."""
    names = extension_registry.agent_names_list().replace(", ", "|")
    return re.compile(rf'\b(?:{names})@\w+\b', re.IGNORECASE)


# Agent mention pattern — built dynamically from the extension registry
AGENT_MENTION_PATTERN = _build_agent_mention_pattern()


def resolve_citations(report: str, references: List[Reference]) -> str:
    """Replace numbered citations [n] with markdown links to source URLs.

    Each ``[n]`` that matches a valid reference is replaced with a markdown
    link of the form ``[[n] Title](URL)`` (or ``[[n]](URL)`` when no title
    is available).  Citations that fall outside the valid range are left
    untouched.

    Args:
        report: The report text containing ``[n]`` citations.
        references: Ordered list of references (1-indexed in the report).

    Returns:
        The report with resolved markdown citation links.
    """
    if not references or not report:
        return report

    def _replace(match: re.Match) -> str:
        num = int(match.group(1))
        if num < 1 or num > len(references):
            return match.group(0)  # leave out-of-range citations as-is
        ref = references[num - 1]
        label = f"[{num}]"
        url = ref.identifier
        if ref.title:
            title = ref.title
            if len(title) > 50:
                title = title[:47] + "..."
            label = f"[{num}: {title}]"
        return f"[{label}]({url})"

    return re.sub(r'\[(\d+)\]', _replace, report)


def validate_report_citations(report: str, references: List[Reference]) -> Tuple[List[str], List[str]]:
    """Validate report for agent mentions and invalid citations.

    Returns:
        Tuple of (agent_mentions, invalid_citations)
    """
    issues: List[str] = []
    invalid_citations: List[str] = []

    # Check for agent@host mentions
    agent_mentions = AGENT_MENTION_PATTERN.findall(report)
    if agent_mentions:
        issues.extend([f"Report mentions internal agent: {mention}" for mention in agent_mentions])

    # Extract citation numbers from report [1], [2], etc.
    citation_pattern = re.compile(r'\[(\d+)\]')
    used_citations = {int(match) for match in citation_pattern.findall(report)}

    # Check if cited numbers are within valid range
    max_ref = len(references)
    for cite_num in used_citations:
        if cite_num < 1 or cite_num > max_ref:
            invalid_citations.append(
                f"Citation [{cite_num}] references non-existent source (only {max_ref} references available)"
            )

    return issues, invalid_citations


class SessionAwareLLMAgent(LLMAgent):
    """LLMAgent with automatic session tracking for concurrent sessions."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session_map: Dict[str, str] = {}
    
    async def setup(self) -> None:
        await super().setup()
        if hasattr(self, 'llm_behaviour'):
            apply_session_tracking(self, 'llm_behaviour')


class ArXivAgent(SessionAwareLLMAgent):
    """Agent for searching academic papers on arXiv."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.ARXIV_AGENT_PROMPT,
            mcp_servers=[get_arxiv_mcp_config()],
            output_schema=FindingsSummary,
            **kwargs
        )
    
    async def setup(self) -> None:
        """Setup agent with MCP tools wrapped for reference extraction."""
        await super().setup()
        
        # Wrap MCP tools with session-aware reference extraction
        if hasattr(self, 'tools') and self.tools:
            self.tools = wrap_arxiv_mcp_tools(self.tools)
            # Also update the behaviour's tools
            if hasattr(self, 'llm_behaviour'):
                self.llm_behaviour.tools = self.tools
            logger.info(f"[{self.jid}] ArXiv MCP tools wrapped for reference extraction")


class TavilyAgent(SessionAwareLLMAgent):
    """Agent for web search using Tavily API."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider, 
                 summary_provider: Optional[LLMProvider] = None, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.TAVILY_AGENT_PROMPT,
            tools=[
                create_tavily_search_tool(
                    summary_provider,
                    owner_id=jid,
                    max_calls_per_agent=settings.MAX_TAVILY_CALLS_PER_AGENT,
                    max_calls_per_session=settings.MAX_TAVILY_CALLS_PER_SESSION,
                )
            ],
            output_schema=FindingsSummary,
            **kwargs
        )


class KnowledgeBaseAgent(SessionAwareLLMAgent):
    """Agent for document retrieval from local knowledge base."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider,
                 retrieval_tool: LLMTool, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.KNOWLEDGE_BASE_AGENT_PROMPT,
            tools=[retrieval_tool],
            output_schema=FindingsSummary,
            **kwargs
        )
        logger.info(f"KnowledgeBaseAgent initialized: {jid}")


class PlannerAgent(SessionAwareLLMAgent):
    """Agent for generating research plans."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.PLANNER_SYSTEM_PROMPT,
            output_schema=ResearchPlan,
            **kwargs
        )


class WriterAgent(SessionAwareLLMAgent):
    """Agent for writing research reports."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.WRITER_SYSTEM_PROMPT,
            **kwargs
        )


class CriticAgent(SessionAwareLLMAgent):
    """Agent for reviewing and critiquing research reports."""
    
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid, password=password, provider=provider,
            system_prompt=prompts.CRITIC_SYSTEM_PROMPT,
            output_schema=CriticReview,
            **kwargs
        )
