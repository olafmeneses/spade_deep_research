import os
from datetime import datetime
import asyncio
import logging
import sys
import spade
from spade_llm.providers import LLMProvider
from spade_llm.agent.coordinator_agent import CoordinatorAgent
from src.config.settings import settings
from src.agents import (
    ArXivAgent, 
    TavilyAgent, 
    PlannerAgent, 
    WriterAgent, 
    CriticAgent,
)
from src.agent import DeepResearchAgent

# Configure logging
os.makedirs("logs/deep_research", exist_ok=True)
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_filename = f"logs/deep_research/run_{timestamp}.log"

logging.basicConfig(
    filename=log_filename,
    filemode='w',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING,
    force=True 
)
logging.getLogger("spade_llm.providers").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
print(f"Logging to: {log_filename}")

async def async_input(prompt: str) -> str:
    print(prompt, end='', flush=True)
    return await asyncio.to_thread(sys.stdin.readline)

async def main():
    logger.info("Initializing Deep Research System...")
    
    provider = LLMProvider.create_ollama(
        base_url=settings.OLLAMA_BASE_URL,
        model="gpt-oss:20b"
    )
    
    domain = settings.JID_DOMAIN
    password = settings.PASSWORD
    
    arxiv_jid = f"arxiv@{domain}"
    tavily_jid = f"tavily@{domain}"
    planner_jid = f"planner@{domain}"
    writer_jid = f"writer@{domain}"
    critic_jid = f"critic@{domain}"
    coordinator_jid = f"coordinator@{domain}"
    orchestrator_jid = f"orchestrator@{domain}"
    
    logger.info("Creating Research Sub-Agents...")
    arxiv_agent = ArXivAgent(arxiv_jid, password, provider)
    tavily_agent = TavilyAgent(tavily_jid, password, provider)
    
    logger.info("Creating Coordinator Agent...")
    coordinator = CoordinatorAgent(
        jid=coordinator_jid,
        password=password,
        subagent_ids=[tavily_jid, arxiv_jid],
        provider=provider,
        coordination_session="deep_research_session"
    )
    
    logger.info("Creating Specialized Agents...")
    planner = PlannerAgent(planner_jid, password, provider)
    writer = WriterAgent(writer_jid, password, provider)
    critic = CriticAgent(critic_jid, password, provider)
    
    await arxiv_agent.start()
    await tavily_agent.start()
    await coordinator.start()
    await planner.start()
    await writer.start()
    await critic.start()
    
    logger.info("Waiting for agents to come online...")
    await asyncio.sleep(2)
    
    # Orchestrator (User Interaction)
    query = await async_input("\nEnter your research query: ")
    query = query.strip()
    
    logger.info(f"Starting Deep Research for: {query}")
    
    orchestrator = DeepResearchAgent(
        jid=orchestrator_jid,
        password=password,
        user_query=query,
        planner_jid=planner_jid,
        coordinator_jid=coordinator_jid,
        writer_jid=writer_jid,
        critic_jid=critic_jid,
        input_func=async_input
    )
    
    await orchestrator.start()
    
    while orchestrator.is_alive():
        await asyncio.sleep(1)
        
    logger.info("Stopping all agents...")
    
    await arxiv_agent.stop()
    await tavily_agent.stop()
    await coordinator.stop()
    await planner.stop()
    await writer.stop()
    await critic.stop()
    
    logger.info("All agents stopped.")

if __name__ == "__main__":
    try:
        spade.run(main())
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)

