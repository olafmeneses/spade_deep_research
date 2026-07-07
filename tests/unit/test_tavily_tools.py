"""Tests for Tavily tool caching behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.telemetry import telemetry_registry
from src.budgets import SessionBudgetRegistry
from src.config.tools import SessionAwareTavilyTool
from src.references import reference_registry
from src.tavily_cache import TavilySearchCache


def _make_results(count: int) -> dict:
    return {
        "results": [
            {
                "title": f"Result {index}",
                "url": f"https://example.com/{index}",
                "content": f"Summary {index}",
            }
            for index in range(1, count + 1)
        ]
    }


@pytest.fixture()
def fresh_tavily_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TavilySearchCache:
    now = [1_000_000.0]

    cache = TavilySearchCache(
        path=tmp_path / "tavily_cache.sqlite",
        ttl_days=21,
        enabled=True,
        time_fn=lambda: now[0],
    )
    monkeypatch.setattr("src.config.tools.tavily_cache", cache)
    monkeypatch.setattr("src.config.tools._cache_key_locks", {})
    cache._test_now = now  # type: ignore[attr-defined]
    return cache


@pytest.fixture(autouse=True)
def _reset_tavily_budgets() -> None:
    SessionBudgetRegistry.reset_all()


class TestSessionAwareTavilyToolCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_consumes_budget_and_re_registers_references(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fresh_tavily_cache: TavilySearchCache,
    ) -> None:
        calls = []

        def fake_search(**kwargs):
            calls.append(kwargs)
            return _make_results(2)

        monkeypatch.setattr("src.config.tools.tavily_client.search", fake_search)

        tool = SessionAwareTavilyTool(
            owner_id="agent-1",
            max_calls_per_agent=3,
            max_calls_per_session=2,
        )
        tool.set_session_id("session-1")

        first = await tool._search("same query", max_results=2, topic="general")
        refs_after_first = reference_registry.collect("session-1")
        second = await tool._search("same query", max_results=2, topic="general")
        refs_after_second = reference_registry.collect("session-1")
        third = await tool._search("same query", max_results=2, topic="general")

        assert len(calls) == 1
        assert "Result 1" in first
        assert "Result 1" in second
        assert len(refs_after_first) == 2
        assert len(refs_after_second) == 2
        assert "Search budget reached." in third

        snapshot = telemetry_registry._records["session-1"].snapshot(
            telemetry_registry.archive_store.session_dir("session-1")
        )
        tavily_stats = snapshot["tool_usage"]["by_source_family"]["tavily"]["tools"]["tavily_search"]
        assert tavily_stats["attempts"] == 3
        assert tavily_stats["successes"] == 2
        assert tavily_stats["failures"] == 1

    @pytest.mark.asyncio
    async def test_smaller_max_results_reuses_larger_cached_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fresh_tavily_cache: TavilySearchCache,
    ) -> None:
        calls = []

        def fake_search(**kwargs):
            calls.append(kwargs)
            return _make_results(3)

        monkeypatch.setattr("src.config.tools.tavily_client.search", fake_search)

        tool = SessionAwareTavilyTool(owner_id="agent-1", max_calls_per_session=5, max_calls_per_agent=5)
        tool.set_session_id("session-2")

        await tool._search("same query", max_results=3, topic="news")
        second = await tool._search("same query", max_results=2, topic="news")

        assert len(calls) == 1
        assert "Result 1" in second
        assert "Result 2" in second
        assert "Result 3" not in second

    @pytest.mark.asyncio
    async def test_larger_max_results_refreshes_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fresh_tavily_cache: TavilySearchCache,
    ) -> None:
        call_counts = []

        def fake_search(**kwargs):
            call_counts.append(kwargs["max_results"])
            return _make_results(kwargs["max_results"])

        monkeypatch.setattr("src.config.tools.tavily_client.search", fake_search)

        tool = SessionAwareTavilyTool(owner_id="agent-1", max_calls_per_session=5, max_calls_per_agent=5)
        tool.set_session_id("session-3")

        first = await tool._search("same query", max_results=2, topic="finance")
        second = await tool._search("same query", max_results=3, topic="finance")

        assert call_counts == [2, 3]
        assert "Result 2" in first
        assert "Result 3" in second

    @pytest.mark.asyncio
    async def test_expired_entry_refreshes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fresh_tavily_cache: TavilySearchCache,
    ) -> None:
        calls = []

        def fake_search(**kwargs):
            calls.append(kwargs)
            result_number = len(calls)
            return {
                "results": [
                    {
                        "title": f"Result {result_number}",
                        "url": f"https://example.com/{result_number}",
                        "content": f"Summary {result_number}",
                    }
                ]
            }

        monkeypatch.setattr("src.config.tools.tavily_client.search", fake_search)

        tool = SessionAwareTavilyTool(owner_id="agent-1", max_calls_per_session=5, max_calls_per_agent=5)
        tool.set_session_id("session-4")

        first = await tool._search("same query", max_results=1, topic="general")
        fresh_tavily_cache._test_now[0] += 22 * 24 * 60 * 60  # type: ignore[attr-defined]
        second = await tool._search("same query", max_results=1, topic="general")

        assert len(calls) == 2
        assert "Result 1" in first
        assert "Result 2" in second
