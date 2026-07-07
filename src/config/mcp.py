import os
from spade_llm.mcp import StreamableHttpServerConfig
from src.config.settings import settings

def get_arxiv_mcp_config() -> StreamableHttpServerConfig:
    """Return the configuration for the ArXiv MCP server."""
    os.makedirs(settings.ARXIV_STORAGE_PATH, exist_ok=True)

    return StreamableHttpServerConfig(
        name="arxiv",
        url=settings.ARXIV_MCP_URL
    )