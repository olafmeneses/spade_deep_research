"""Tests for src.extensions — ExtensionRegistry."""

from typing import Optional

from pydantic import BaseModel, Field

from src.extensions import ExtensionRegistry, SourceDescriptor


# ── Helpers ───────────────────────────────────────────────────────────


class _DummySchema(BaseModel):
    token: Optional[str] = Field(None, description="A test token")


# ── Registration ──────────────────────────────────────────────────────


class TestExtensionRegistryRegistration:
    def test_register_core(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers", is_core=True)
        assert fresh_registry.is_registered("arxiv")
        assert fresh_registry.registered["arxiv"].is_core is True

    def test_register_extension_with_schema(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        assert fresh_registry.registered["private_rag"].schema is _DummySchema
        assert fresh_registry.registered["private_rag"].is_core is False

    def test_reset_clears(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers", is_core=True)
        fresh_registry.reset()
        assert fresh_registry.source_names == []

    def test_is_registered_false(self, fresh_registry: ExtensionRegistry):
        assert fresh_registry.is_registered("nonexistent") is False


# ── Property helpers ──────────────────────────────────────────────────


class TestExtensionRegistryProperties:
    def test_source_names(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers", is_core=True)
        fresh_registry.register("tavily", "Web", is_core=True)
        assert set(fresh_registry.source_names) == {"arxiv", "tavily"}

    def test_extensions_with_schema_filters(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers", is_core=True)
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        ext = fresh_registry.extensions_with_schema
        assert "arxiv" not in ext
        assert "private_rag" in ext


# ── Prompt generation ─────────────────────────────────────────────────


class TestExtensionRegistryPrompts:
    def test_agent_selection_block(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Academic papers")
        block = fresh_registry.agent_selection_block()
        assert "- 'arxiv': Academic papers" in block

    def test_agent_names_list(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers")
        fresh_registry.register("tavily", "Web")
        names = fresh_registry.agent_names_list()
        # Should include registered sources + built-in role names
        for name in ("arxiv", "tavily", "coordinator", "planner", "writer", "critic"):
            assert name in names


# ── Dynamic model building ───────────────────────────────────────────


class TestExtensionRegistryDynamicModel:
    def test_model_query_only(self, fresh_registry: ExtensionRegistry):
        """No extensions with schemas → model has only `query`."""
        fresh_registry.register("arxiv", "Papers", is_core=True)
        Model = fresh_registry.build_research_request_model()
        instance = Model(query="hello")
        assert instance.query == "hello"
        assert not hasattr(instance, "extensions")

    def test_model_with_extension_schema(self, fresh_registry: ExtensionRegistry):
        """Extension with schema → model has `extensions` sub-field."""
        fresh_registry.register("arxiv", "Papers", is_core=True)
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        Model = fresh_registry.build_research_request_model()
        instance = Model(query="hello", extensions={"private_rag": {"token": "abc"}})
        assert instance.extensions.private_rag.token == "abc"

    def test_model_extensions_optional(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        Model = fresh_registry.build_research_request_model()
        instance = Model(query="hello")
        assert instance.extensions is None

    def test_extract_session_extensions_none(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("arxiv", "Papers", is_core=True)
        Model = fresh_registry.build_research_request_model()
        req = Model(query="hello")
        assert fresh_registry.extract_session_extensions(req) is None

    def test_extract_session_extensions_with_data(self, fresh_registry: ExtensionRegistry):
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        Model = fresh_registry.build_research_request_model()
        req = Model(query="hello", extensions={"private_rag": {"token": "x"}})
        ext = fresh_registry.extract_session_extensions(req)
        assert ext is not None
        assert ext["private_rag"].token == "x"

    def test_extract_session_extensions_empty_extensions(self, fresh_registry: ExtensionRegistry):
        """Extensions object provided but all fields are None → returns None."""
        fresh_registry.register("private_rag", "RAG", schema=_DummySchema)
        Model = fresh_registry.build_research_request_model()
        req = Model(query="hello", extensions={})
        assert fresh_registry.extract_session_extensions(req) is None


# ── SourceDescriptor ──────────────────────────────────────────────────


class TestSourceDescriptor:
    def test_slots(self):
        sd = SourceDescriptor("test", "description", None, True)
        assert sd.name == "test"
        assert sd.description == "description"
        assert sd.schema is None
        assert sd.is_core is True
