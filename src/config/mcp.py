import os
import shutil
from spade_llm.mcp import StdioServerConfig, SseServerConfig, StreamableHttpServerConfig
from src.config.settings import settings

def get_arxiv_mcp_config() -> StdioServerConfig:
    """
    Returns the configuration for the local ArXiv MCP server.
    Uses 'uv' to run the server.
    """
    # Ensure storage path exists
    os.makedirs(settings.ARXIV_STORAGE_PATH, exist_ok=True)
    
    # Check if uv is available
    uv_path = shutil.which("uv")
    if not uv_path:
        uv_path = "uv"

    return StdioServerConfig(
        name="arxiv",
        command=uv_path,
        args=[
            "--directory", "../arxiv-mcp-server",
            "run",
            "arxiv-mcp-server",
            "--storage-path",
            os.path.abspath(settings.ARXIV_STORAGE_PATH)
        ]
    )