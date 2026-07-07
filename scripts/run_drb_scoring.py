"""Local wrapper around DeepResearch Bench RACE scoring.

This preserves the upstream CLI while allowing a distinct cleaner LLM via
benchmark-scoped environment variables.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from threading import Lock
from typing import Optional

import requests
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from src.utils.otel import force_flush_otel

BENCH_REPO = REPO_ROOT.parent / "deep_research_bench"
if str(BENCH_REPO) not in sys.path:
    sys.path.insert(0, str(BENCH_REPO))
os.chdir(BENCH_REPO)

drb_race = importlib.import_module("deepresearch_bench_race")
drb_api = importlib.import_module("utils.api")


class OverrideAIClient(drb_api.AIClient):
    """OpenAI-compatible client with per-instance backend/model configuration."""

    def __init__(
        self,
        *,
        backend: str,
        api_key: str,
        model: str,
        base_url: str,
        request_profile: str,
    ) -> None:
        if not api_key:
            raise ValueError("Cleaner API key not provided")
        self.backend = backend
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.request_profile = request_profile

    def _headers(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.backend == "openrouter":
            headers["HTTP-Referer"] = os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/Ayanami0730/deep_research_bench"
            )
            headers["X-Title"] = os.environ.get("OPENROUTER_TITLE", "DRB-GPT5")
        return headers

    def _post(self, payload):
        url = f"{self.base_url}/chat/completions"
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=drb_api.HTTP_TIMEOUT_S)
        if resp.status_code != 200:
            raise Exception(f"{self.backend} chat/completions {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return data


def _get_clean_client(default_client: drb_api.AIClient):
    clean_model = os.environ.get("BENCHMARK_CLEAN_MODEL", "").strip()
    clean_base_url = os.environ.get("BENCHMARK_CLEAN_BASE_URL", "").strip()
    clean_api_key = os.environ.get("BENCHMARK_CLEAN_API_KEY", "").strip()
    clean_profile = os.environ.get("BENCHMARK_CLEAN_REQUEST_PROFILE", "").strip().lower()
    clean_backend = os.environ.get("BENCHMARK_CLEAN_BACKEND", "").strip().lower()

    if not any([clean_model, clean_base_url, clean_api_key, clean_profile, clean_backend]):
        return default_client

    backend = clean_backend or drb_api.LLM_BACKEND
    if backend not in drb_api._BACKEND_DEFAULTS:
        raise ValueError(f"Unknown BENCHMARK_CLEAN_BACKEND={backend!r}")

    backend_defaults = drb_api._BACKEND_DEFAULTS[backend]
    key_env = backend_defaults["key_env"]
    if backend == "openrouter":
        default_base_url = os.environ.get("OPENROUTER_BASE_URL", backend_defaults["base_url"])
    else:
        default_base_url = os.environ.get("OPENAI_BASE_URL", backend_defaults["base_url"])

    api_key = clean_api_key or os.environ.get(key_env, "")
    model = clean_model or default_client.model
    base_url = clean_base_url or default_base_url
    request_profile = clean_profile or default_client.request_profile

    return OverrideAIClient(
        backend=backend,
        api_key=api_key,
        model=model,
        base_url=base_url,
        request_profile=request_profile,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score model articles against reference articles using detailed evaluation criteria and LLM."
    )
    parser.add_argument("target_model", type=str, help="Name of target model to evaluate")
    parser.add_argument("--limit", type=int, default=None, help="Limit on number of prompts to process (for testing).")
    parser.add_argument("--skip_cleaning", action="store_true", help="Skip article cleaning step.")
    parser.add_argument("--only_zh", action="store_true", help="Only process Chinese data.")
    parser.add_argument("--only_en", action="store_true", help="Only process English data.")
    parser.add_argument("--force", action="store_true", help="Force re-evaluation even if results exist.")
    parser.add_argument("--raw_data_dir", type=str, default="data/test_data/raw_data", help="Directory containing raw data.")
    parser.add_argument("--cleaned_data_dir", type=str, default="data/test_data/cleaned_data", help="Directory for cleaned data.")
    parser.add_argument("--max_workers", type=int, default=5, help="Maximum number of worker threads.")
    parser.add_argument("--query_file", type=str, default="data/prompt_data/query.jsonl", help="Path to query file with language information.")
    parser.add_argument("--output_dir", type=str, default="results", help="Directory for output results.")
    return parser


def _build_filtered_query_file(query_file: str, existing_ids: set[int]) -> str:
    """Write a temporary query file containing only tasks that still need scoring."""
    if not existing_ids:
        return query_file

    all_tasks = drb_race.load_jsonl(query_file)
    pending_tasks = [task for task in all_tasks if task.get("id") not in existing_ids]

    temp_dir = tempfile.mkdtemp(prefix="drb_query_filter_")
    filtered_query_path = Path(temp_dir) / Path(query_file).name
    with filtered_query_path.open("w", encoding="utf-8") as handle:
        for task in pending_tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    return str(filtered_query_path)


def _install_incremental_result_writer(output_file: str, existing_results: list[dict]) -> None:
    """Persist each successfully scored item as soon as DRB returns it."""
    original_process_single_item = drb_race.process_single_item
    write_lock = Lock()
    results_by_id = {
        result.get("id"): result
        for result in existing_results
        if result.get("id") is not None and "error" not in result
    }

    def write_results_locked() -> None:
        ordered_results = sorted(results_by_id.values(), key=lambda item: item.get("id", float("inf")))
        temp_path = f"{output_file}.tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            for result in ordered_results:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        os.replace(temp_path, output_file)

    def process_single_item_with_persistence(*args, **kwargs):
        result = original_process_single_item(*args, **kwargs)
        if result and "error" not in result and result.get("id") is not None:
            with write_lock:
                results_by_id[result["id"]] = result
                write_results_locked()
        return result

    drb_race.process_single_item = process_single_item_with_persistence


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    target_model = args.target_model
    limit = args.limit
    skip_cleaning = args.skip_cleaning
    only_zh = args.only_zh
    only_en = args.only_en
    force = args.force
    raw_data_dir = args.raw_data_dir
    cleaned_data_dir = args.cleaned_data_dir
    max_workers = args.max_workers
    query_file = args.query_file
    output_dir = args.output_dir

    os.makedirs(output_dir, exist_ok=True)
    os.environ["DRB_COST_CALLS_PATH"] = os.path.join(output_dir, "judge_cost_calls.jsonl")
    os.environ["DRB_COST_SUMMARY_PATH"] = os.path.join(output_dir, "judge_cost.json")

    output_file = os.path.join(output_dir, "raw_results.jsonl")
    result_file = os.path.join(output_dir, "race_result.txt")
    existing_results = []
    existing_ids = set()

    if os.path.exists(output_file) and not force:
        try:
            existing_results = drb_race.load_jsonl(output_file)
            existing_ids = {r.get("id") for r in existing_results if r.get("id")}
            drb_race.logger.info(f"Found existing results file with {len(existing_results)} entries")
            if limit is not None and len(existing_results) >= limit:
                drb_race.logger.info(
                    f"Existing results ({len(existing_results)}) meet or exceed limit ({limit}). Skipping evaluation."
                )
                successful_results = [r for r in existing_results if "error" not in r]
                if successful_results:
                    comprehensiveness_avg = sum(r.get("comprehensiveness", 0) for r in successful_results) / len(successful_results)
                    insight_avg = sum(r.get("insight", 0) for r in successful_results) / len(successful_results)
                    instruction_following_avg = sum(r.get("instruction_following", 0) for r in successful_results) / len(successful_results)
                    readability_avg = sum(r.get("readability", 0) for r in successful_results) / len(successful_results)
                    overall_avg = sum(r.get("overall_score", 0) for r in successful_results) / len(successful_results)
                    drb_race.logger.info("\n=== Existing Evaluation Results Summary ===")
                    drb_race.logger.info(f"Comprehensiveness:      {comprehensiveness_avg:.4f}")
                    drb_race.logger.info(f"Insight:                {insight_avg:.4f}")
                    drb_race.logger.info(f"Instruction Following:  {instruction_following_avg:.4f}")
                    drb_race.logger.info(f"Readability:            {readability_avg:.4f}")
                    drb_race.logger.info(f"Overall Score:          {overall_avg:.4f}")
                    drb_race.logger.info("================================")
                return 0
        except Exception as exc:
            drb_race.logger.warning(f"Error reading existing results file: {exc}. Will create new results.")
            existing_results = []
            existing_ids = set()

    _install_incremental_result_writer(output_file, existing_results)

    effective_query_file = _build_filtered_query_file(query_file, existing_ids)

    llm_client = drb_api.AIClient()
    clean_agent = _get_clean_client(llm_client)
    if clean_agent is llm_client:
        drb_race.logger.info(f"Cleaner model shares scoring model: {llm_client.model}")
    else:
        drb_race.logger.info(f"Cleaner model override active: {clean_agent.model}")

    all_results = list(existing_results)
    all_tasks = drb_race.load_jsonl(effective_query_file)
    if existing_ids:
        drb_race.logger.info(f"Will skip {len(existing_ids)} already processed task IDs")

    if not only_en:
        drb_race.logger.info("Starting Chinese data processing...")
        if not skip_cleaning:
            zh_tasks = [task for task in all_tasks if task.get("language") == "zh" and task.get("id") not in existing_ids]
            if not zh_tasks:
                drb_race.logger.info("All Chinese tasks have been processed already. Skipping.")
            elif limit is not None:
                existing_zh_count = len(
                    [
                        r for r in existing_results
                        if r.get("prompt", "").strip()
                        and any(t.get("prompt") == r.get("prompt") and t.get("language") == "zh" for t in all_tasks)
                    ]
                )
                remaining_limit = max(0, limit - existing_zh_count)
                if remaining_limit > 0:
                    drb_race.logger.info(
                        f"Processing up to {remaining_limit} more Chinese tasks (limit: {limit}, already processed: {existing_zh_count})"
                    )
                    zh_results = drb_race.process_language_data(
                        "zh", target_model, llm_client, clean_agent,
                        raw_data_dir, cleaned_data_dir, max_workers, remaining_limit, effective_query_file
                    )
                    if zh_results:
                        all_results.extend(zh_results)
                else:
                    drb_race.logger.info(f"Already reached limit for Chinese tasks ({existing_zh_count}/{limit}). Skipping.")
            else:
                zh_results = drb_race.process_language_data(
                    "zh", target_model, llm_client, clean_agent,
                    raw_data_dir, cleaned_data_dir, max_workers, limit, effective_query_file
                )
                if zh_results:
                    all_results.extend(zh_results)
        else:
            drb_race.logger.info("Skipping article cleaning step for Chinese data.")

    if not only_zh:
        drb_race.logger.info("Starting English data processing...")
        if not skip_cleaning:
            en_tasks = [task for task in all_tasks if task.get("language") == "en" and task.get("id") not in existing_ids]
            if not en_tasks:
                drb_race.logger.info("All English tasks have been processed already. Skipping.")
            elif limit is not None:
                existing_en_count = len(
                    [
                        r for r in existing_results
                        if r.get("prompt", "").strip()
                        and any(t.get("prompt") == r.get("prompt") and t.get("language") == "en" for t in all_tasks)
                    ]
                )
                remaining_limit = max(0, limit - existing_en_count)
                if remaining_limit > 0:
                    drb_race.logger.info(
                        f"Processing up to {remaining_limit} more English tasks (limit: {limit}, already processed: {existing_en_count})"
                    )
                    en_results = drb_race.process_language_data(
                        "en", target_model, llm_client, clean_agent,
                        raw_data_dir, cleaned_data_dir, max_workers, remaining_limit, effective_query_file
                    )
                    if en_results:
                        all_results.extend(en_results)
                else:
                    drb_race.logger.info(f"Already reached limit for English tasks ({existing_en_count}/{limit}). Skipping.")
            else:
                en_results = drb_race.process_language_data(
                    "en", target_model, llm_client, clean_agent,
                    raw_data_dir, cleaned_data_dir, max_workers, limit, effective_query_file
                )
                if en_results:
                    all_results.extend(en_results)
        else:
            drb_race.logger.info("Skipping article cleaning step for English data.")

    if all_results:
        all_results.sort(key=lambda x: x.get("id", float("inf")))
        drb_race.logger.info(f"Saving {len(all_results)} results to {output_file}...")
        with open(output_file, "w", encoding="utf-8") as handle:
            for result in all_results:
                handle.write(drb_race.json.dumps(result, ensure_ascii=False) + "\n")
        drb_race.logger.info("Results saved successfully.")

        successful_results = [r for r in all_results if "error" not in r]
        if successful_results:
            comprehensiveness_avg = sum(r.get("comprehensiveness", 0) for r in successful_results) / len(successful_results)
            insight_avg = sum(r.get("insight", 0) for r in successful_results) / len(successful_results)
            instruction_following_avg = sum(r.get("instruction_following", 0) for r in successful_results) / len(successful_results)
            readability_avg = sum(r.get("readability", 0) for r in successful_results) / len(successful_results)
            overall_avg = sum(r.get("overall_score", 0) for r in successful_results) / len(successful_results)

            drb_race.logger.info("\n=== Evaluation Results Summary ===")
            drb_race.logger.info(f"Comprehensiveness:      {comprehensiveness_avg:.4f}")
            drb_race.logger.info(f"Insight:                {insight_avg:.4f}")
            drb_race.logger.info(f"Instruction Following:  {instruction_following_avg:.4f}")
            drb_race.logger.info(f"Readability:            {readability_avg:.4f}")
            drb_race.logger.info(f"Overall Score:          {overall_avg:.4f}")
            drb_race.logger.info("================================")

            with open(result_file, "w", encoding="utf-8") as handle:
                handle.write(f"Comprehensiveness: {comprehensiveness_avg:.4f}\n")
                handle.write(f"Insight: {insight_avg:.4f}\n")
                handle.write(f"Instruction Following: {instruction_following_avg:.4f}\n")
                handle.write(f"Readability: {readability_avg:.4f}\n")
                handle.write(f"Overall Score: {overall_avg:.4f}\n")
    else:
        drb_race.logger.warning("No results to save.")

    drb_race.logger.info("\n--- Run Summary ---")
    drb_race.logger.info(f"Target model: {target_model}")
    drb_race.logger.info(f"Total tasks processed: {len(all_results)}")
    drb_race.logger.info(f"Results file: {output_file}")
    drb_race.logger.info("-------------------")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        force_flush_otel()
