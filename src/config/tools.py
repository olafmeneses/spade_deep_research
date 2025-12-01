import logging
import asyncio
from tavily import TavilyClient
from typing import List, Dict, Any, Literal
from src.utils.summarizer import summarize_content as summarize_with_llm

tavily_client = TavilyClient()

def create_tavily_search_tool(summary_provider = None):
    """
    Create a Tavily search tool for the given summary_provider.
    
    Args:
        summary_provider: Optional LLM provider for content summarization
    
    Returns:
        LLMTool configured for Tavily search
    """
    from spade_llm.tools import LLMTool
    
    async def tavily_search_impl(
        query: str,
        max_results: int = 3,
        topic: Literal["general", "news", "finance"] = "general"
    ) -> str:
        logging.info(f"Tavily search called with query: {query}, max_results: {max_results}, topic: {topic}")
        
        try:
            results = await tavily_search(
                query=query,
                max_results=max_results,
                topic=topic,
                summary_provider=summary_provider,
            )
            
            if not results:
                logging.warning(f"No results found for query: {query}")
                return "No results found for your search query."
            
            formatted_results = []
            for i, result in enumerate(results, 1):
                formatted_results.append(
                    f"Document {i}. **{result.get('title', 'N/A')}**\n"
                    f"   URL: {result.get('url', 'N/A')}\n"
                    f"   Summary: {result.get('summary', 'N/A')}"
                )
            
            result_str = "\n\n".join(formatted_results)
            logging.info(f"Returning {len(results)} results")
            return result_str
        except Exception as e:
            logging.error(f"Error in tavily_search_impl: {e}", exc_info=True)
            return f"Error performing search: {str(e)}"
    
    return LLMTool(
        name="tavily_search",
        description="Search the web for current information using Tavily. Returns formatted results with titles, URLs, and summaries.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 3)",
                    "default": 3
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news", "finance"],
                    "description": "Search topic category (default: general)",
                    "default": "general"
                }
            },
            "required": ["query"]
        },
        func=tavily_search_impl
    )

async def summarize_content(
    results: Dict[str, Any],
    summary_provider = None
) -> Dict[str, Any]:
    """
    Summarize the content of each search result using an LLM provider directly.
    Modifies the results dictionary in place to add a 'summary' field.
    
    Args:
        results: Dictionary of search results keyed by URL
        summary_provider: LLM provider for summarization (optional)
    
    Returns:
        Modified list of results
    """
    for url, result in results.items():
        raw_content = result.get("raw_content", "")
        summary = None

        if raw_content and summary_provider:
            if len(raw_content) > 500:  # Summarize if content is long
                try:
                    summary = await summarize_with_llm(
                        summary_provider=summary_provider,
                        content=raw_content,
                        context="Extract key findings and main points from this web content"
                    )
                except Exception as e:
                    logging.error(f"Error summarizing content from {url}: {e}")
            else:
                summary = raw_content

        if summary:
            result["summary"] = summary
            logging.info(f"Summarized content from {url}")
        else:
            result["summary"] = result.get("content", "") # Fallback to original content
            logging.info(f"Using original content from {url} as summary could not be obtained")
    
    return results

async def tavily_search(
    query: str,
    max_results: int = 3,
    topic: Literal["general", "news", "finance"] = "general",
    summary_provider = None
) -> List[Dict[str, Any]]:
    """
    Perform a search using the Tavily API.
    
    Args:
        query: Search query
        max_results: Maximum number of results
        topic: Search topic category
        summary_provider: Optional LLM provider for content summarization
    
    Returns:
        List of search results. If summary_provider is provided,
        results will have 'summary' field. Otherwise, 'content' field is used.
    """
    try:
        logging.info(f"Starting Tavily search for: {query}")
        
        # Run the synchronous tavily_client.search() in a thread pool
        results = await asyncio.to_thread(
            tavily_client.search,
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=True,
            include_images=False
        )
        
        logging.info(f"Tavily returned {len(results.get('results', []))} results")

        unique_results = {}
        for result in results.get("results", []):
            url = result.get("url")
            if url not in unique_results:
                unique_results[url] = result
        
        logging.info(f"Processing {len(unique_results)} unique results")
        
        # Summarize content if summary_provider available
        await summarize_content(unique_results, summary_provider)
        
        final_results = list(unique_results.values())
        logging.info(f"Returning {len(final_results)} results")
        return final_results
    except Exception as e:
        logging.error(f"Error during Tavily search: {e}", exc_info=True)
        return []