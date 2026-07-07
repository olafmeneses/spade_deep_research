"""Tests for src.config.mcp_adapters — ArXiv reference extraction & MCP wrappers."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.mcp_adapters import (
    extract_arxiv_references,
    SessionAwareMCPToolWrapper,
    wrap_arxiv_mcp_tools,
)
from src.references import reference_registry


@pytest.fixture(autouse=True)
def _clean_refs(_clear_reference_registry):
    pass


# ── extract_arxiv_references — JSON format ───────────────────────────


class TestExtractArxivReferencesJson:
    def test_structured_papers(self):
        data = {
            "papers": [
                {"id": "2301.12345", "title": "Paper A"},
                {"id": "2302.99999", "title": "Paper B"},
            ]
        }
        text_result = {"text": json.dumps(data)}
        extract_arxiv_references("s1", text_result)
        refs = reference_registry.collect("s1")
        assert len(refs) == 2
        assert refs[0].identifier == "https://arxiv.org/abs/2301.12345"
        assert refs[0].title == "Paper A"

    def test_empty_papers(self):
        text_result = {"text": json.dumps({"papers": []})}
        extract_arxiv_references("s1", text_result)
        assert reference_registry.collect("s1") == []


# ── extract_arxiv_references — edge cases ────────────────────────────


class TestExtractArxivReferencesEdgeCases:
    def test_none_result(self):
        extract_arxiv_references("s1", None)
        assert reference_registry.collect("s1") == []

    def test_empty_session(self):
        extract_arxiv_references("", {"text": "2301.12345"})
        # Nothing should be registered for empty session
        assert reference_registry.collect("") == []

    def test_no_arxiv_ids_in_text(self):
        extract_arxiv_references("s1", {"text": "No papers here."})
        assert reference_registry.collect("s1") == []


# ── SessionAwareMCPToolWrapper ───────────────────────────────────────


class TestSessionAwareMCPToolWrapper:
    async def test_delegates_execute(self):
        inner_tool = MagicMock()
        inner_tool.execute = AsyncMock(return_value="result")
        inner_tool.name = "arxiv_search_papers"
        extractor = MagicMock()

        wrapper = SessionAwareMCPToolWrapper(inner_tool, extractor)
        wrapper.set_session_id("s1")
        result = await wrapper.execute(query="test")

        assert result == "result"
        inner_tool.execute.assert_awaited_once_with(query="test")
        extractor.assert_called_once_with("s1", "result")

    async def test_no_extraction_without_session(self):
        inner_tool = MagicMock()
        inner_tool.execute = AsyncMock(return_value="result")
        extractor = MagicMock()

        wrapper = SessionAwareMCPToolWrapper(inner_tool, extractor)
        # No session_id set
        await wrapper.execute(query="test")
        extractor.assert_not_called()

    def test_delegates_attributes(self):
        inner_tool = MagicMock()
        inner_tool.name = "arxiv_search_papers"
        inner_tool.description = "Search papers"
        inner_tool.parameters = {"type": "object"}

        wrapper = SessionAwareMCPToolWrapper(inner_tool, lambda s, r: None)
        assert wrapper.name == "arxiv_search_papers"
        assert wrapper.description == "Search papers"
        assert wrapper.parameters == {"type": "object"}


# ── wrap_arxiv_mcp_tools ─────────────────────────────────────────────


class TestWrapArxivMcpTools:
    def test_wraps_arxiv_tools(self):
        arxiv_tool = MagicMock()
        arxiv_tool.name = "arxiv_search_papers"
        other_tool = MagicMock()
        other_tool.name = "some_other_tool"

        result = wrap_arxiv_mcp_tools([arxiv_tool, other_tool])
        assert len(result) == 2
        assert isinstance(result[0], SessionAwareMCPToolWrapper)
        assert result[1] is other_tool  # not wrapped

    def test_wraps_prefixed_arxiv_tools(self):
        arxiv_tool = MagicMock()
        arxiv_tool.name = "arxiv_arxiv_search_papers"

        result = wrap_arxiv_mcp_tools([arxiv_tool])

        assert len(result) == 1
        assert isinstance(result[0], SessionAwareMCPToolWrapper)
