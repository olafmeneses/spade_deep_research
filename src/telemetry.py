"""Session telemetry and durable research-session archiving."""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any, Dict, Optional

from src.text_normalization import normalize_report_text

logger = logging.getLogger(__name__)

PHASE_STATES = {
    "planning",
    "awaiting_validation",
    "researching",
    "writing",
    "reviewing",
}

SOURCE_FAMILIES = ("tavily", "arxiv", "knowledge_base")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _utcnow() -> datetime:
    return datetime.now()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _clean_reference_url(url: str) -> str:
    return str(url or "").strip().rstrip("\\").rstrip(".,;")


def _used_reference_stats(report: str, reference_details: list[Dict[str, str]]) -> Dict[str, Any]:
    ref_by_url = {
        _clean_reference_url(ref.get("identifier", "")): ref
        for ref in reference_details
        if ref.get("identifier")
    }
    used_keys: set[str] = set()
    by_family: Dict[str, int] = {}

    def add_reference(raw_number: str, url: str = "") -> None:
        ref: Dict[str, str] | None = None
        key = ""
        cleaned_url = _clean_reference_url(url)
        if cleaned_url:
            key = cleaned_url
            ref = ref_by_url.get(cleaned_url)
        else:
            index = int(raw_number) - 1
            if 0 <= index < len(reference_details):
                ref = reference_details[index]
                key = _clean_reference_url(ref.get("identifier", "")) or f"index:{index}"
        if not key or key in used_keys:
            return
        used_keys.add(key)
        family = (ref or {}).get("source_family") or "unknown"
        by_family[family] = by_family.get(family, 0) + 1

    def add_arxiv_id(arxiv_id: str) -> None:
        key = f"https://arxiv.org/abs/{arxiv_id}"
        if key in used_keys:
            return
        used_keys.add(key)
        by_family["arxiv"] = by_family.get("arxiv", 0) + 1

    consumed_spans: list[tuple[int, int]] = []
    patterns = [
        re.compile(r"\[\[(\d+)(?::\s*([\s\S]*?))?\]\]\(([^)\n]*?)(?:\\)?\)"),
        re.compile(r"(?<!!)\[(\d+)(?::\s*([\s\S]*?))?\]\(([^)\n]*?)(?:\\)?\)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(report or ""):
            add_reference(match.group(1), match.group(3))
            consumed_spans.append(match.span())

    def is_consumed(start: int, end: int) -> bool:
        return any(span_start <= start and end <= span_end for span_start, span_end in consumed_spans)

    for match in re.finditer(r"(?<!\[)\[(\d+)(?::\s*([^\]\n]+))?\](?![\]\(])", report or ""):
        if not is_consumed(*match.span()):
            add_reference(match.group(1))
    for match in re.finditer(r"【(\d+)】", report or ""):
        add_reference(match.group(1))
    for match in re.finditer(r"［(\d+)］", report or ""):
        add_reference(match.group(1))
    for match in re.finditer(r"\[(\d{4}\.\d{4,5}(?:v\d+)?)\]", report or ""):
        add_arxiv_id(match.group(1))
    for match in re.finditer(
        r"\((arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s*,\s*(?:arXiv:)?\d{4}\.\d{4,5}(?:v\d+)?)*)\)",
        report or "",
        re.IGNORECASE,
    ):
        for arxiv_id in re.findall(r"(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", match.group(1), re.IGNORECASE):
            add_arxiv_id(arxiv_id)

    return {"total": len(used_keys), "by_source_family": dict(sorted(by_family.items()))}


@dataclass
class SessionTelemetryRecord:
    """In-memory telemetry accumulator for a single session."""

    session_id: str
    query: str
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    phase_entry_timestamps: Dict[str, str] = field(default_factory=dict)
    phase_durations_seconds: Dict[str, float] = field(default_factory=dict)
    current_phase: Optional[str] = None
    current_phase_started_at: Optional[datetime] = None
    cost: Dict[str, Any] = field(
        default_factory=lambda: {
            "total_cost": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "call_count": 0,
            "pricing_model": None,
            "last_response_model": None,
            "cost_source": "litellm_model_pricing_table",
        }
    )
    tool_usage: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    references: Dict[str, int] = field(default_factory=dict)
    reference_details: list[Dict[str, str]] = field(default_factory=list)
    _reference_ids: Dict[str, set[str]] = field(default_factory=dict)
    coordinator: Dict[str, int] = field(
        default_factory=lambda: {
            "launch_count": 0,
            "wave_count": 0,
        }
    )
    report_text: str = ""
    failure: Optional[Dict[str, Any]] = None
    cancellation: Optional[Dict[str, Any]] = None

    def ensure_source_family(self, family: str) -> None:
        if family not in self.tool_usage:
            self.tool_usage[family] = {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "duration_seconds": 0.0,
                "tools": {},
            }
        if family not in self.references:
            self.references[family] = 0
        if family not in self._reference_ids:
            self._reference_ids[family] = set()

    def apply_phase_transition(self, new_status: str, timestamp: datetime) -> None:
        if self.current_phase and self.current_phase_started_at:
            elapsed = (timestamp - self.current_phase_started_at).total_seconds()
            self.phase_durations_seconds[self.current_phase] = (
                self.phase_durations_seconds.get(self.current_phase, 0.0) + max(0.0, elapsed)
            )
        self.current_phase = new_status if new_status in PHASE_STATES else None
        self.current_phase_started_at = timestamp if new_status in PHASE_STATES else None
        if new_status in PHASE_STATES:
            self.phase_entry_timestamps[new_status] = timestamp.isoformat()

    def report_stats(self) -> Dict[str, Any]:
        return {
            "has_report": bool(self.report_text),
            "char_count": len(self.report_text),
            "word_count": _word_count(self.report_text),
            "used_references": _used_reference_stats(self.report_text, self.reference_details),
        }

    def snapshot(self, archive_dir: Path) -> Dict[str, Any]:
        now = self.completed_at or self.updated_at
        phase_durations = deepcopy(self.phase_durations_seconds)
        if self.current_phase and self.current_phase_started_at and self.completed_at is None:
            phase_durations[self.current_phase] = phase_durations.get(self.current_phase, 0.0) + max(
                0.0, (self.updated_at - self.current_phase_started_at).total_seconds()
            )

        for family in SOURCE_FAMILIES:
            self.ensure_source_family(family)

        total_tool_attempts = sum(item["attempts"] for item in self.tool_usage.values())
        total_tool_duration = sum(item["duration_seconds"] for item in self.tool_usage.values())

        return {
            "session_id": self.session_id,
            "query": self.query,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "completed_at": _iso(self.completed_at),
            "total_wall_clock_seconds": max(0.0, (now - self.created_at).total_seconds()),
            "phase_entry_timestamps": dict(self.phase_entry_timestamps),
            "phase_durations_seconds": phase_durations,
            "cost": dict(self.cost),
            "tool_usage": {
                "total_attempts": total_tool_attempts,
                "total_duration_seconds": total_tool_duration,
                "by_source_family": deepcopy(self.tool_usage),
            },
            "references": {
                "total": sum(self.references.values()),
                "by_source_family": dict(self.references),
                "details": deepcopy(self.reference_details),
            },
            "coordinator": dict(self.coordinator),
            "report": self.report_stats(),
            "failure": deepcopy(self.failure),
            "cancellation": deepcopy(self.cancellation),
            "archive": {
                "session_dir": str(archive_dir),
                "session_json": str(archive_dir / "session.json"),
                "events_jsonl": str(archive_dir / "events.jsonl"),
                "report_md": str(archive_dir / "report.md"),
            },
        }


class SessionArchiveStore:
    """File-backed archive for research session artifacts."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or (_repo_root() / "bench" / "session_archive")
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        path = self.root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_snapshot(self, session_id: str, snapshot: Dict[str, Any]) -> Path:
        session_dir = self.session_dir(session_id)
        target = session_dir / "session.json"
        with NamedTemporaryFile("w", encoding="utf-8", dir=session_dir, delete=False) as handle:
            json.dump(snapshot, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            temp_name = handle.name
        os.replace(temp_name, target)
        return target

    def append_event(self, session_id: str, event_type: str, data: Dict[str, Any], timestamp: datetime) -> Path:
        session_dir = self.session_dir(session_id)
        target = session_dir / "events.jsonl"
        entry = {
            "event_type": event_type,
            "timestamp": timestamp.isoformat(),
            "data": data,
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return target

    def write_report(self, session_id: str, report: str) -> Path:
        session_dir = self.session_dir(session_id)
        target = session_dir / "report.md"
        with NamedTemporaryFile("w", encoding="utf-8", dir=session_dir, delete=False) as handle:
            handle.write(normalize_report_text(report))
            temp_name = handle.name
        os.replace(temp_name, target)
        return target


class SessionTelemetryRegistry:
    """In-process telemetry registry with durable archival flushes."""

    def __init__(self, archive_store: Optional[SessionArchiveStore] = None):
        self.archive_store = archive_store or SessionArchiveStore()
        self._records: Dict[str, SessionTelemetryRecord] = {}
        self._lock = Lock()

    def _ensure_record(
        self,
        session_id: str,
        query: str = "",
        status: Optional[str] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> SessionTelemetryRecord:
        record = self._records.get(session_id)
        if record is None:
            timestamp = created_at or updated_at or _utcnow()
            record = SessionTelemetryRecord(
                session_id=session_id,
                query=query,
                status=status or "created",
                created_at=timestamp,
                updated_at=updated_at or timestamp,
            )
            self._records[session_id] = record
        else:
            if query:
                record.query = query
            if created_at:
                record.created_at = created_at
            if updated_at:
                record.updated_at = updated_at
            if status is not None:
                record.status = status
        return record

    def _flush_snapshot_locked(self, session_id: str) -> Dict[str, Any]:
        record = self._records[session_id]
        archive_dir = self.archive_store.session_dir(session_id)
        snapshot = record.snapshot(archive_dir)
        self.archive_store.write_snapshot(session_id, snapshot)
        if record.report_text:
            self.archive_store.write_report(session_id, record.report_text)
        return snapshot

    def initialize_session(
        self,
        session_id: str,
        query: str,
        status: str,
        created_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            timestamp = created_at or _utcnow()
            self._ensure_record(
                session_id=session_id,
                query=query,
                status=status,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.archive_store.append_event(
                session_id,
                "session_created",
                {"query": query, "status": status},
                timestamp,
            )
            return self._flush_snapshot_locked(session_id)

    def record_state_transition(
        self,
        session_id: str,
        previous_status: str,
        new_status: str,
        query: str = "",
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            ts = timestamp or _utcnow()
            record = self._ensure_record(session_id, query=query, status=new_status, updated_at=ts)
            record.apply_phase_transition(new_status, ts)
            record.status = new_status
            record.updated_at = ts
            if new_status in {"completed", "failed", "cancelled"}:
                record.completed_at = ts
            self.archive_store.append_event(
                session_id,
                "state_transition",
                {"previous_status": previous_status, "new_status": new_status},
                ts,
            )
            return self._flush_snapshot_locked(session_id)

    def record_progress(self, session_id: str, message: str, phase: Optional[str] = None) -> None:
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            record.updated_at = ts
            payload = {"message": message}
            if phase:
                payload["phase"] = phase
            self.archive_store.append_event(session_id, "progress", payload, ts)
            self._flush_snapshot_locked(session_id)

    def record_report(self, session_id: str, report: str) -> Dict[str, Any]:
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            record.report_text = normalize_report_text(report)
            record.updated_at = ts
            self.archive_store.append_event(
                session_id,
                "report_updated",
                record.report_stats(),
                ts,
            )
            return self._flush_snapshot_locked(session_id)

    def record_terminal_state(
        self,
        session_id: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, status=status, updated_at=ts)
            record.status = status
            record.updated_at = ts
            record.completed_at = ts
            if status == "failed":
                record.failure = details or {"failed_at": ts.isoformat()}
            elif status == "cancelled":
                record.cancellation = details or {"cancelled_at": ts.isoformat()}
            self.archive_store.append_event(session_id, status, details or {}, ts)
            return self._flush_snapshot_locked(session_id)

    def record_tool_call(
        self,
        session_id: Optional[str],
        source_family: str,
        tool_name: str,
        success: bool,
        duration_seconds: float,
        error: Optional[str] = None,
    ) -> None:
        if not session_id:
            return
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            record.updated_at = ts
            record.ensure_source_family(source_family)
            family = record.tool_usage[source_family]
            family["attempts"] += 1
            family["duration_seconds"] += max(0.0, duration_seconds)
            if success:
                family["successes"] += 1
            else:
                family["failures"] += 1
            tool_stats = family["tools"].setdefault(
                tool_name,
                {"attempts": 0, "successes": 0, "failures": 0, "duration_seconds": 0.0},
            )
            tool_stats["attempts"] += 1
            tool_stats["duration_seconds"] += max(0.0, duration_seconds)
            if success:
                tool_stats["successes"] += 1
            else:
                tool_stats["failures"] += 1
            self.archive_store.append_event(
                session_id,
                "tool_call",
                {
                    "source_family": source_family,
                    "tool_name": tool_name,
                    "success": success,
                    "duration_seconds": duration_seconds,
                    "error": error,
                },
                ts,
            )
            self._flush_snapshot_locked(session_id)

    def record_reference(
        self,
        session_id: Optional[str],
        source_family: str,
        identifier: str,
        title: Optional[str] = None,
    ) -> None:
        if not session_id or not identifier:
            return
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            record.updated_at = ts
            record.ensure_source_family(source_family)
            refs = record._reference_ids[source_family]
            if identifier in refs:
                return
            refs.add(identifier)
            record.reference_details.append(
                {
                    "source_family": source_family,
                    "identifier": identifier,
                    "title": title or "",
                }
            )
            record.references[source_family] += 1
            self.archive_store.append_event(
                session_id,
                "reference_registered",
                {"source_family": source_family, "identifier": identifier, "title": title},
                ts,
            )
            self._flush_snapshot_locked(session_id)

    def record_cost(
        self,
        session_id: str,
        total_cost: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
        call_count: int,
        pricing_model: Optional[str] = None,
        last_response_model: Optional[str] = None,
        cost_source: str = "litellm_model_pricing_table",
    ) -> None:
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            if record.status in {"completed", "failed", "cancelled"} or record.completed_at is not None:
                logger.warning(
                    "Ignoring late cost update for terminal session %s (status=%s, completed_at=%s)",
                    session_id,
                    record.status,
                    _iso(record.completed_at),
                )
                return
            record.updated_at = ts
            record.cost = {
                "total_cost": total_cost,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "call_count": call_count,
                "pricing_model": pricing_model,
                "last_response_model": last_response_model,
                "cost_source": cost_source,
            }
            self.archive_store.append_event(session_id, "cost_updated", dict(record.cost), ts)
            self._flush_snapshot_locked(session_id)

    def record_coordinator_wave(self, session_id: str, launch_count: int) -> None:
        with self._lock:
            ts = _utcnow()
            record = self._ensure_record(session_id, updated_at=ts)
            record.updated_at = ts
            record.coordinator["wave_count"] += 1
            record.coordinator["launch_count"] += max(0, launch_count)
            self.archive_store.append_event(
                session_id,
                "coordinator_wave",
                {
                    "launch_count": launch_count,
                    "wave_count": record.coordinator["wave_count"],
                    "total_launch_count": record.coordinator["launch_count"],
                },
                ts,
            )
            self._flush_snapshot_locked(session_id)

    def clear_session_memory(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(session_id, None)


telemetry_registry = SessionTelemetryRegistry()
