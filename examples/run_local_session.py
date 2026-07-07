"""Run one local deep-research session from the command line.

Example:
    uv run python examples/run_local_session.py "Summarize recent work on Ti-6Al-4V LPBF"
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

import spade
from spade_llm.providers import LLMProvider

from src.config.settings import settings
from src.config.retrieval import RetrievalConfig
from src.agents import AgentManager
from src.orchestrator import DeepResearchAgent


def setup_logging() -> str:
    """Configure logging and return log filename."""
    os.makedirs("logs/deep_research", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"logs/deep_research/session_run_{timestamp}.log"
    
    logging.basicConfig(
        filename=log_filename,
        filemode='w',
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.WARNING,
        force=True 
    )
    logging.getLogger("spade_llm.providers").setLevel(logging.DEBUG)
    logging.getLogger("src").setLevel(logging.DEBUG)
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    print(f"Logging to: {log_filename}")
    return log_filename


async def main(query: str):
    """Run the deep research system."""
    logger = logging.getLogger(__name__)
    logger.info("Initializing Deep Research System...")
    
    # Create LLM providers
    embedding_provider = LLMProvider(model=settings.EMBEDDING_MODEL)
    
    # Initialize retrieval system
    retrieval_config = RetrievalConfig()
    retrieval_agent, retrieval_tool = await retrieval_config.setup(
        embedding_provider=embedding_provider,
        default_k=4,
        timeout=30,
        metadata_fields=["source", "page"]
    )
    
    # Start retrieval agent if available
    if retrieval_agent:
        await retrieval_config.start()
    
    # Use AgentManager as context manager for automatic cleanup
    async with AgentManager(domain=settings.JID_DOMAIN, password=settings.PASSWORD) as manager:
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
        await asyncio.sleep(2)
        
        # Create and start orchestrator
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
        
        # Run research query
        logger.info(f"Starting research: {query}")
        
        session = await orchestrator.start_session(query)
        await orchestrator.wait_for_session(session.session_id, timeout=900)
        print(f"\nSession State: {session.state}")
        
        # Cleanup orchestrator (manager cleanup handled by context exit)
        logger.info("Stopping orchestrator...")
        await orchestrator.stop()
    
    # Stop retrieval agent
    if retrieval_agent:
        await retrieval_config.stop()
    
    logger.info("Done.")


if __name__ == "__main__":
    setup_logging()
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = "Summarize recent research on Ti-6Al-4V ELI laser powder bed fusion."
    try:
        spade.run(main(query))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted by user")
    except Exception as e:
        logging.getLogger(__name__).error(f"Error: {e}", exc_info=True)
