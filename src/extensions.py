"""Extension registry for dynamic source and integration configuration.

Extensions (research sources) register at startup based on environment
settings.

Adding a new extension
-------------------
- Add a ``_register_*`` block in ``_discover_extensions()``.
- Prompts, schemas, and route handling adapt automatically.
"""

import logging
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field, create_model

from src.config.settings import settings

logger = logging.getLogger(__name__)


class SourceDescriptor:
    """Metadata for a registered research source."""

    __slots__ = ("name", "description", "schema", "is_core")

    def __init__(
        self,
        name: str,
        description: str,
        schema: Optional[Type[BaseModel]] = None,
        is_core: bool = False,
    ):
        self.name = name
        self.description = description
        # Per-request Pydantic schema (only for extensions that need API config)
        self.schema = schema
        # Core sources are always present (arxiv, tavily)
        self.is_core = is_core


class ExtensionRegistry:
    """Central registry of research sources and integrations.

    Sources are registered eagerly at startup.  The registry then provides:

    * Dynamic prompt fragments listing available sources.
    * A dynamically-built ``ResearchRequest`` Pydantic model.
    * A dynamically-built ``ResearchQuestion.source`` Literal.
    * Helpers to extract per-extension configs from a request.
    """

    def __init__(self) -> None:
        self._sources: Dict[str, SourceDescriptor] = {}

    def register(
        self,
        name: str,
        description: str,
        schema: Optional[Type[BaseModel]] = None,
        is_core: bool = False,
    ) -> None:
        """Register a research source."""
        self._sources[name] = SourceDescriptor(name, description, schema, is_core)
        label = "core source" if is_core else "extension"
        logger.info(f"Registered {label}: '{name}'")

    def reset(self) -> None:
        """Clear all registered sources (useful for tests)."""
        self._sources.clear()

    @property
    def registered(self) -> Dict[str, SourceDescriptor]:
        return dict(self._sources)

    @property
    def source_names(self) -> List[str]:
        """All registered source names (core + optional)."""
        return list(self._sources.keys())

    @property
    def extensions_with_schema(self) -> Dict[str, SourceDescriptor]:
        """Only sources that have a per-request API schema."""
        return {k: v for k, v in self._sources.items() if v.schema is not None}

    def is_registered(self, name: str) -> bool:
        return name in self._sources

    def has_source(self, name: str) -> bool:
        return name in self._sources

    def agent_selection_block(self) -> str:
        """Build the AGENT SELECTION section for planner/coordinator prompts.

        Example output::

            - 'arxiv': Academic papers and scientific research
            - 'tavily': Current events, web content, general information
        """
        lines = []
        for src in self._sources.values():
            lines.append(f"- '{src.name}': {src.description}")
        return "\n".join(lines)

    def agent_names_list(self) -> str:
        """Comma-separated list of internal agent names (for NEVER-mention rules)."""
        internal = list(self._sources.keys()) + ["coordinator", "planner", "writer", "critic"]
        return ", ".join(internal)

    def build_research_request_model(self) -> Type[BaseModel]:
        """Build ``ResearchRequest`` from registered sources.

        Only sources with a ``schema`` contribute an ``extensions`` sub-field.
        If no source has a schema the model just has ``query``.
        """
        base_fields: Dict[str, Any] = {
            "query": (str, Field(..., description="The research query to investigate")),
            "disable_critic": (
                bool,
                Field(False, description="Skip the critic review loop entirely"),
            ),
        }

        ext_schemas = self.extensions_with_schema
        if not ext_schemas:
            return create_model("ResearchRequest", **base_fields)

        ext_fields: Dict[str, Any] = {}
        for name, desc in ext_schemas.items():
            ext_fields[name] = (
                Optional[desc.schema],
                Field(None, description=desc.description),
            )

        ExtensionsModel = create_model("Extensions", **ext_fields)

        base_fields["extensions"] = (
            Optional[ExtensionsModel],
            Field(None, description="Configuration for enabled integrations"),
        )

        return create_model("ResearchRequest", **base_fields)

    def extract_session_extensions(self, request: Any) -> Optional[Dict[str, Any]]:
        """Extract extension configs from a validated request.

        Returns a dict suitable for ``session.extensions``, or ``None``
        if no extension data was provided.
        """
        extensions_obj = getattr(request, "extensions", None)
        if extensions_obj is None:
            return None

        result: Dict[str, Any] = {}
        for name in self.extensions_with_schema:
            val = getattr(extensions_obj, name, None)
            if val is not None:
                result[name] = val

        return result or None


extension_registry = ExtensionRegistry()


def _discover_extensions() -> None:
    """Discover and register sources based on environment settings."""

    extension_registry.register(
        "arxiv",
        description="Academic papers and scientific research",
        is_core=True,
    )
    extension_registry.register(
        "tavily",
        description="Current events, web content, general information",
        is_core=True,
    )

    if settings.CHROMADB_PATH:
        extension_registry.register(
            "knowledge_base",
            description="Internal documents from local knowledge base",
        )


_discover_extensions()
