"""Agent manager for creating and managing agent lifecycle."""

import logging
from typing import Dict, List

from spade_llm.providers import LLMProvider

from src.config.settings import settings
from src.agents import (
    ArXivAgent, TavilyAgent, KnowledgeBaseAgent,
    PlannerAgent, WriterAgent, CriticAgent,
    SessionAwareCoordinatorAgent,
)

logger = logging.getLogger(__name__)


class AgentManager:
    """Manager for creating and supervising research agent lifecycle.
    
    This is a Manager/Supervisor pattern that maintains references to agents
    and manages their lifecycle. Can be used as an async context manager for
    automatic cleanup.
    
    Example:
        async with AgentManager() as manager:
            jids = await manager.create_arxiv_agents()
            # Agents automatically started and will be stopped on exit
    """
    
    def __init__(self, domain: str = None, password: str = None, auto_start: bool = False):
        """Initialize the manager with XMPP credentials.
        
        Args:
            domain: XMPP domain (defaults to settings.JID_DOMAIN)
            password: Agent password (defaults to settings.PASSWORD)
            auto_start: If True, agents are started immediately upon creation
        """
        self.domain = domain or settings.JID_DOMAIN
        self.password = password or settings.PASSWORD
        self.auto_start = auto_start
        self.agents: Dict[str, any] = {}
        self._started = False
        
    def _create_provider(self, agent_type: str) -> LLMProvider:
        """Create an LLM provider with the appropriate model for an agent type."""
        model = settings.get_model_for_agent(agent_type)
        return LLMProvider(model=model)
    
    async def create_arxiv_agents(self, count: int = None) -> List[str]:
        """Create ArXiv research agents.
        
        Args:
            count: Number of agents to create (defaults to settings.AGENTS_PER_TYPE,
                which defaults to settings.MAX_PARALLEL)
            
        Returns:
            List of created agent JIDs
        """
        count = count or settings.AGENTS_PER_TYPE
        provider = self._create_provider("arxiv")
        jids = []
        
        for i in range(1, count + 1):
            jid = f"arxiv_{i}@{self.domain}"
            agent = ArXivAgent(
                jid, self.password, provider,
                soft_timeout=settings.SUBAGENT_SOFT_TIMEOUT,
                hard_timeout=settings.SUBAGENT_HARD_TIMEOUT,
            )
            self.agents[f"arxiv_{i}"] = agent
            jids.append(jid)
            logger.info(f"Created ArXivAgent: {jid}")
            
            if self.auto_start:
                await agent.start()
                logger.info(f"Auto-started ArXivAgent: {jid}")
            
        return jids
    
    async def create_tavily_agents(self, count: int = None) -> List[str]:
        """Create Tavily search agents.
        
        Args:
            count: Number of agents to create (defaults to settings.AGENTS_PER_TYPE,
                which defaults to settings.MAX_PARALLEL)
            
        Returns:
            List of created agent JIDs
        """
        count = count or settings.AGENTS_PER_TYPE
        provider = self._create_provider("tavily")
        jids = []
        
        for i in range(1, count + 1):
            jid = f"tavily_{i}@{self.domain}"
            agent = TavilyAgent(
                jid, self.password, provider,
                soft_timeout=settings.SUBAGENT_SOFT_TIMEOUT,
                hard_timeout=settings.SUBAGENT_HARD_TIMEOUT,
            )
            self.agents[f"tavily_{i}"] = agent
            jids.append(jid)
            logger.info(f"Created TavilyAgent: {jid}")
            
            if self.auto_start:
                await agent.start()
                logger.info(f"Auto-started TavilyAgent: {jid}")
            
        return jids
    
    async def create_knowledge_base_agents(self, retrieval_tool, count: int = None) -> List[str]:
        """Create knowledge base research agents.
        
        Args:
            retrieval_tool: Configured retrieval tool for document search
            count: Number of agents to create (defaults to settings.AGENTS_PER_TYPE,
                which defaults to settings.MAX_PARALLEL)
            
        Returns:
            List of created agent JIDs
        """
        count = count or settings.AGENTS_PER_TYPE
        provider = self._create_provider("knowledge_base")
        jids = []
        
        for i in range(1, count + 1):
            jid = f"knowledge_base_{i}@{self.domain}"
            agent = KnowledgeBaseAgent(
                jid, self.password, provider, retrieval_tool,
                soft_timeout=settings.SUBAGENT_SOFT_TIMEOUT,
                hard_timeout=settings.SUBAGENT_HARD_TIMEOUT,
            )
            self.agents[f"knowledge_base_{i}"] = agent
            jids.append(jid)
            logger.info(f"Created KnowledgeBaseAgent: {jid}")
            
            if self.auto_start:
                await agent.start()
                logger.info(f"Auto-started KnowledgeBaseAgent: {jid}")
            
        return jids

    def create_coordinator(self, subagent_jids: List[str]) -> str:
        """Create the coordinator agent.
        
        Args:
            subagent_jids: List of research agent JIDs to coordinate
            
        Returns:
            Coordinator agent JID
        """
        provider = self._create_provider("coordinator")
        jid = f"coordinator@{self.domain}"
        
        coordinator = SessionAwareCoordinatorAgent(
            jid=jid,
            password=self.password,
            subagent_ids=subagent_jids,
            provider=provider,
            max_parallel=settings.MAX_PARALLEL,
            soft_timeout=settings.COORDINATOR_SOFT_TIMEOUT,
            hard_timeout=settings.COORDINATOR_HARD_TIMEOUT,
            max_delegations=settings.MAX_COORDINATOR_DELEGATIONS,
            return_raw_findings=settings.COORDINATOR_RETURN_RAW_FINDINGS,
            subagent_response_timeout=settings.SUBAGENT_RESPONSE_TIMEOUT,
        )
        self.agents["coordinator"] = coordinator
        
        logger.info(f"Created Coordinator: {jid}")
        return jid
    
    def create_workflow_agents(self) -> Dict[str, str]:
        """Create planner, writer, and critic agents.
        
        Returns:
            Dictionary mapping agent role to JID
        """
        jids = {}
        
        # Planner
        provider = self._create_provider("planner")
        jid = f"planner@{self.domain}"
        self.agents["planner"] = PlannerAgent(jid, self.password, provider)
        jids["planner"] = jid
        logger.info(f"Created PlannerAgent: {jid}")
        
        # Writer
        provider = self._create_provider("writer")
        jid = f"writer@{self.domain}"
        self.agents["writer"] = WriterAgent(jid, self.password, provider)
        jids["writer"] = jid
        logger.info(f"Created WriterAgent: {jid}")
        
        # Critic
        provider = self._create_provider("critic")
        jid = f"critic@{self.domain}"
        self.agents["critic"] = CriticAgent(jid, self.password, provider)
        jids["critic"] = jid
        logger.info(f"Created CriticAgent: {jid}")
        
        return jids
    
    async def start_all(self) -> None:
        """Start all created agents."""
        if self._started:
            logger.warning("Agents already started")
            return
            
        for name, agent in self.agents.items():
            await agent.start()
            logger.info(f"Started agent: {name}")
        self._started = True
    
    async def stop_all(self) -> None:
        """Stop all created agents."""
        for name, agent in self.agents.items():
            try:
                await agent.stop()
                logger.info(f"Stopped agent: {name}")
            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")
        self._started = False
    
    def clear(self) -> None:
        """Clear all agent references (use after stopping to prevent memory leaks)."""
        self.agents.clear()
        logger.info("Cleared all agent references")
    
    def get_agent(self, name: str):
        """Get an agent by name."""
        return self.agents.get(name)
    
    def get_all_agents(self) -> Dict[str, any]:
        """Get all created agents."""
        return self.agents
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures cleanup."""
        logger.info("AgentManager context exiting, stopping all agents...")
        await self.stop_all()
        self.clear()
        return False
