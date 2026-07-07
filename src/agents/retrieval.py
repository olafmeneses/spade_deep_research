"""Retrieval agent for document search using ChromaDB vector store.

Provides document retrieval capabilities via spade_llm's RAG components.
"""

import json
import logging
import time
from typing import Optional, Callable, List, Dict

from spade_llm import RetrievalAgent, RetrievalTool
from spade_llm.rag import Chroma, VectorStoreRetriever

from src.telemetry import telemetry_registry
from src.behaviors import apply_session_tracking
from src.references import reference_registry
from src.session import ReferenceSource
from src.tools.session_aware import SessionAwareToolMixin

logger = logging.getLogger(__name__)


class SessionAwareRetrievalTool(SessionAwareToolMixin):
    """Wrapper that adds session awareness to RetrievalTool for reference extraction."""
    
    def __init__(self, retrieval_tool: RetrievalTool, metadata_fields: Optional[List[str]] = None):
        self._wrapped_tool = retrieval_tool
        self._metadata_fields = metadata_fields
        self._apply_reference_extraction()
    
    def _apply_reference_extraction(self):
        """Patch the format_response to extract references."""
        original_format = getattr(self._wrapped_tool, '_format_response', None)
        wrapper = self
        
        def format_with_references(response_body: str) -> str:
            # Extract references from response
            if wrapper._session_id:
                try:
                    data = json.loads(response_body)
                    for doc in data.get("documents", []):
                        metadata = doc.get("metadata", {})
                        source = metadata.get("source", "")
                        filename = metadata.get("filename", "")
                        if source:
                            reference_registry.register(
                                session_id=wrapper._session_id,
                                identifier=source,
                                source_type=ReferenceSource.KNOWLEDGE_BASE,
                                title=filename if filename else None
                            )
                except json.JSONDecodeError:
                    pass
            
            # Call original formatter if exists
            if original_format:
                return original_format(response_body)
            return response_body
        
        self._wrapped_tool._format_response = format_with_references
    
    async def execute(self, **kwargs):
        """Execute the wrapped tool."""
        started_at = time.perf_counter()
        try:
            result = await self._wrapped_tool.execute(**kwargs)
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.KNOWLEDGE_BASE.value,
                tool_name=self.name,
                success=True,
                duration_seconds=time.perf_counter() - started_at,
            )
            return result
        except Exception as exc:
            telemetry_registry.record_tool_call(
                session_id=self._session_id,
                source_family=ReferenceSource.KNOWLEDGE_BASE.value,
                tool_name=self.name,
                success=False,
                duration_seconds=time.perf_counter() - started_at,
                error=str(exc),
            )
            raise
    
    # Delegate LLMTool interface to wrapped tool
    def __getattr__(self, name):
        return getattr(self._wrapped_tool, name)
    
    @property
    def name(self):
        return self._wrapped_tool.name
    
    @property
    def description(self):
        return self._wrapped_tool.description
    
    @property
    def parameters(self):
        return self._wrapped_tool.parameters


class DocumentRetrievalAgent(RetrievalAgent):
    """Session-aware RetrievalAgent for document search.
    
    Extends spade_llm's RetrievalAgent with session tracking for concurrent queries.
    """
    
    def __init__(self, jid: str, password: str, retriever: VectorStoreRetriever, 
                 default_k: int = 4, **kwargs):
        super().__init__(
            jid=jid, password=password, retriever=retriever,
            default_k=default_k, verify_security=False, **kwargs
        )
        self._session_map: Dict[str, str] = {}
        logger.info(f"DocumentRetrievalAgent initialized: {jid}")
    
    async def setup(self) -> None:
        await super().setup()
        if hasattr(self, 'retrieval_behaviour'):
            apply_session_tracking(self, 'retrieval_behaviour')


async def create_retriever(
    persist_directory: str,
    collection_name: str = "documents",
    embedding_fn: Optional[Callable] = None,
) -> VectorStoreRetriever:
    """Create a VectorStoreRetriever backed by ChromaDB."""
    logger.info(f"Creating ChromaDB vector store at: {persist_directory}")
    
    vector_store = Chroma(
        collection_name=collection_name,
        persist_directory=persist_directory,
        embedding_fn=embedding_fn
    )
    await vector_store.initialize()
    
    doc_count = await vector_store.get_document_count()
    logger.info(f"Collection '{collection_name}' has {doc_count} documents")
    
    return VectorStoreRetriever(vector_store)


def create_retrieval_tool(
    retrieval_agent_jid: str,
    default_k: int = 4,
    timeout: int = 30,
    metadata_fields: Optional[List[str]] = None
) -> SessionAwareRetrievalTool:
    """Create a session-aware RetrievalTool for LLM agents to query documents.
    
    Args:
        retrieval_agent_jid: JID of the DocumentRetrievalAgent
        default_k: Number of documents to retrieve
        timeout: Response timeout in seconds
        metadata_fields: Metadata fields to include (None = all)
        
    Returns:
        Session-aware retrieval tool that extracts references
    """
    tool = RetrievalTool(
        retrieval_agent_jid=retrieval_agent_jid,
        default_k=default_k,
        timeout=timeout,
        name="retrieve_documents"
    )
    
    if metadata_fields is not None:
        _apply_metadata_filter(tool, metadata_fields)
    
    # Wrap with session-aware reference extraction
    wrapped_tool = SessionAwareRetrievalTool(tool, metadata_fields)
    
    logger.info(f"Session-aware RetrievalTool created for: {retrieval_agent_jid}")
    return wrapped_tool


def _apply_metadata_filter(tool: RetrievalTool, fields: List[str]) -> None:
    """Apply metadata field filtering to a retrieval tool."""
    
    def filtered_format(response_body: str) -> str:
        try:
            data = json.loads(response_body)
            if "error" in data:
                return json.dumps(data)
            
            documents = data.get("documents", [])
            if not documents:
                return json.dumps({"message": "No documents found."})
            
            filtered = []
            for i, doc in enumerate(documents, 1):
                entry = {"rank": i, "content": doc.get("content", "")}
                metadata = {k: v for k, v in doc.get("metadata", {}).items() if k in fields}
                if metadata:
                    entry["metadata"] = metadata
                filtered.append(entry)
            
            return json.dumps({"documents": filtered}, indent=2)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse response: {response_body}")
            return json.dumps({"error": "Parse failed", "raw": response_body})
    
    tool._format_response = filtered_format
