"""Application lifespan management."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from pyjabber.server import Server
from pyjabber.server_parameters import Parameters
from spade_llm.providers import LLMProvider

from src.config.settings import settings
from src.config.retrieval import RetrievalConfig
from src.agents import AgentManager
from src.orchestrator import DeepResearchAgent
from src.api.state import app_state
from src.utils.cost_tracker import cost_tracker

logger = logging.getLogger(__name__)


async def _start_xmpp_server() -> None:
    """Start the embedded XMPP server for SPADE agents."""
    logger.info("Starting embedded XMPP server...")
    
    xmpp_server = Server(
        Parameters(host="localhost", database_in_memory=True)
    )
    app_state.xmpp_server = xmpp_server
    app_state.xmpp_task = asyncio.create_task(xmpp_server.start())
    await xmpp_server.ready.wait()
    
    logger.info("Embedded XMPP server ready on localhost:5222")


async def _setup_retrieval_system() -> object | None:
    """Initialize the retrieval system (optional).
    
    Returns the retrieval tool if setup succeeds, None otherwise.
    """
    try:
        embedding_provider = LLMProvider(model=settings.EMBEDDING_MODEL)
        retrieval_config = RetrievalConfig()
        retrieval_agent, retrieval_tool = await retrieval_config.setup(
            embedding_provider=embedding_provider,
            default_k=4,
            timeout=30,
            metadata_fields=["source", "page"]
        )
        app_state.retrieval_config = retrieval_config
        logger.info("Retrieval system initialized")
        return retrieval_tool
    except Exception as e:
        logger.warning(f"Retrieval system not available: {e}")
        app_state.retrieval_config = None
        return None


async def _create_and_start_agents(retrieval_tool: object | None) -> tuple[str, dict]:
    """Create and start all research agents.
    
    Returns:
        Tuple of (coordinator_jid, workflow_jids)
    """
    manager = AgentManager(domain=settings.JID_DOMAIN, password=settings.PASSWORD)
    app_state.agent_manager = manager
    
    # Create research agents
    subagent_jids = []
    subagent_jids.extend(await manager.create_arxiv_agents())
    subagent_jids.extend(await manager.create_tavily_agents())
    
    # Add knowledge base agents if retrieval is available
    if retrieval_tool:
        subagent_jids.extend(await manager.create_knowledge_base_agents(retrieval_tool))

    # Create coordinator and workflow agents
    coordinator_jid = manager.create_coordinator(subagent_jids)
    workflow_jids = manager.create_workflow_agents()
    
    # Start all agents
    await manager.start_all()
    await asyncio.sleep(2)  # Allow agents to initialize
    
    # Start retrieval agent if available
    if app_state.retrieval_config:
        try:
            await app_state.retrieval_config.start()
        except Exception as e:
            logger.warning(f"Failed to start retrieval agent: {e}")
    
    return coordinator_jid, workflow_jids


async def _create_orchestrator(coordinator_jid: str, workflow_jids: dict) -> None:
    """Create and start the orchestrator agent."""
    orchestrator = DeepResearchAgent(
        jid=f"orchestrator@{settings.JID_DOMAIN}",
        password=settings.PASSWORD,
        planner_jid=workflow_jids["planner"],
        coordinator_jid=coordinator_jid,
        writer_jid=workflow_jids["writer"],
        critic_jid=workflow_jids["critic"],
    )
    await orchestrator.start()
    await asyncio.sleep(1)
    orchestrator.agent_manager = app_state.agent_manager
    app_state.orchestrator = orchestrator
    logger.info("Orchestrator agent started")


async def _shutdown() -> None:
    """Gracefully shutdown all components."""
    logger.info("Shutting down Deep Research API...")
    
    # Stop orchestrator
    if app_state.orchestrator:
        await app_state.orchestrator.stop()
    
    # Stop all managed agents
    if app_state.agent_manager:
        await app_state.agent_manager.stop_all()
        app_state.agent_manager.clear()
    
    # Stop retrieval system
    if app_state.retrieval_config:
        with contextlib.suppress(Exception):
            await app_state.retrieval_config.stop()
    
    # Stop XMPP server
    if app_state.xmpp_task and app_state.xmpp_server:
        app_state.xmpp_task.cancel()
        with contextlib.suppress(Exception):
            await app_state.xmpp_task
    
    # Reset state
    app_state.reset()
    
    logger.info("Deep Research API shutdown complete.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and cleanup the SPADE agent system.
    
    This lifespan context manager:
    1. Starts the embedded XMPP server
    2. Sets up the retrieval system (optional)
    3. Creates and starts all research agents
    4. Creates the orchestrator
    5. Handles graceful shutdown on exit
    """
    logger.info("Starting Deep Research API...")
    logger.info(f"XMPP Domain: {settings.JID_DOMAIN}")
    
    try:
        cost_tracker.register()
        
        # Start XMPP server
        await _start_xmpp_server()
        
        # Setup retrieval (optional)
        retrieval_tool = await _setup_retrieval_system()
        
        # Create and start agents
        coordinator_jid, workflow_jids = await _create_and_start_agents(retrieval_tool)
        
        # Create orchestrator
        await _create_orchestrator(coordinator_jid, workflow_jids)
        
        logger.info("Deep Research API ready!")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start Deep Research API: {e}")
        raise
    finally:
        await _shutdown()
