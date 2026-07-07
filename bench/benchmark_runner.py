"""RACE benchmark runner built around the existing SPADE API."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from bench.direct_runner import run_direct_task
from src.config.settings import settings
from src.telemetry import SOURCE_FAMILIES
from src.utils.otel import configure_otel_flush_defaults, force_flush_otel, with_otel_flush_defaults

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is present in normal uv envs
    load_dotenv = None

DEFAULT_SYSTEM_NAME = "spade_deep_research"
GENERATOR_SYSTEM_NAMES = {
    "spade_api": DEFAULT_SYSTEM_NAME,
    "direct_no_tools": "gpt5_no_tools",
    "direct_tools": "gpt5_tools",
}
DEFAULT_API_BASE_URL = "http://localhost:8080"
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_CONCURRENCY = 1

if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
configure_otel_flush_defaults()

DEFAULT_JUDGE_MODEL = os.getenv("BENCHMARK_JUDGE_MODEL", "gpt-oss-120b")
DEFAULT_JUDGE_REQUEST_PROFILE = os.getenv("BENCHMARK_JUDGE_REQUEST_PROFILE", "compat_openai")

PILOT10_IDS = [17, 25, 31, 35, 43, 51, 67, 69, 86, 97]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _bench_repo_root() -> Path:
    return _repo_root().parent / "deep_research_bench"


def _get_benchmark_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["LLM_BACKEND"] = env.get("BENCHMARK_LLM_BACKEND", "openai")
    if "BENCHMARK_OPENAI_API_KEY" in env:
        env["OPENAI_API_KEY"] = env["BENCHMARK_OPENAI_API_KEY"]
    if "BENCHMARK_OPENAI_BASE_URL" in env:
        env["OPENAI_BASE_URL"] = env["BENCHMARK_OPENAI_BASE_URL"]
    return env


def _with_otel_flush_defaults(env: Dict[str, str]) -> Dict[str, str]:
    return dict(with_otel_flush_defaults(env))


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional_judge_cost(run_dir: Path) -> Optional[float]:
    """Load evaluator/judge cost if the scoring pipeline emitted a cost artifact.

    The external DRB scorer is a separate subprocess and currently does not
    guarantee a cost output. We support an optional `judge_cost.json` contract
    for future integration without making the benchmark summary ambiguous.
    """
    candidate = run_dir / "drb_results" / "judge_cost.json"
    if not candidate.exists():
        return None

    data = _read_json(candidate)
    raw_value = data.get("total_cost")
    if raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _median(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.median(values) if values else 0.0


def _p95(values: Iterable[float]) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    index = math.ceil(len(values) * 0.95) - 1
    return values[max(0, min(index, len(values) - 1))]


def _normalize_tool_name(tool_name: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z]+", "_", tool_name).strip("_").lower()
    return normalized or "unnamed_tool"


def _number(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _archive_events_path(row: Dict[str, Any]) -> Optional[Path]:
    archive = row.get("archive", {})
    events_path = archive.get("events_jsonl")
    if events_path:
        return Path(str(events_path))
    session_dir = archive.get("session_dir")
    if session_dir:
        return Path(str(session_dir)) / "events.jsonl"
    return None


def _archived_cumulative_cost(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    events_path = _archive_events_path(row)
    if events_path is None or not events_path.exists():
        return None

    totals = {
        "total_cost": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "call_count": 0,
        "pricing_model": None,
        "last_response_model": None,
        "cost_source": None,
    }
    previous_segment: Optional[Dict[str, Any]] = None
    saw_cost = False

    for event in _load_jsonl(events_path):
        if event.get("event_type") != "cost_updated":
            continue
        data = event.get("data")
        if not isinstance(data, dict) or not data.get("call_count"):
            continue
        saw_cost = True
        if previous_segment is None:
            delta = data
        elif (
            _number(data.get("call_count")) >= _number(previous_segment.get("call_count"))
            and _number(data.get("total_cost")) >= _number(previous_segment.get("total_cost"))
            and _number(data.get("total_tokens")) >= _number(previous_segment.get("total_tokens"))
        ):
            delta = {
                "total_cost": _number(data.get("total_cost")) - _number(previous_segment.get("total_cost")),
                "prompt_tokens": int(_number(data.get("prompt_tokens")) - _number(previous_segment.get("prompt_tokens"))),
                "completion_tokens": int(
                    _number(data.get("completion_tokens")) - _number(previous_segment.get("completion_tokens"))
                ),
                "total_tokens": int(_number(data.get("total_tokens")) - _number(previous_segment.get("total_tokens"))),
                "cache_creation_input_tokens": int(
                    _number(data.get("cache_creation_input_tokens"))
                    - _number(previous_segment.get("cache_creation_input_tokens"))
                ),
                "cache_read_input_tokens": int(
                    _number(data.get("cache_read_input_tokens")) - _number(previous_segment.get("cache_read_input_tokens"))
                ),
                "call_count": int(_number(data.get("call_count")) - _number(previous_segment.get("call_count"))),
            }
        else:
            # A lower counter means the in-memory tracker was cleared before a
            # late callback arrived, so this event starts a new cumulative segment.
            delta = data

        totals["total_cost"] += _number(delta.get("total_cost"))
        totals["prompt_tokens"] += int(_number(delta.get("prompt_tokens")))
        totals["completion_tokens"] += int(_number(delta.get("completion_tokens")))
        totals["total_tokens"] += int(_number(delta.get("total_tokens")))
        totals["cache_creation_input_tokens"] += int(_number(delta.get("cache_creation_input_tokens")))
        totals["cache_read_input_tokens"] += int(_number(delta.get("cache_read_input_tokens")))
        totals["call_count"] += int(_number(delta.get("call_count")))
        totals["pricing_model"] = data.get("pricing_model") or totals["pricing_model"]
        totals["last_response_model"] = data.get("last_response_model") or totals["last_response_model"]
        totals["cost_source"] = data.get("cost_source") or totals["cost_source"]
        previous_segment = data

    return totals if saw_cost else None


def _archived_cost_is_more_complete(current: Dict[str, Any], archived: Dict[str, Any]) -> bool:
    return (
        _number(archived.get("call_count")) > _number(current.get("call_count"))
        or _number(archived.get("total_tokens")) > _number(current.get("total_tokens"))
        or _number(archived.get("total_cost")) > _number(current.get("total_cost"))
    )


def backfill_missing_costs_from_archive(metrics_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill incomplete benchmark cost rows from archived late cost events."""
    repaired_rows: List[Dict[str, Any]] = []
    for row in metrics_rows:
        repaired = dict(row)
        current_cost = row.get("cost", {})
        archived_cost = _archived_cumulative_cost(row)
        if archived_cost and _archived_cost_is_more_complete(current_cost, archived_cost):
            repaired["cost"] = dict(archived_cost)
            repaired["cost_backfilled_from_archive"] = True
        repaired_rows.append(repaired)
    return repaired_rows


def flatten_metrics_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten archived session metrics into a thesis-friendly table row."""
    flat = {
        "task_id": row.get("task_id"),
        "language": row.get("language"),
        "prompt": row.get("prompt"),
        "session_id": row.get("session_id"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "completed_at": row.get("completed_at"),
        "total_wall_clock_seconds": row.get("total_wall_clock_seconds", 0.0),
        "total_cost": row.get("cost", {}).get("total_cost", 0.0),
        "prompt_tokens": row.get("cost", {}).get("prompt_tokens", 0),
        "completion_tokens": row.get("cost", {}).get("completion_tokens", 0),
        "total_tokens": row.get("cost", {}).get("total_tokens", 0),
        "cache_creation_input_tokens": row.get("cost", {}).get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": row.get("cost", {}).get("cache_read_input_tokens", 0),
        "llm_call_count": row.get("cost", {}).get("call_count", 0),
        "pricing_model": row.get("cost", {}).get("pricing_model"),
        "last_response_model": row.get("cost", {}).get("last_response_model"),
        "cost_source": row.get("cost", {}).get("cost_source"),
        "report_word_count": row.get("report", {}).get("word_count", 0),
        "report_char_count": row.get("report", {}).get("char_count", 0),
        "coordinator_launch_count": row.get("coordinator", {}).get("launch_count", 0),
        "coordinator_wave_count": row.get("coordinator", {}).get("wave_count", 0),
        "archive_session_dir": row.get("archive", {}).get("session_dir"),
    }

    for phase, duration in row.get("phase_durations_seconds", {}).items():
        flat[f"phase_{phase}_seconds"] = duration

    tool_usage = row.get("tool_usage", {}).get("by_source_family", {})
    ref_counts = row.get("references", {}).get("by_source_family", {})
    used_refs = row.get("report", {}).get("used_references", {}).get("by_source_family", {})
    metric_families = tuple(dict.fromkeys((*SOURCE_FAMILIES, "unknown", *ref_counts.keys(), *used_refs.keys())))
    for family in metric_families:
        family_tool = tool_usage.get(family, {})
        flat[f"{family}_tool_attempts"] = family_tool.get("attempts", 0)
        flat[f"{family}_tool_successes"] = family_tool.get("successes", 0)
        flat[f"{family}_tool_failures"] = family_tool.get("failures", 0)
        flat[f"{family}_tool_duration_seconds"] = family_tool.get("duration_seconds", 0.0)
        flat[f"{family}_reference_count"] = ref_counts.get(family, 0)
        flat[f"{family}_used_reference_count"] = used_refs.get(family, 0)

        for tool_name, tool_stats in sorted(family_tool.get("tools", {}).items()):
            tool_key = f"{family}_{_normalize_tool_name(tool_name)}"
            flat[f"{tool_key}_attempts"] = tool_stats.get("attempts", 0)
            flat[f"{tool_key}_successes"] = tool_stats.get("successes", 0)
            flat[f"{tool_key}_failures"] = tool_stats.get("failures", 0)
            flat[f"{tool_key}_duration_seconds"] = tool_stats.get("duration_seconds", 0.0)

    flat["total_tool_attempts"] = row.get("tool_usage", {}).get("total_attempts", 0)
    flat["total_tool_duration_seconds"] = row.get("tool_usage", {}).get("total_duration_seconds", 0.0)
    flat["total_reference_count"] = row.get("references", {}).get("total", 0)
    flat["total_used_reference_count"] = row.get("report", {}).get("used_references", {}).get("total", 0)
    return flat


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_tasks(query_file: Path) -> List[Dict[str, Any]]:
    tasks = _load_jsonl(query_file)
    tasks.sort(key=lambda item: int(item["id"]))
    return tasks


def resolve_profile_tasks(
    tasks: List[Dict[str, Any]],
    profile: str,
    custom_task_ids: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    by_id = {int(task["id"]): task for task in tasks}
    if custom_task_ids:
        selected_ids = custom_task_ids
    elif profile == "pilot1_en":
        selected_ids = [51]
    elif profile == "pilot10_mix":
        selected_ids = PILOT10_IDS
    elif profile == "full100":
        selected_ids = [int(task["id"]) for task in tasks]
    else:
        raise ValueError(f"Unknown profile: {profile}")

    missing = [task_id for task_id in selected_ids if task_id not in by_id]
    if missing:
        raise ValueError(f"Task IDs not found in query file: {missing}")

    return [by_id[task_id] for task_id in selected_ids]


def build_run_id(profile: str) -> str:
    return f"{profile}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _default_direct_model() -> str:
    return settings.get_model_for_agent("writer")


def aggregate_results(
    metrics_rows: List[Dict[str, Any]],
    score_rows: List[Dict[str, Any]],
    judge_cost_total: Optional[float] = None,
) -> Dict[str, Any]:
    scores_by_id = {int(row["id"]): row for row in score_rows if "id" in row}
    merged_rows = []
    for metric in metrics_rows:
        merged = dict(metric)
        score = scores_by_id.get(int(metric["task_id"]))
        if score:
            merged["race"] = score
        merged_rows.append(merged)

    successful = [row for row in merged_rows if row.get("status") == "completed"]
    failed = [row for row in merged_rows if row.get("status") == "failed"]
    cancelled = [row for row in merged_rows if row.get("status") == "cancelled"]
    total = len(merged_rows)

    latencies = [row.get("total_wall_clock_seconds", 0.0) for row in merged_rows]
    costs = [row.get("cost", {}).get("total_cost", 0.0) for row in merged_rows]
    prompt_tokens = [row.get("cost", {}).get("prompt_tokens", 0) for row in merged_rows]
    completion_tokens = [row.get("cost", {}).get("completion_tokens", 0) for row in merged_rows]
    total_tokens = [row.get("cost", {}).get("total_tokens", 0) for row in merged_rows]
    cache_creation_input_tokens = [row.get("cost", {}).get("cache_creation_input_tokens", 0) for row in merged_rows]
    cache_read_input_tokens = [row.get("cost", {}).get("cache_read_input_tokens", 0) for row in merged_rows]
    llm_calls = [row.get("cost", {}).get("call_count", 0) for row in merged_rows]
    total_tool_calls = [row.get("tool_usage", {}).get("total_attempts", 0) for row in merged_rows]
    report_words = [row.get("report", {}).get("word_count", 0) for row in successful]

    race_rows = [row["race"] for row in merged_rows if row.get("race") and "error" not in row["race"]]
    race_summary = {
        "comprehensiveness": _mean(row.get("comprehensiveness", 0.0) for row in race_rows),
        "insight": _mean(row.get("insight", 0.0) for row in race_rows),
        "instruction_following": _mean(row.get("instruction_following", 0.0) for row in race_rows),
        "readability": _mean(row.get("readability", 0.0) for row in race_rows),
        "overall": _mean(row.get("overall_score", 0.0) for row in race_rows),
    }

    phase_names = sorted({name for row in metrics_rows for name in row.get("phase_durations_seconds", {}).keys()})
    phase_means = {
        phase: _mean(row.get("phase_durations_seconds", {}).get(phase, 0.0) for row in metrics_rows)
        for phase in phase_names
    }

    by_language = []
    for language in sorted({row.get("language", "") for row in metrics_rows}):
        rows = [row for row in merged_rows if row.get("language") == language]
        lang_scores = [row["race"] for row in rows if row.get("race") and "error" not in row["race"]]
        by_language.append(
            {
                "language": language,
                "task_count": len(rows),
                "completion_rate": len([row for row in rows if row.get("status") == "completed"]) / len(rows) if rows else 0.0,
                "failure_rate": len([row for row in rows if row.get("status") == "failed"]) / len(rows) if rows else 0.0,
                "cancellation_rate": len([row for row in rows if row.get("status") == "cancelled"]) / len(rows) if rows else 0.0,
                "overall_race": _mean(score.get("overall_score", 0.0) for score in lang_scores),
                "avg_latency_seconds": _mean(row.get("total_wall_clock_seconds", 0.0) for row in rows),
                "avg_cost": _mean(row.get("cost", {}).get("total_cost", 0.0) for row in rows),
            }
        )

    by_source_family = []
    for family in SOURCE_FAMILIES:
        attempts = [row.get("tool_usage", {}).get("by_source_family", {}).get(family, {}).get("attempts", 0) for row in metrics_rows]
        durations = [row.get("tool_usage", {}).get("by_source_family", {}).get(family, {}).get("duration_seconds", 0.0) for row in metrics_rows]
        references = [row.get("references", {}).get("by_source_family", {}).get(family, 0) for row in metrics_rows]
        by_source_family.append(
            {
                "source_family": family,
                "total_tool_attempts": sum(attempts),
                "avg_tool_attempts": _mean(attempts),
                "total_tool_duration_seconds": sum(durations),
                "avg_tool_duration_seconds": _mean(durations),
                "total_reference_count": sum(references),
                "avg_reference_count": _mean(references),
            }
        )

    tool_dimensions = sorted(
        {
            (family, tool_name)
            for row in metrics_rows
            for family, family_stats in row.get("tool_usage", {}).get("by_source_family", {}).items()
            for tool_name in family_stats.get("tools", {}).keys()
        }
    )
    by_tool = []
    for family, tool_name in tool_dimensions:
        attempts = []
        successes = []
        failures = []
        durations = []
        for row in metrics_rows:
            tool_stats = (
                row.get("tool_usage", {})
                .get("by_source_family", {})
                .get(family, {})
                .get("tools", {})
                .get(tool_name, {})
            )
            attempts.append(tool_stats.get("attempts", 0))
            successes.append(tool_stats.get("successes", 0))
            failures.append(tool_stats.get("failures", 0))
            durations.append(tool_stats.get("duration_seconds", 0.0))
        by_tool.append(
            {
                "source_family": family,
                "tool_name": tool_name,
                "tool_key": f"{family}_{_normalize_tool_name(tool_name)}",
                "total_tool_attempts": sum(attempts),
                "avg_tool_attempts": _mean(attempts),
                "total_tool_successes": sum(successes),
                "avg_tool_successes": _mean(successes),
                "total_tool_failures": sum(failures),
                "avg_tool_failures": _mean(failures),
                "total_tool_duration_seconds": sum(durations),
                "avg_tool_duration_seconds": _mean(durations),
            }
        )

    total_output_words = sum(report_words)
    research_total_cost = sum(costs)
    known_total_cost = research_total_cost + (judge_cost_total or 0.0)
    summary = {
        "task_count": total,
        "completed_count": len(successful),
        "failed_count": len(failed),
        "cancelled_count": len(cancelled),
        "completion_rate": len(successful) / total if total else 0.0,
        "failure_rate": len(failed) / total if total else 0.0,
        "cancellation_rate": len(cancelled) / total if total else 0.0,
        "race": race_summary,
        "latency_seconds": {
            "mean": _mean(latencies),
            "median": _median(latencies),
            "p95": _p95(latencies),
        },
        "phase_duration_seconds": phase_means,
        "cost": {
            "total": known_total_cost,
            "average": _mean(costs),
            "cost_per_successful_task": (research_total_cost / len(successful)) if successful else 0.0,
            "research_total": research_total_cost,
            "judge_total": judge_cost_total,
            "judge_cost_status": "tracked" if judge_cost_total is not None else "not_tracked",
        },
        "tokens": {
            "prompt_total": sum(prompt_tokens),
            "prompt_average": _mean(prompt_tokens),
            "completion_total": sum(completion_tokens),
            "completion_average": _mean(completion_tokens),
            "total": sum(total_tokens),
            "average": _mean(total_tokens),
            "cache_creation_input_total": sum(cache_creation_input_tokens),
            "cache_read_input_total": sum(cache_read_input_tokens),
            "per_1k_output_words": ((sum(total_tokens) / total_output_words) * 1000.0) if total_output_words else 0.0,
        },
        "llm_calls": {
            "total": sum(llm_calls),
            "average": _mean(llm_calls),
        },
        "tool_calls": {
            "total": sum(total_tool_calls),
            "average": _mean(total_tool_calls),
            "distribution_by_source_family": by_source_family,
            "distribution_by_tool": by_tool,
        },
        "references": {
            "distribution_by_source_family": by_source_family,
        },
        "report_word_count": {
            "average": _mean(report_words),
        },
        "latency_per_1k_output_words": ((sum(latencies) / total_output_words) * 1000.0) if total_output_words else 0.0,
        "by_language": by_language,
    }
    return summary


@dataclass
class RunnerConfig:
    profile: str
    task_ids: Optional[List[int]]
    run_id: str
    output_dir: Path
    api_base_url: str = DEFAULT_API_BASE_URL
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    concurrency: int = DEFAULT_CONCURRENCY
    judge_model: str = DEFAULT_JUDGE_MODEL
    judge_request_profile: str = DEFAULT_JUDGE_REQUEST_PROFILE
    official_judge: bool = False
    query_file: Path = _bench_repo_root() / "data" / "prompt_data" / "query.jsonl"
    bench_repo: Path = _bench_repo_root()
    resume: bool = False
    score_only: bool = False
    incremental_scoring: bool = True
    generator: str = "spade_api"
    variant_name: Optional[str] = None
    system_name: Optional[str] = None
    direct_model: Optional[str] = None


class RaceBenchmarkRunner:
    """End-to-end runner for SPADE + DRB RACE evaluation."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.system_name = config.system_name or GENERATOR_SYSTEM_NAMES.get(config.generator, DEFAULT_SYSTEM_NAME)
        self.run_dir = config.output_dir.resolve() / config.run_id
        self.raw_data_dir = self.run_dir / "raw_data"
        self.cleaned_data_dir = self.run_dir / "cleaned_data"
        self.drb_output_dir = self.run_dir / "drb_results"
        self.raw_data_path = self.raw_data_dir / f"{self.system_name}.jsonl"
        self.metrics_jsonl_path = self.run_dir / "spade_metrics.jsonl"
        self.metrics_csv_path = self.run_dir / "spade_metrics.csv"
        self.failures_path = self.run_dir / "failures.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        self.summary_path = self.run_dir / "summary.json"
        self.aggregate_overall_csv = self.run_dir / "aggregate_overall.csv"
        self.aggregate_language_csv = self.run_dir / "aggregate_by_language.csv"
        self.aggregate_source_csv = self.run_dir / "aggregate_by_source_family.csv"
        self.aggregate_tool_csv = self.run_dir / "aggregate_by_tool.csv"
        self._manifest_lock = threading.RLock()
        self._output_lock = threading.Lock()

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self.config.api_base_url.rstrip("/") + path
        body = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc

    def _archive_snapshot_path(self, session_id: str) -> Path:
        return _repo_root() / "bench" / "session_archive" / session_id / "session.json"

    def _load_archive_snapshot(self, session_id: str) -> Dict[str, Any]:
        snapshot_path = self._archive_snapshot_path(session_id)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Archived snapshot not found for session {session_id}: {snapshot_path}")
        return _read_json(snapshot_path)

    def _read_manifest(self) -> Dict[str, Any]:
        return _read_json(self.manifest_path) if self.manifest_path.exists() else {}

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        with self._manifest_lock:
            _write_json(self.manifest_path, manifest)

    def _append_output_row(self, path: Path, row: Dict[str, Any]) -> None:
        with self._output_lock:
            _append_jsonl(path, row)

    def _build_manifest(self, selected_tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "run_id": self.config.run_id,
            "system_name": self.system_name,
            "generator": self.config.generator,
            "variant_name": self.config.variant_name or self.config.generator,
            "direct_model": self.config.direct_model or _default_direct_model(),
            "profile": self.config.profile,
            "task_ids": [int(task["id"]) for task in selected_tasks],
            "api_base_url": self.config.api_base_url,
            "timeout_seconds": self.config.timeout_seconds,
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "concurrency": self.config.concurrency,
            "official_judge": self.config.official_judge,
            "evaluator": {
                "backend": "openai",
                "model": self.config.judge_model,
                "request_profile": self.config.judge_request_profile,
            },
            "query_file": str(self.config.query_file),
            "bench_repo": str(self.config.bench_repo),
            "created_at": datetime.now().isoformat(),
            "tasks": [
                {
                    "id": int(task["id"]),
                    "prompt": task["prompt"],
                    "language": task.get("language"),
                }
                for task in selected_tasks
            ],
            "sessions": {},
        }

    def _refresh_manifest_evaluator(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        manifest["system_name"] = self.system_name
        manifest["generator"] = self.config.generator
        manifest["variant_name"] = self.config.variant_name or self.config.generator
        manifest["direct_model"] = self.config.direct_model or _default_direct_model()
        manifest["official_judge"] = self.config.official_judge
        manifest["evaluator"] = {
            "backend": "openai",
            "model": self.config.judge_model,
            "request_profile": self.config.judge_request_profile,
        }
        return manifest

    def _existing_completed_ids(self) -> set[int]:
        return {int(row["task_id"]) for row in _load_jsonl(self.metrics_jsonl_path)}

    def _run_task(self, task: Dict[str, Any], manifest: Dict[str, Any]) -> bool:
        if self.config.generator == "spade_api":
            return self._run_spade_api_task(task, manifest)
        if self.config.generator in {"direct_no_tools", "direct_tools"}:
            return self._run_direct_task(task, manifest)
        raise ValueError(f"Unknown generator: {self.config.generator}")

    def _run_spade_api_task(self, task: Dict[str, Any], manifest: Dict[str, Any]) -> bool:
        create_response = self._request("POST", "/research", {"query": task["prompt"]})
        session_id = create_response["session_id"]
        with self._manifest_lock:
            manifest["sessions"][str(task["id"])] = {
                "task_id": int(task["id"]),
                "session_id": session_id,
                "status_url": f"{self.config.api_base_url.rstrip('/')}/research/{session_id}",
                "archive_dir": str((_repo_root() / "bench" / "session_archive" / session_id)),
                "archive_session_json": str(self._archive_snapshot_path(session_id)),
                "created_via_api_at": datetime.now().isoformat(),
            }
        self._write_manifest(manifest)

        deadline = time.time() + self.config.timeout_seconds
        latest_status = None
        while time.time() < deadline:
            status_payload = self._request("GET", f"/research/{session_id}")
            latest_status = status_payload.get("status")
            if latest_status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(self.config.poll_interval_seconds)
        else:
            latest_status = "timeout"

        if latest_status == "timeout":
            failure = {
                "task_id": int(task["id"]),
                "session_id": session_id,
                "prompt": task["prompt"],
                "reason": "timeout",
                "timeout_seconds": self.config.timeout_seconds,
            }
            _append_jsonl(self.failures_path, failure)
            return False

        snapshot = self._load_archive_snapshot(session_id)
        metrics_row = dict(snapshot)
        metrics_row["task_id"] = int(task["id"])
        metrics_row["prompt"] = task["prompt"]
        metrics_row["language"] = task.get("language")
        metrics_row["session_id"] = session_id
        self._append_output_row(self.metrics_jsonl_path, metrics_row)

        if snapshot.get("status") == "completed":
            article = ""
            report_path = Path(snapshot.get("archive", {}).get("report_md", ""))
            if report_path.exists():
                article = report_path.read_text(encoding="utf-8")
            self._append_output_row(
                self.raw_data_path,
                {
                    "id": int(task["id"]),
                    "prompt": task["prompt"],
                    "article": article,
                },
            )
            return True
        else:
            self._append_output_row(
                self.failures_path,
                {
                    "task_id": int(task["id"]),
                    "session_id": session_id,
                    "prompt": task["prompt"],
                    "status": snapshot.get("status"),
                    "archive_session_json": snapshot.get("archive", {}).get("session_json"),
                },
            )
            return False

    def _run_direct_task(self, task: Dict[str, Any], manifest: Dict[str, Any]) -> bool:
        result = run_direct_task(
            task,
            model=self.config.direct_model or _default_direct_model(),
            use_tools=self.config.generator == "direct_tools",
        )
        session_id = result["session_id"]
        with self._manifest_lock:
            manifest["sessions"][str(task["id"])] = {
                "task_id": int(task["id"]),
                "session_id": session_id,
                "generator": self.config.generator,
                "archive_dir": result["metrics"].get("archive", {}).get("session_dir"),
                "archive_session_json": result["metrics"].get("archive", {}).get("session_json"),
                "created_direct_at": datetime.now().isoformat(),
            }
        self._write_manifest(manifest)

        self._append_output_row(self.metrics_jsonl_path, result["metrics"])
        if result["completed"]:
            self._append_output_row(
                self.raw_data_path,
                {
                    "id": int(task["id"]),
                    "prompt": task["prompt"],
                    "article": result["article"],
                },
            )
            return True

        self._append_output_row(
            self.failures_path,
            {
                "task_id": int(task["id"]),
                "session_id": session_id,
                "prompt": task["prompt"],
                "status": result["metrics"].get("status"),
                "error": result["failure"],
                "archive_session_json": result["metrics"].get("archive", {}).get("session_json"),
            },
        )
        return False

    def _run_drb_scoring(self, *, force: bool = False) -> None:
        env = _with_otel_flush_defaults(_get_benchmark_env())
        env["RACE_MODEL"] = self.config.judge_model
        env["LLM_REQUEST_PROFILE"] = self.config.judge_request_profile
        cmd = [
            sys.executable,
            str(_repo_root() / "scripts" / "run_drb_scoring.py"),
            self.system_name,
            "--raw_data_dir",
            str(self.raw_data_dir),
            "--cleaned_data_dir",
            str(self.cleaned_data_dir),
            "--query_file",
            str(self.config.query_file),
            "--output_dir",
            str(self.drb_output_dir),
            "--max_workers",
            "1",
        ]
        if force:
            cmd.append("--force")
        subprocess.run(cmd, cwd=_repo_root(), env=env, check=True)

    def _maybe_run_incremental_scoring(self, completed: bool) -> None:
        if not completed or not self.config.incremental_scoring or not self.raw_data_path.exists():
            return
        self._run_drb_scoring(force=False)

    def _write_post_run_outputs(self) -> None:
        metrics_rows = backfill_missing_costs_from_archive(_load_jsonl(self.metrics_jsonl_path))
        _write_jsonl(self.metrics_jsonl_path, metrics_rows)
        flat_rows = [flatten_metrics_row(row) for row in metrics_rows]
        write_csv(self.metrics_csv_path, flat_rows)

        raw_results_path = self.drb_output_dir / "raw_results.jsonl"
        score_rows = _load_jsonl(raw_results_path)
        judge_cost_total = _load_optional_judge_cost(self.run_dir)
        summary = aggregate_results(metrics_rows, score_rows, judge_cost_total=judge_cost_total)
        summary["run_id"] = self.config.run_id
        summary["system_name"] = self.system_name
        summary["generator"] = self.config.generator
        summary["variant_name"] = self.config.variant_name or self.config.generator
        summary["direct_model"] = self.config.direct_model or _default_direct_model()
        summary["official_judge"] = self.config.official_judge
        summary["judge_model"] = self.config.judge_model
        summary["judge_request_profile"] = self.config.judge_request_profile
        _write_json(self.summary_path, summary)

        overall_rows = [
            {
                "metric": key,
                "value": value,
            }
            for key, value in summary["race"].items()
        ] + [
            {"metric": "completion_rate", "value": summary["completion_rate"]},
            {"metric": "failure_rate", "value": summary["failure_rate"]},
            {"metric": "cancellation_rate", "value": summary["cancellation_rate"]},
            {"metric": "latency_mean_seconds", "value": summary["latency_seconds"]["mean"]},
            {"metric": "latency_median_seconds", "value": summary["latency_seconds"]["median"]},
            {"metric": "latency_p95_seconds", "value": summary["latency_seconds"]["p95"]},
            {"metric": "cost_total", "value": summary["cost"]["total"]},
            {"metric": "cost_average", "value": summary["cost"]["average"]},
            {"metric": "tokens_total", "value": summary["tokens"]["total"]},
            {"metric": "tokens_average", "value": summary["tokens"]["average"]},
            {"metric": "llm_calls_total", "value": summary["llm_calls"]["total"]},
            {"metric": "tool_calls_total", "value": summary["tool_calls"]["total"]},
        ]
        write_csv(self.aggregate_overall_csv, overall_rows)
        write_csv(self.aggregate_language_csv, summary["by_language"])
        write_csv(self.aggregate_source_csv, summary["tool_calls"]["distribution_by_source_family"])
        write_csv(self.aggregate_tool_csv, summary["tool_calls"]["distribution_by_tool"])

    def run(self) -> Dict[str, Any]:
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if self.config.score_only:
                if self.raw_data_path.exists():
                    self._run_drb_scoring(force=False)
                self._write_post_run_outputs()
                return self._read_manifest()

            tasks = load_tasks(self.config.query_file)
            selected_tasks = resolve_profile_tasks(tasks, self.config.profile, self.config.task_ids)

            if self.config.resume and self.manifest_path.exists():
                manifest = self._read_manifest()
                manifest = self._refresh_manifest_evaluator(manifest)
                self._write_manifest(manifest)
            else:
                manifest = self._build_manifest(selected_tasks)
                self._write_manifest(manifest)

            completed_ids = self._existing_completed_ids() if self.config.resume else set()
            pending_tasks = [task for task in selected_tasks if int(task["id"]) not in completed_ids]
            if self.config.concurrency <= 1:
                for task in pending_tasks:
                    completed = self._run_task(task, manifest)
                    self._maybe_run_incremental_scoring(completed)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.concurrency) as executor:
                    futures = [executor.submit(self._run_task, task, manifest) for task in pending_tasks]
                    for future in concurrent.futures.as_completed(futures):
                        completed = future.result()
                        self._maybe_run_incremental_scoring(completed)

            if self.raw_data_path.exists():
                self._run_drb_scoring(force=False)
            self._write_post_run_outputs()
            return self._read_manifest()
        finally:
            force_flush_otel()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SPADE against DRB RACE tasks.")
    parser.add_argument("--profile", default="pilot1_en", choices=["pilot1_en", "pilot10_mix", "full100"])
    parser.add_argument("--task-ids", help="Comma-separated custom task IDs to run")
    parser.add_argument("--run-id", help="Override output run ID")
    parser.add_argument("--output-dir", default=str(_repo_root() / "bench" / "eval_runs"))
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--judge-request-profile", default=DEFAULT_JUDGE_REQUEST_PROFILE, choices=["compat_openai", "reasoning_openai"])
    parser.add_argument("--official-judge", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--score-only", action="store_true", help="Only run DRB scoring/final aggregation for an existing benchmark run")
    parser.add_argument("--no-incremental-scoring", action="store_true", help="Disable score persistence after each completed task")
    parser.add_argument("--generator", default="spade_api", choices=["spade_api", "direct_no_tools", "direct_tools"])
    parser.add_argument("--variant-name", help="Human-readable ablation variant name recorded in manifests")
    parser.add_argument("--system-name", help="Target model/system name used for DRB raw-data file and scoring")
    parser.add_argument("--direct-model", default=_default_direct_model(), help="LiteLLM model for direct GPT baselines")
    return parser


def parse_task_ids(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = RunnerConfig(
        profile=args.profile,
        task_ids=parse_task_ids(args.task_ids),
        run_id=args.run_id or build_run_id(args.profile),
        output_dir=Path(args.output_dir),
        api_base_url=args.api_base_url,
        timeout_seconds=args.timeout,
        poll_interval_seconds=args.poll_interval,
        concurrency=args.concurrency,
        judge_model=args.judge_model,
        judge_request_profile=args.judge_request_profile,
        official_judge=args.official_judge or args.judge_request_profile == "reasoning_openai",
        resume=args.resume,
        score_only=args.score_only,
        incremental_scoring=not args.no_incremental_scoring,
        generator=args.generator,
        variant_name=args.variant_name,
        system_name=args.system_name,
        direct_model=args.direct_model,
    )
    RaceBenchmarkRunner(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
