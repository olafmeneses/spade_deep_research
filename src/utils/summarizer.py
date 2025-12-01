from typing import Optional
from spade_llm.providers import LLMProvider
from spade_llm.context import ContextManager
import logging

logger = logging.getLogger(__name__)


async def summarize_content(
    summary_provider: LLMProvider,
    content: str,
    context: Optional[str] = None
) -> Optional[str]:
    """
    Summarize content using an LLM provider with ContextManager.
    
    Args:
        summary_provider: LLM provider instance to use for summarization
        content: The content to summarize
        context: Optional context/instructions for summarization
        
    Returns:
        Summary text or None if request failed
    """
    try:
        system_prompt = """You are an expert at summarizing content concisely and accurately.
Extract the key findings, main points, and important information.
Keep the summary focused and informative."""
        
        # Create a context manager with the system prompt
        context_manager = ContextManager(system_prompt=system_prompt)
        
        if context:
            user_message = f"{context}\n\n---\n\nCONTENT: {content}"
        else:
            user_message = content
        
        context_manager.add_message_dict(
            {"role": "user", "content": user_message},
            conversation_id="summarization"
        )
        
        response = await summary_provider.get_llm_response(context_manager)
        
        if response and response.get('text'):
            logger.info("Successfully generated summary")
            return response['text']
        else:
            logger.warning("LLM returned empty response")
            return None
            
    except Exception as e:
        logger.error(f"Error generating summary: {e}", exc_info=True)
        return None