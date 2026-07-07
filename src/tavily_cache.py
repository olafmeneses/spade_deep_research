"""Persistent cache for Tavily search responses."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


CACHE_SCHEMA_VERSION = "v1"


def _normalize_query(query: str) -> str:
    return " ".join(query.split())


def _cache_key(query: str, topic: str) -> str:
    normalized_query = _normalize_query(query)
    return json.dumps(
        {
            "version": CACHE_SCHEMA_VERSION,
            "query": normalized_query,
            "topic": topic,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class TavilyCacheEntry:
    query: str
    topic: str
    requested_max_results: int
    items: List[Dict[str, Any]]
    created_at: float
    expires_at: float


class TavilySearchCache:
    """SQLite-backed cache for processed Tavily results."""

    def __init__(
        self,
        path: str | Path,
        ttl_days: int,
        enabled: bool = True,
        time_fn=time.time,
    ) -> None:
        self.path = Path(path)
        self.ttl_days = ttl_days
        self.enabled = enabled
        self._time_fn = time_fn
        self._lock = threading.Lock()
        if self.enabled:
            self._initialize()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tavily_cache (
                    cache_key TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    requested_max_results INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    items_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, query: str, topic: str) -> Optional[TavilyCacheEntry]:
        if not self.enabled:
            return None

        now = self._time_fn()
        key = _cache_key(query, topic)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT query, topic, requested_max_results, created_at, expires_at, items_json
                FROM tavily_cache
                WHERE cache_key = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                return None
            if row[4] <= now:
                conn.execute("DELETE FROM tavily_cache WHERE cache_key = ?", (key,))
                return None

        return TavilyCacheEntry(
            query=row[0],
            topic=row[1],
            requested_max_results=row[2],
            created_at=row[3],
            expires_at=row[4],
            items=json.loads(row[5]),
        )

    def set(self, query: str, topic: str, requested_max_results: int, items: List[Dict[str, Any]]) -> None:
        if not self.enabled:
            return

        now = self._time_fn()
        expires_at = now + max(0, self.ttl_days) * 24 * 60 * 60
        key = _cache_key(query, topic)
        payload = json.dumps(items, ensure_ascii=False, sort_keys=True)
        normalized_query = _normalize_query(query)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tavily_cache (
                    cache_key, query, topic, requested_max_results, created_at, expires_at, items_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    query = excluded.query,
                    topic = excluded.topic,
                    requested_max_results = excluded.requested_max_results,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    items_json = excluded.items_json
                """,
                (
                    key,
                    normalized_query,
                    topic,
                    requested_max_results,
                    now,
                    expires_at,
                    payload,
                ),
            )

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM tavily_cache")
