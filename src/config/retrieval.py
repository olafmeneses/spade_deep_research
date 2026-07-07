"""Retrieval system configuration and setup."""

import os
import logging
from typing import Optional

from spade_llm.providers import LLMProvider

from src.config.settings import settings
from src.agents.retrieval import (
    DocumentRetrievalAgent,
    create_retriever,
    create_retrieval_tool,
)

logger = logging.getLogger(__name__)


class RetrievalConfig:
    """Configuration and setup for document retrieval system."""
    
    def __init__(
        self,
        persist_directory: str = None,
        collection_name: str = None,
        domain: str = None,
        password: str = None,
    ):
        """Initialize retrieval configuration.
        
        Args:
            persist_directory: ChromaDB storage path
            collection_name: ChromaDB collection name
            domain: XMPP domain for retrieval agent
            password: XMPP password
        """
        self.persist_directory = persist_directory or settings.CHROMADB_PATH
        self.collection_name = collection_name or settings.CHROMADB_COLLECTION
        self.domain = domain or settings.JID_DOMAIN
        self.password = password or settings.PASSWORD
        self.retrieval_agent = None
        self.retrieval_tool = None
        
    def is_available(self) -> bool:
        """Check if ChromaDB data is available."""
        return os.path.exists(self.persist_directory)
    
    async def setup(
        self,
        embedding_provider: LLMProvider,
        default_k: int = 4,
        timeout: int = 30,
        metadata_fields: Optional[list] = None,
    ) -> tuple[Optional[DocumentRetrievalAgent], Optional[object]]:
        """Set up the retrieval system.
        
        Args:
            embedding_provider: LLM provider for embeddings
            default_k: Default number of documents to retrieve
            timeout: Retrieval timeout in seconds
            metadata_fields: Metadata fields to include in results
            
        Returns:
            Tuple of (retrieval_agent, retrieval_tool) or (None, None) if unavailable
        """
        if not self.is_available():
            logger.warning(
                "ChromaDB not found at %s. "
                "Document retrieval will not be available.",
                self.persist_directory
            )
            return None, None
        
        try:
            retriever = await create_retriever(
                persist_directory=self.persist_directory,
                collection_name=self.collection_name,
                embedding_fn=embedding_provider.get_embeddings
            )
            logger.info("Retriever created (dir=%s, collection=%s)", self.persist_directory, self.collection_name)

            retrieval_jid = f"retrieval@{self.domain}"
            self.retrieval_agent = DocumentRetrievalAgent(
                jid=retrieval_jid,
                password=self.password,
                retriever=retriever,
                default_k=default_k
            )
            logger.info("DocumentRetrievalAgent created: %s", retrieval_jid)
            
            self.retrieval_tool = create_retrieval_tool(
                retrieval_agent_jid=retrieval_jid,
                default_k=default_k,
                timeout=timeout,
                metadata_fields=metadata_fields or ["source", "page"]
            )
            logger.info("Retrieval tool created: %s", retrieval_jid)
            
            return self.retrieval_agent, self.retrieval_tool
            
        except Exception as e:
            logger.error("Failed to setup retrieval system: %s", e)
            return None, None
    
    async def start(self) -> None:
        """Start the retrieval agent."""
        if self.retrieval_agent:
            await self.retrieval_agent.start()
            logger.info("Retrieval agent started")
    
    async def stop(self) -> None:
        """Stop the retrieval agent."""
        if self.retrieval_agent:
            try:
                await self.retrieval_agent.stop()
                logger.info("Retrieval agent stopped")
            except Exception as e:
                logger.error("Error stopping retrieval agent: %s", e)
