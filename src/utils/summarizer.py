"""Content summarization using LLM."""

import logging
from typing import Optional

from spade_llm.providers import LLMProvider
from spade_llm.context import ContextManager

logger = logging.getLogger(__name__)

SUMMARIZER_PROMPT = """You are an expert at summarizing content concisely.
Extract key findings, main points, and important information.
Keep summaries focused and informative."""


async def summarize_content(
    summary_provider: LLMProvider,
    content: str,
    context: Optional[str] = None
) -> Optional[str]:
    """Summarize content using an LLM provider."""
    try:
        ctx = ContextManager(system_prompt=SUMMARIZER_PROMPT)
        
        message = f"{context}\n\n---\n\n{content}" if context else content
        ctx.add_message_dict({"role": "user", "content": message}, conversation_id="summarization")
        
        response = await summary_provider.get_llm_response(ctx)
        
        if response and response.get('text'):
            return response['text']
        return None
        
    except Exception as e:
        logger.error(f"Summarization error: {e}", exc_info=True)
        return None
