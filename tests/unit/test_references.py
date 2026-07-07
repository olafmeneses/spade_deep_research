"""Tests for src.references — ReferenceRegistry."""

import threading

import pytest

from src.references import ReferenceRegistry, reference_registry
from src.session import ReferenceSource


@pytest.fixture()
def registry(_clear_reference_registry) -> ReferenceRegistry:
    """Return the global singleton with a clean slate."""
    return reference_registry


# ── Basic operations ──────────────────────────────────────────────────


class TestReferenceRegistryBasic:
    def test_register_and_collect(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY, "Title A")
        refs = registry.collect("s1")
        assert len(refs) == 1
        assert refs[0].identifier == "http://a.com"
        assert refs[0].source_type is ReferenceSource.TAVILY
        assert refs[0].title == "Title A"

    def test_collect_clears(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        registry.collect("s1")
        assert registry.collect("s1") == []

    def test_peek_does_not_clear(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        peeked = registry.peek("s1")
        assert len(peeked) == 1
        assert len(registry.peek("s1")) == 1  # still there

    def test_clear(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        registry.clear("s1")
        assert registry.collect("s1") == []

    def test_clear_nonexistent_session(self, registry: ReferenceRegistry):
        registry.clear("no-such-session")  # should not raise


# ── Deduplication ─────────────────────────────────────────────────────


class TestReferenceRegistryDedup:
    def test_duplicate_identifier_ignored(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        assert len(registry.peek("s1")) == 1

    def test_different_identifiers_kept(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        registry.register("s1", "http://b.com", ReferenceSource.TAVILY)
        assert len(registry.peek("s1")) == 2


# ── String source type conversion ────────────────────────────────────


class TestReferenceRegistrySourceConversion:
    def test_string_source_type(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", "tavily")
        refs = registry.peek("s1")
        assert refs[0].source_type is ReferenceSource.TAVILY

    def test_unknown_source_type_skipped(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", "unknown_source")
        assert registry.peek("s1") == []


# ── Edge cases ────────────────────────────────────────────────────────


class TestReferenceRegistryEdgeCases:
    def test_empty_session_id_skipped(self, registry: ReferenceRegistry):
        registry.register("", "http://a.com", ReferenceSource.TAVILY)
        assert registry.peek("") == []

    def test_empty_identifier_skipped(self, registry: ReferenceRegistry):
        registry.register("s1", "", ReferenceSource.TAVILY)
        assert registry.peek("s1") == []

    def test_multiple_sessions_isolated(self, registry: ReferenceRegistry):
        registry.register("s1", "http://a.com", ReferenceSource.TAVILY)
        registry.register("s2", "http://b.com", ReferenceSource.ARXIV)
        assert len(registry.collect("s1")) == 1
        assert len(registry.collect("s2")) == 1


# ── Thread safety ─────────────────────────────────────────────────────


class TestReferenceRegistryThreadSafety:
    def test_concurrent_register(self, registry: ReferenceRegistry):
        """Register many references from multiple threads concurrently."""
        n_threads = 10
        refs_per_thread = 50

        def worker(thread_id: int):
            for i in range(refs_per_thread):
                registry.register(
                    "concurrent-session",
                    f"http://t{thread_id}-{i}.com",
                    ReferenceSource.TAVILY,
                )

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        refs = registry.collect("concurrent-session")
        assert len(refs) == n_threads * refs_per_thread
