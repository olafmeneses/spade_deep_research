"""Build a self-contained static HTML dashboard for benchmark runs."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import statistics
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH_ROOT = REPO_ROOT.parent / "deep_research_bench"
LOGO_PATH = REPO_ROOT / "diagrams" / "logo-transparent.png"
FALLBACK_LOGO_PATH = REPO_ROOT / "diagrams" / "logo.png"
FAVICON_PATH = REPO_ROOT / "diagrams" / "favicon-white.png"
STATIC_DASHBOARD_TEMPLATE_PATH = Path(__file__).with_name("static_dashboard_template.html")
STATIC_DASHBOARD_CSS_PATH = Path(__file__).with_name("static_dashboard.css")
SCORE_KEYS = ["overall_score", "comprehensiveness", "insight", "instruction_following", "readability"]
HIDDEN_SOURCE_FAMILIES = {"knowledge_base"}
FALLBACK_FAVICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='12' fill='%231f2428'/%3E%3Cpath d='M43 16H24c-8 0-13 4-13 10s5 10 13 10h16c5 0 8 2 8 6s-3 6-8 6H19' fill='none' stroke='%23fff' stroke-width='8' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='46' cy='18' r='5' fill='%234f78ba'/%3E%3C/svg%3E"
AGENT_PHASES = [
    ("Planner", "planner"),
    ("Coordinator", "coordinator"),
    ("Subagents", "subagents"),
    ("Writer", "writer"),
    ("Critic", "critic"),
]
SUBAGENT_TYPES = ["arxiv", "tavily", "knowledge_base"]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path} at line {line_number}: {exc}") from exc
    return rows


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def image_data_url(path: Path, mime_type: str = "image/png") -> str | None:
    if not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    index = math.ceil(len(values) * q) - 1
    return values[max(0, min(index, len(values) - 1))]


def short_text(value: str, limit: int = 180) -> str:
    compact = " ".join(value.split())
    effective_limit = math.ceil(limit * 1.12) if re.search(r"[\u3400-\u9fff]", compact) else limit
    if len(compact) <= effective_limit:
        return compact
    clipped = compact[: effective_limit - 1].rstrip()
    if not re.search(r"[\u3400-\u9fff]", compact):
        boundary = max(clipped.rfind(" "), clipped.rfind("-"), clipped.rfind("/"))
        if boundary >= max(12, int(effective_limit * 0.55)):
            clipped = clipped[:boundary].rstrip(" -/")
    return clipped + "..."


def display_title(task: dict[str, Any]) -> str:
    prompt = str(task.get("prompt") or "")
    if not prompt:
        return f"Task {task.get('id', task.get('task_id', '?'))}"
    first_line = next((part.strip(" \"'") for part in prompt.splitlines() if part.strip()), prompt)
    return " ".join(first_line.split())


def visible_families(families: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in families.items()
        if key not in HIDDEN_SOURCE_FAMILIES and number(value.get("attempts") if isinstance(value, dict) else value) > 0
    }


def visible_reference_families(families: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in families.items() if key not in HIDDEN_SOURCE_FAMILIES and number(value) > 0}


def html_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_template(path: Path, replacements: dict[str, str]) -> str:
    template = path.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def read_text_if_exists(path_value: Any) -> str:
    if not path_value:
        return ""
    try:
        path = Path(str(path_value))
    except TypeError:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        values[key.strip()] = value
    return values


def compact_model_name(model: str) -> str:
    return str(model or "").removeprefix("openai/").strip()


def configured_model(env_values: dict[str, str], agent_type: str) -> str:
    default = os.environ.get("LLM_DEFAULT") or env_values.get("LLM_DEFAULT") or ""
    return os.environ.get(f"LLM_{agent_type.upper()}") or env_values.get(f"LLM_{agent_type.upper()}") or default


def agent_phase_models(env_values: dict[str, str]) -> list[dict[str, str]]:
    subagent_models = {
        compact_model_name(configured_model(env_values, agent_type))
        for agent_type in SUBAGENT_TYPES
        if configured_model(env_values, agent_type)
    }
    rows: list[dict[str, str]] = []
    for label, agent_type in AGENT_PHASES:
        if agent_type == "subagents":
            model = ", ".join(sorted(subagent_models))
        else:
            model = compact_model_name(configured_model(env_values, agent_type))
        if model:
            rows.append({"label": label, "model": model})
    return rows


def clean_reference_url(url: str) -> str:
    return str(url or "").strip().rstrip("\\").rstrip(".,;")


def infer_source_family(identifier: str) -> str:
    value = clean_reference_url(identifier).lower()
    if not value:
        return "unknown"
    if "arxiv.org" in value or re.search(r"(?:^|/)arxiv:\d{4}\.\d{4,5}", value):
        return "arxiv"
    if value.startswith(("http://", "https://", "www.")):
        return "tavily"
    return "unknown"


def archived_reference_links(archive: dict[str, Any]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    events_path = archive.get("events_jsonl")
    if not events_path:
        return links
    try:
        path = Path(str(events_path))
    except TypeError:
        return links
    if not path.exists() or not path.is_file():
        return links
    for row in load_jsonl(path):
        if row.get("event_type") != "reference_registered":
            continue
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue
        identifier = str(data.get("identifier") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        links.append(
            {
                "identifier": identifier,
                "title": str(data.get("title") or ""),
                "source_family": str(data.get("source_family") or "unknown"),
            }
        )
    return links


def used_reference_metrics(article: str, references: list[dict[str, str]]) -> dict[str, Any]:
    ref_by_url = {clean_reference_url(ref.get("identifier", "")): ref for ref in references if ref.get("identifier")}
    used_keys: set[str] = set()
    by_family: dict[str, int] = {}

    def add_reference(raw_number: str, url: str = "") -> None:
        key = ""
        ref: dict[str, str] | None = None
        cleaned_url = clean_reference_url(url)
        if cleaned_url:
            key = cleaned_url
            ref = ref_by_url.get(cleaned_url)
        else:
            index = int(raw_number) - 1
            if 0 <= index < len(references):
                ref = references[index]
                key = clean_reference_url(ref.get("identifier", "")) or f"index:{index}"
        if not key or key in used_keys:
            return
        used_keys.add(key)
        family = (ref or {}).get("source_family") or infer_source_family(key)
        by_family[family] = by_family.get(family, 0) + 1

    def add_arxiv_id(arxiv_id: str) -> None:
        key = f"https://arxiv.org/abs/{arxiv_id}"
        if key in used_keys:
            return
        used_keys.add(key)
        by_family["arxiv"] = by_family.get("arxiv", 0) + 1

    patterns = [
        re.compile(r"\[\[(\d+)(?::\s*([\s\S]*?))?\]\]\(([^)\n]*?)(?:\\)?\)"),
        re.compile(r"(?<!!)\[(\d+)(?::\s*([\s\S]*?))?\]\(([^)\n]*?)(?:\\)?\)"),
    ]
    consumed_spans: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in pattern.finditer(article or ""):
            add_reference(match.group(1), match.group(3))
            consumed_spans.append(match.span())

    def is_consumed(start: int, end: int) -> bool:
        return any(span_start <= start and end <= span_end for span_start, span_end in consumed_spans)

    for match in re.finditer(r"(?<!\[)\[(\d+)(?::\s*([^\]\n]+))?\](?![\]\(])", article or ""):
        if not is_consumed(*match.span()):
            add_reference(match.group(1))
    for match in re.finditer(r"【(\d+)】", article or ""):
        add_reference(match.group(1))
    for match in re.finditer(r"［(\d+)］", article or ""):
        add_reference(match.group(1))
    for match in re.finditer(r"\[(\d{4}\.\d{4,5}(?:v\d+)?)\]", article or ""):
        add_arxiv_id(match.group(1))
    for match in re.finditer(
        r"\((arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s*,\s*(?:arXiv:)?\d{4}\.\d{4,5}(?:v\d+)?)*)\)",
        article or "",
        re.IGNORECASE,
    ):
        for arxiv_id in re.findall(r"(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", match.group(1), re.IGNORECASE):
            add_arxiv_id(arxiv_id)

    return {"total": len(used_keys), "by_source_family": dict(sorted(by_family.items()))}


def derive_paths(run_dir: Path, system_name: str) -> dict[str, Path | None]:
    raw_path = first_existing(
        [
            run_dir / "raw_data" / f"{system_name}.jsonl",
            run_dir / "raw_data" / "spade_deep_research.jsonl",
        ]
    )
    cleaned_path = first_existing(
        [
            run_dir / "cleaned_data" / f"{system_name}.jsonl",
            run_dir / "cleaned_data" / "spade_deep_research.jsonl",
        ]
    )
    return {
        "raw": raw_path,
        "cleaned": cleaned_path,
        "metrics": run_dir / "spade_metrics.jsonl",
        "scores": run_dir / "drb_results" / "raw_results.jsonl",
        "judge_cost": run_dir / "drb_results" / "judge_cost_calls.jsonl",
        "failures": run_dir / "failures.jsonl",
    }


def build_tasks(
    *,
    run_dir: Path,
    query_file: Path,
    criteria_file: Path,
    system_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = derive_paths(run_dir, system_name)
    queries = {int(row["id"]): row for row in load_jsonl(query_file) if row.get("id") is not None}
    criteria = {int(row["id"]): row for row in load_jsonl(criteria_file) if row.get("id") is not None}
    metrics = {
        int(row.get("task_id", row.get("id"))): row
        for row in load_jsonl(paths["metrics"] or Path())
        if row.get("task_id", row.get("id")) is not None
    }
    scores = {
        int(row["id"]): row
        for row in load_jsonl(paths["scores"] or Path())
        if row.get("id") is not None
    }
    raw_articles = {
        int(row["id"]): row
        for row in load_jsonl(paths["raw"] or Path())
        if row.get("id") is not None
    }
    cleaned_articles = {
        int(row["id"]): row
        for row in load_jsonl(paths["cleaned"] or Path())
        if row.get("id") is not None
    }
    failures = load_jsonl(paths["failures"] or Path())
    manifest = read_json_if_exists(run_dir / "manifest.json")
    run_summary = read_json_if_exists(run_dir / "summary.json")
    env_values = read_dotenv(REPO_ROOT / ".env")

    ids = sorted(set(queries) | set(metrics) | set(scores) | set(raw_articles) | set(cleaned_articles))
    tasks: list[dict[str, Any]] = []
    for task_id in ids:
        query = queries.get(task_id, {})
        metric = metrics.get(task_id, {})
        score = scores.get(task_id, {})
        raw = raw_articles.get(task_id, {})
        cleaned = cleaned_articles.get(task_id, {})
        criterion = criteria.get(task_id, {})
        archive = metric.get("archive") or {}
        prompt = query.get("prompt") or metric.get("prompt") or score.get("prompt") or raw.get("prompt") or ""
        archived_article = read_text_if_exists(archive.get("report_md"))
        article = archived_article or cleaned.get("article") or raw.get("article") or ""
        references = metric.get("references") or {}
        reference_links = []
        for ref in references.get("details", []) or []:
            if isinstance(ref, dict):
                reference_links.append(
                    {
                        "identifier": ref.get("identifier") or "",
                        "title": ref.get("title") or "",
                        "source_family": ref.get("source_family") or "unknown",
                    }
                )
        if not reference_links:
            reference_links = archived_reference_links(archive)
        used_references = used_reference_metrics(article, reference_links)
        cost = metric.get("cost") or {}
        tool_usage = metric.get("tool_usage") or {}
        coordinator = metric.get("coordinator") or {}
        report = metric.get("report") or {}
        phase_durations = metric.get("phase_durations_seconds") or {}
        task = {
            "id": task_id,
            "title": display_title({"id": task_id, "prompt": prompt}),
            "prompt": prompt,
            "topic": query.get("topic") or "Uncategorized",
            "language": query.get("language") or metric.get("language") or "unknown",
            "status": metric.get("status") or ("scored" if score else "missing"),
            "session_id": metric.get("session_id"),
            "created_at": metric.get("created_at"),
            "completed_at": metric.get("completed_at"),
            "scores": {key: number(score.get(key)) for key in SCORE_KEYS},
            "dimension_weight": criterion.get("dimension_weight") or {},
            "criterions": criterion.get("criterions") or {},
            "runtime_seconds": number(metric.get("total_wall_clock_seconds")),
            "cost": number(cost.get("total_cost")),
            "total_tokens": int(number(cost.get("total_tokens"))),
            "prompt_tokens": int(number(cost.get("prompt_tokens"))),
            "completion_tokens": int(number(cost.get("completion_tokens"))),
            "llm_calls": int(number(cost.get("call_count"))),
            "pricing_model": cost.get("pricing_model") or cost.get("last_response_model") or "",
            "tool_attempts": int(number(tool_usage.get("total_attempts"))),
            "tool_duration_seconds": number(tool_usage.get("total_duration_seconds")),
            "tool_families": visible_families(tool_usage.get("by_source_family") or {}),
            "references_total": int(number(references.get("total"))),
            "references_by_family": visible_reference_families(references.get("by_source_family") or {}),
            "used_references_total": used_references["total"],
            "used_references_by_family": visible_reference_families(used_references["by_source_family"]),
            "coordinator_launches": int(number(coordinator.get("launch_count"))),
            "coordinator_waves": int(number(coordinator.get("wave_count"))),
            "word_count": int(number(report.get("word_count"))),
            "char_count": int(number(report.get("char_count"))),
            "phase_durations": phase_durations,
            "archive": archive,
            "reference_links": reference_links,
            "article": article,
            "article_preview": short_text(article, 360),
            "has_score": bool(score),
            "has_report": bool(article),
        }
        tasks.append(task)

    evaluator = manifest.get("evaluator") if isinstance(manifest.get("evaluator"), dict) else {}
    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "failure_count": len(failures),
        "run_name": manifest.get("run_id") or run_summary.get("run_id") or run_dir.name,
        "run_dir": str(run_dir),
        "system_name": manifest.get("system_name") or run_summary.get("system_name") or system_name,
        "profile": manifest.get("profile") or "",
        "variant_name": manifest.get("variant_name") or run_summary.get("variant_name") or "",
        "generator": manifest.get("generator") or run_summary.get("generator") or "",
        "direct_model": manifest.get("direct_model") or run_summary.get("direct_model") or "",
        "official_judge": bool(manifest.get("official_judge", run_summary.get("official_judge", False))),
        "evaluator": {
            "backend": evaluator.get("backend") or "",
            "model": compact_model_name(evaluator.get("model") or run_summary.get("judge_model") or ""),
            "request_profile": evaluator.get("request_profile") or run_summary.get("judge_request_profile") or "",
        },
        "agent_phase_models": agent_phase_models(env_values),
        "query_file": str(query_file),
        "criteria_file": str(criteria_file),
    }
    return tasks, metadata


def summarize(tasks: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    scored = [task for task in tasks if task["has_score"]]
    completed = [task for task in tasks if task.get("status") == "completed"]
    topics: dict[str, list[dict[str, Any]]] = {}
    languages: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        topics.setdefault(task["topic"], []).append(task)
        languages.setdefault(task["language"], []).append(task)

    def group_rows(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        rows = []
        for name, rows_tasks in sorted(groups.items()):
            score_values = [item["scores"]["overall_score"] for item in rows_tasks if item["has_score"]]
            rows.append(
                {
                    "name": name,
                    "count": len(rows_tasks),
                    "avg_overall": average(score_values),
                    "avg_runtime_seconds": average([item["runtime_seconds"] for item in rows_tasks]),
                    "avg_cost": average([item["cost"] for item in rows_tasks]),
                    "avg_references": average([float(item["references_total"]) for item in rows_tasks]),
                }
            )
        return rows

    score_lists = {key: [task["scores"][key] for task in scored] for key in SCORE_KEYS}
    runtime_values = [task["runtime_seconds"] for task in tasks if task["runtime_seconds"]]
    cost_values = [task["cost"] for task in tasks if task["cost"]]
    token_values = [float(task["total_tokens"]) for task in tasks if task["total_tokens"]]
    prompt_token_values = [float(task["prompt_tokens"]) for task in tasks if task["prompt_tokens"]]
    completion_token_values = [float(task["completion_tokens"]) for task in tasks if task["completion_tokens"]]
    return {
        "metadata": metadata,
        "counts": {
            "tasks": len(tasks),
            "scored": len(scored),
            "completed": len(completed),
            "failures": metadata.get("failure_count", 0),
        },
        "score_avgs": {key: average(values) for key, values in score_lists.items()},
        "score_medians": {key: statistics.median(values) if values else 0 for key, values in score_lists.items()},
        "runtime": {
            "avg": average(runtime_values),
            "median": statistics.median(runtime_values) if runtime_values else 0,
            "p95": percentile(runtime_values, 0.95),
        },
        "cost": {
            "total": sum(cost_values),
            "avg": average(cost_values),
            "p95": percentile(cost_values, 0.95),
        },
        "tokens": {
            "total": int(sum(token_values)),
            "prompt": int(sum(prompt_token_values)),
            "completion": int(sum(completion_token_values)),
            "avg": average(token_values),
        },
        "models": {
            "agent_phases": metadata.get("agent_phase_models", []),
            "evaluator": metadata.get("evaluator", {}),
        },
        "topics": sorted(group_rows(topics), key=lambda row: row["avg_overall"], reverse=True),
        "languages": group_rows(languages),
        "best": sorted(scored, key=lambda item: item["scores"]["overall_score"], reverse=True)[:8],
        "needs_attention": sorted(scored, key=lambda item: item["scores"]["overall_score"])[:8],
    }


def render_html(tasks: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    title = "SPADE Deep Research Benchmark"
    data = {"tasks": tasks, "summary": summary}
    storage_key = f"spade-dashboard:{summary.get('metadata', {}).get('run_name') or 'dashboard'}"
    logo_data_url = image_data_url(first_existing([LOGO_PATH, FALLBACK_LOGO_PATH]) or LOGO_PATH)
    favicon_href = image_data_url(first_existing([FAVICON_PATH, FALLBACK_LOGO_PATH]) or FAVICON_PATH) or FALLBACK_FAVICON
    header_logo = (
        f'<img class="brand-logo" src="{logo_data_url}" alt="" width="42" height="42">'
        if logo_data_url
        else ""
    )
    return render_template(
        STATIC_DASHBOARD_TEMPLATE_PATH,
        {
            "%%TITLE%%": escape(title),
            "%%FAVICON_HREF%%": favicon_href,
            "%%DASHBOARD_CSS%%": STATIC_DASHBOARD_CSS_PATH.read_text(encoding="utf-8"),
            "%%HEADER_LOGO%%": header_logo,
            "%%DASHBOARD_DATA%%": html_json(data),
            "%%SCORE_KEYS_JSON%%": json.dumps(SCORE_KEYS),
            "%%STORAGE_KEY_JSON%%": json.dumps(storage_key),
        },
    )


def build_dashboard(args: argparse.Namespace) -> Path:
    run_dir = args.run_dir.resolve()
    query_file = args.query_file.resolve()
    criteria_file = args.criteria_file.resolve()
    output = args.output.resolve() if args.output else run_dir / "dashboard.html"
    tasks, metadata = build_tasks(
        run_dir=run_dir,
        query_file=query_file,
        criteria_file=criteria_file,
        system_name=args.system_name,
    )
    if not tasks:
        raise ValueError(f"No tasks found for run directory: {run_dir}")
    summary = summarize(tasks, metadata)
    html = render_html(tasks, summary)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Benchmark run directory, e.g. bench/eval_runs/full100_final")
    parser.add_argument("--output", type=Path, default=None, help="Output HTML path. Defaults to RUN_DIR/dashboard.html")
    parser.add_argument("--system-name", default="spade_deep_research", help="System JSONL stem under raw_data/cleaned_data")
    parser.add_argument(
        "--query-file",
        type=Path,
        default=DEFAULT_BENCH_ROOT / "data" / "prompt_data" / "query.jsonl",
        help="Benchmark query JSONL with id/topic/language/prompt",
    )
    parser.add_argument(
        "--criteria-file",
        type=Path,
        default=DEFAULT_BENCH_ROOT / "data" / "criteria_data" / "criteria.jsonl",
        help="Benchmark criteria JSONL with rubric weights",
    )
    return parser.parse_args()


def main() -> int:
    output = build_dashboard(parse_args())
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
