"""
Chat Interface for Deep Research System with FSM
Allows interactive chat with the DeepResearchAgent that uses the FSM workflow
"""
import os
from datetime import datetime
import logging
import asyncio
import spade
from rich.console import Console
from rich.markdown import Markdown

from spade_llm.providers import LLMProvider
from spade_llm.agent import ChatAgent, CoordinatorAgent
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
log_filename = f"logs/deep_research/chat_{timestamp}.log"

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

console = Console()

def print_rich(message: str, sender_jid: str):
    console.print("\n[bold green]Agent Response:[/bold green]")
    md = Markdown(message)
    console.print(md)
    console.print()

async def main():
    logger.info("Initializing Deep Research Chat System...")
    
    provider = LLMProvider.create_ollama(
        base_url=settings.OLLAMA_BASE_URL,
        model="gpt-oss:20b"
    )
    
    domain = settings.JID_DOMAIN
    password = settings.PASSWORD
    
    arxiv_jid = f"arxiv_chat@{domain}"
    tavily_jid = f"tavily_chat@{domain}"
    planner_jid = f"planner_chat@{domain}"
    writer_jid = f"writer_chat@{domain}"
    critic_jid = f"critic_chat@{domain}"
    coordinator_jid = f"coordinator_chat@{domain}"
    orchestrator_jid = f"orchestrator_chat@{domain}"
    chat_jid = f"chat_deep_research@{domain}"
    
    logger.info("Creating Research Sub-Agents...")
    arxiv_agent = ArXivAgent(arxiv_jid, password, provider)
    tavily_agent = TavilyAgent(tavily_jid, password, provider, summary_provider=None)
    
    logger.info("Creating Coordinator Agent...")
    coordinator = CoordinatorAgent(
        jid=coordinator_jid,
        password=password,
        subagent_ids=[tavily_jid, arxiv_jid],
        provider=provider,
        coordination_session="deep_research_chat_session"
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

    logger.info("Creating DeepResearchAgent orchestrator...")
    
    async def async_input(prompt: str) -> str:
        print(prompt, end='', flush=True)
        return await asyncio.to_thread(input)
    
    orchestrator = DeepResearchAgent(
        jid=orchestrator_jid,
        password=password,
        user_query="",  # Will receive queries from ChatAgent
        planner_jid=planner_jid,
        coordinator_jid=coordinator_jid,
        writer_jid=writer_jid,
        critic_jid=critic_jid,
        input_func=async_input
    )
    
    await orchestrator.start()
    
    logger.info("Creating ChatAgent for interactive communication...")
    chat_agent = ChatAgent(
        jid=chat_jid,
        target_agent_jid=orchestrator_jid,
        password=password,
        display_callback=print_rich
    )
    
    await chat_agent.start()
    
    logger.info("Starting interactive chat session...")
    await chat_agent.run_interactive(response_timeout=600)
    
    logger.info("Stopping all agents...")
    await chat_agent.stop()
    await orchestrator.stop()
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
        print("\nGoodbye!")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        print(f"\nError: {e}")
