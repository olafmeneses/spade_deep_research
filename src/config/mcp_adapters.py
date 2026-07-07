"""Custom MCP tool adapters with reference extraction.

Since spade_llm creates MCP tools internally, we provide wrappers that can be applied
to intercept tool results and extract references.
"""

import re
import logging
import time
from typing import Any, Optional, List

from spade_llm.tools import LLMTool

from src.telemetry import telemetry_registry
from src.references import reference_registry
from src.session import ReferenceSource
from src.tools.session_aware import SessionAwareToolMixin

logger = logging.getLogger(__name__)


class SessionAwareMCPToolWrapper(SessionAwareToolMixin):
    """Wrapper that adds session awareness to any MCP tool.
    
    Intercepts execute() calls to extract references from results.
    This is used to wrap MCP tools after they're created by spade_llm.
    """
    
    def __init__(self, wrapped_tool: LLMTool, reference_extractor: Optional[callable] = None):
        """Initialize the wrapper.
        
        Args:
            wrapped_tool: The original MCP tool to wrap
            reference_extractor: Optional function that extracts references from tool result.
                Signature: (session_id: str, result: Any) -> None
        """
        self._wrapped_tool = wrapped_tool
        self._reference_extractor = reference_extractor
        
    async def execute(self, **kwargs) -> Any:
        """Execute the wrapped tool and extract references."""
        started_at = time.perf_counter()
        try:
            result = await self._wrapped_tool.execute(**kwargs)

            if self._reference_extractor and self._session_id and result:
                try:
                    self._reference_extractor(self._session_id, result)
                except Exception as e:
                    logger.warning(f"Failed to extract references: {e}")

            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.ARXIV.value,
                tool_name=self.name,
                success=True,
                duration_seconds=time.perf_counter() - started_at,
            )
            return result
        except Exception as exc:
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.ARXIV.value,
                tool_name=self.name,
                success=False,
                duration_seconds=time.perf_counter() - started_at,
                error=str(exc),
            )
            raise
    
    # Delegate all other attributes to the wrapped tool
    def __getattr__(self, name):
        return getattr(self._wrapped_tool, name)
    
    @property
    def name(self):
        return self._wrapped_tool.name
    
    @property
    def description(self):
        return self._wrapped_tool.description
    
    @property
    def parameters(self):
        return self._wrapped_tool.parameters


def extract_arxiv_references(session_id: str, result: Any) -> None:
    """Extract arxiv references from MCP tool results.
    
    The arxiv-mcp-server returns structured data with paper metadata.
    We parse the result to extract arxiv IDs, titles, and URLs.
    """
    if not session_id or not result:
        return
    
    # Handle dict results (MCP tools return {'type': 'text', 'text': '...'})
    if isinstance(result, dict) and 'text' in result:
        text = result['text']
    else:
        text = str(result)
    
    # Try to parse as JSON first (arxiv-mcp-server returns JSON)
    import json
    try:
        data = json.loads(text)
        if isinstance(data, dict) and 'papers' in data:
            # Structured format from arxiv-mcp-server
            for paper in data['papers']:
                arxiv_id = paper.get('id', '')
                title = paper.get('title', '')
                url = f"https://arxiv.org/abs/{arxiv_id}"
                
                if arxiv_id:
                    reference_registry.register(
                        session_id=session_id,
                        identifier=url,
                        source_type=ReferenceSource.ARXIV,
                        title=title or None
                    )
            return
    except (json.JSONDecodeError, KeyError):
        pass
    

def wrap_arxiv_mcp_tools(tools: List[LLMTool]) -> List[LLMTool]:
    """Wrap ArXiv MCP tools with session-aware reference extraction.
    
    Args:
        tools: List of LLMTools (some may be MCP tools from arxiv server)
        
    Returns:
        List of tools with arxiv tools wrapped for reference extraction
    """
    wrapped = []
    # Tool names from arxiv-mcp-server
    arxiv_tool_names = {'arxiv_search_papers', 'arxiv_download_paper', 'arxiv_read_paper',
                        'arxiv_read_paper_toc', 'arxiv_read_paper_section'}
    
    for tool in tools:
        base_name = tool.name
        if base_name.startswith("arxiv_") and base_name[len("arxiv_"):] in arxiv_tool_names:
            base_name = base_name[len("arxiv_"):]

        if base_name in arxiv_tool_names:
            if base_name == 'arxiv_search_papers':
                wrapped.append(SessionAwareMCPToolWrapper(tool, extract_arxiv_references))
            else:
                wrapped.append(SessionAwareMCPToolWrapper(tool))
            logger.debug(f"Wrapped arxiv tool: {tool.name}")
        else:
            wrapped.append(tool)
    
    return wrapped
