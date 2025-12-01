import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_env_var(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Environment variable {name} is not set and no default provided.")
    return value

class Settings:
    OPENAI_API_KEY = get_env_var("OPENAI_API_KEY", "")
    TAVILY_API_KEY = get_env_var("TAVILY_API_KEY", "")
    OLLAMA_BASE_URL = get_env_var("OLLAMA_BASE_URL", "")
    
    JID_DOMAIN = get_env_var("JID_DOMAIN", "localhost")
    PASSWORD = get_env_var("PASSWORD", "password")
    
    ARXIV_STORAGE_PATH = get_env_var("ARXIV_STORAGE_PATH", "./data/arxiv_papers")

settings = Settings()

