from typing import Optional
from spade_llm.agent import LLMAgent
from spade_llm.providers import LLMProvider
from src.config import prompts
from src.config.mcp import get_arxiv_mcp_config
from src.config.tools import create_tavily_search_tool

class ArXivAgent(LLMAgent):
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=prompts.ARXIV_AGENT_PROMPT,
            mcp_servers=[get_arxiv_mcp_config()],
            **kwargs
        )

class TavilyAgent(LLMAgent):
    def __init__(self, jid: str, password: str, provider: LLMProvider, summary_provider=None, **kwargs):
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=prompts.TAVILY_AGENT_PROMPT,
            tools=[create_tavily_search_tool(summary_provider=summary_provider)],
            **kwargs
        )

class PlannerAgent(LLMAgent):
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=prompts.PLANNER_SYSTEM_PROMPT,
            **kwargs
        )

class WriterAgent(LLMAgent):
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=prompts.WRITER_SYSTEM_PROMPT,
            **kwargs
        )

class CriticAgent(LLMAgent):
    def __init__(self, jid: str, password: str, provider: LLMProvider, **kwargs):
        super().__init__(
            jid=jid,
            password=password,
            provider=provider,
            system_prompt=prompts.CRITIC_SYSTEM_PROMPT,
            **kwargs
        )
