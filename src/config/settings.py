import os
import litellm
from typing import Any
from dotenv import load_dotenv

load_dotenv()

AGENT_TYPES = [
    "arxiv",
    "tavily",
    "knowledge_base",
    "planner",
    "writer",
    "critic",
    "coordinator",
]

def get_env_var(name: str, default: Any = None, required: bool = False) -> Any:
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Environment variable {name} is not set and no default provided.")
    return value


def _env_flag(name: str, default: str = "false") -> bool:
    return str(get_env_var(name, default)).lower() in {"1", "true", "yes", "on"}

# Configure LiteLLM callbacks
_callbacks = get_env_var("LITELLM_CALLBACKS", "").split(",")
_callbacks = [cb.strip() for cb in _callbacks if cb.strip()]
if _callbacks:
    litellm.callbacks = _callbacks

class Settings:
    def __init__(self):
        # APIs
        self.OPENAI_API_KEY = get_env_var("OPENAI_API_KEY", required=True)
        self.TAVILY_API_KEY = get_env_var("TAVILY_API_KEY", required=True)
        self.OPENAI_BASE_URL = get_env_var("OPENAI_BASE_URL")
        
        # XMPP
        self.JID_DOMAIN = get_env_var("JID_DOMAIN", "localhost")
        self.PASSWORD = get_env_var("PASSWORD", "password")
        
        # Storage paths
        self.ARXIV_STORAGE_PATH = get_env_var("ARXIV_STORAGE_PATH", "./data/arxiv_papers")
        self.CHROMADB_PATH = get_env_var("CHROMADB_PATH")
        self.CHROMADB_COLLECTION = get_env_var("CHROMADB_COLLECTION")
        
        # MCP server
        self.ARXIV_MCP_URL = get_env_var("ARXIV_MCP_URL", "http://localhost:8000/mcp")
        
        self.ENABLE_TAVILY_CACHE = _env_flag("ENABLE_TAVILY_CACHE", "true")
        self.TAVILY_CACHE_PATH = get_env_var("TAVILY_CACHE_PATH", "./data/tavily_cache.sqlite")
        self.TAVILY_CACHE_TTL_DAYS = int(get_env_var("TAVILY_CACHE_TTL_DAYS", 21))

        # Timeouts (s)
        self.PLANNER_TIMEOUT = int(get_env_var("PLANNER_TIMEOUT", 120))
        self.RESEARCH_TIMEOUT = int(get_env_var("RESEARCH_TIMEOUT", 600))
        self.WRITER_TIMEOUT = int(get_env_var("WRITER_TIMEOUT", 600))
        self.CRITIC_TIMEOUT = int(get_env_var("CRITIC_TIMEOUT", 180))
        self.SUBAGENT_RESPONSE_TIMEOUT = float(get_env_var("SUBAGENT_RESPONSE_TIMEOUT", 120))
        _subagent_soft_timeout = get_env_var("SUBAGENT_SOFT_TIMEOUT")
        self.SUBAGENT_SOFT_TIMEOUT = (
            float(_subagent_soft_timeout) if _subagent_soft_timeout else None
        )
        self.SUBAGENT_HARD_TIMEOUT = float(
            get_env_var("SUBAGENT_HARD_TIMEOUT", self.SUBAGENT_RESPONSE_TIMEOUT)
        )
        _coordinator_soft_timeout = get_env_var("COORDINATOR_SOFT_TIMEOUT")
        self.COORDINATOR_SOFT_TIMEOUT = (
            float(_coordinator_soft_timeout) if _coordinator_soft_timeout else None
        )
        self.COORDINATOR_HARD_TIMEOUT = float(
            get_env_var("COORDINATOR_HARD_TIMEOUT", self.RESEARCH_TIMEOUT)
        )
        
        # Agent Pool
        self.MAX_PARALLEL = int(get_env_var("MAX_PARALLEL", 3))
        _agents_per_type = get_env_var("AGENTS_PER_TYPE")
        self.AGENTS_PER_TYPE = int(_agents_per_type) if _agents_per_type else self.MAX_PARALLEL
        _max_coord_launches = get_env_var("MAX_COORDINATOR_DELEGATIONS")
        self.MAX_COORDINATOR_DELEGATIONS = (
            int(_max_coord_launches) if _max_coord_launches else None
        )
        self.COORDINATOR_RETURN_RAW_FINDINGS = _env_flag("COORDINATOR_RETURN_RAW_FINDINGS", "false")
        _max_tavily_session_calls = get_env_var("MAX_TAVILY_CALLS_PER_SESSION")
        self.MAX_TAVILY_CALLS_PER_SESSION = (
            int(_max_tavily_session_calls) if _max_tavily_session_calls else None
        )
        _max_tavily_agent_calls = get_env_var("MAX_TAVILY_CALLS_PER_AGENT")
        self.MAX_TAVILY_CALLS_PER_AGENT = (
            int(_max_tavily_agent_calls) if _max_tavily_agent_calls else None
        )
        self.MAX_CRITIC_ITERATIONS = int(get_env_var("MAX_CRITIC_ITERATIONS", 2))
        
        # LLM and embedding models
        # LLM_DEFAULT is the fallback model for agents without specific configuration
        self.LLM_DEFAULT = get_env_var("LLM_DEFAULT", "openai/gpt-4o-mini")
        self.EMBEDDING_MODEL = get_env_var("EMBEDDING_MODEL", "openai/text-embedding-3-small")

        # Map agent types to their specific models (LLM_<AGENT_NAME>)
        self.MODEL_MAP = {
            agent_type: get_env_var(f"LLM_{agent_type.upper()}", self.LLM_DEFAULT)
            for agent_type in AGENT_TYPES
        }

    def get_model_for_agent(self, agent_type: str) -> str:
        """Get the LLM for an agent type."""
        return self.MODEL_MAP.get(agent_type)

settings = Settings()
