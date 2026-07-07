"""Direct GPT benchmark generators used for ablation baselines."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from spade_llm.context import (
    ContextManager,
    create_assistant_tool_call_message,
    create_user_message,
)
from spade_llm.mcp.factory import get_mcp_server_tools
from spade_llm.providers import LLMProvider
from spade_llm.tools import LLMTool

from src.agents.specialized import resolve_citations
from src.budgets import SessionBudgetRegistry
from src.config import prompts
from src.config.mcp import get_arxiv_mcp_config
from src.config.mcp_adapters import wrap_arxiv_mcp_tools
from src.config.settings import settings
from src.config.tools import create_tavily_search_tool
from src.references import reference_registry
from src.session import Reference
from src.telemetry import SessionArchiveStore, telemetry_registry
from src.text_normalization import normalize_report_text
from src.utils.cost_tracker import cost_tracker


_DIRECT_RUNNER_LOOP: Optional[asyncio.AbstractEventLoop] = None


DIRECT_TOOLS_SYSTEM_PROMPT = """You are ChatGPT with access to external research tools.

Use Tavily for current web, policy, market, company, government, and general factual evidence.
Use ArXiv only for academic papers, scientific methods, algorithms, and technical research literature.

Use available tools as needed, then answer the user's request directly. Prefer authoritative sources,
include citations or source attributions where possible, and be clear about uncertainty or evidence
limits. Do not ask for more access or present a workplan instead of the answer.
"""

DIRECT_TOOLS_FINAL_ANSWER_NOTICE = (
    "Stop using tools and answer the user's request directly using only the evidence already gathered."
)

DIRECT_MAX_TOOL_ITERATIONS = 20
DEFAULT_DIRECT_TOOLS_MAX_TAVILY_CALLS = 10


def _default_cost() -> Dict[str, Any]:
    return {
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


def _cost_to_dict(session_id: str) -> Dict[str, Any]:
    cost = cost_tracker.get_session_cost(session_id)
    if not cost:
        return _default_cost()
    return {
        "total_cost": cost.total_cost,
        "prompt_tokens": cost.prompt_tokens,
        "completion_tokens": cost.completion_tokens,
        "total_tokens": cost.total_tokens,
        "cache_creation_input_tokens": cost.cache_creation_input_tokens,
        "cache_read_input_tokens": cost.cache_read_input_tokens,
        "call_count": cost.call_count,
        "pricing_model": cost.pricing_model,
        "last_response_model": cost.last_response_model,
        "cost_source": cost.cost_source,
    }


def _set_tool_session_id(tools: List[LLMTool], session_id: str) -> None:
    for tool in tools:
        set_session_id = getattr(tool, "set_session_id", None)
        if callable(set_session_id):
            set_session_id(session_id)


class DirectBenchmarkGenerator:
    """Generate benchmark reports without the SPADE multi-agent workflow."""

    def __init__(self, *, model: str, archive_root: Optional[Path] = None):
        self.model = model
        self.provider = LLMProvider(model=model, timeout=settings.WRITER_TIMEOUT)
        self.archive_store = SessionArchiveStore(root=archive_root)
        cost_tracker.register()

    async def run_task(self, task: Dict[str, Any], *, use_tools: bool) -> Dict[str, Any]:
        session_id = f"direct-{task['id']}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        started_at = datetime.now()
        telemetry_registry.initialize_session(
            session_id=session_id,
            query=task["prompt"],
            status="created",
            created_at=started_at,
        )
        try:
            if use_tools:
                report = await self._run_with_tools(task, session_id)
            else:
                report = await self._run_no_tools(task, session_id)

            report = normalize_report_text(report)
            pending_refs = reference_registry.collect(session_id)
            references = [
                Reference(identifier=ref.identifier, source_type=ref.source_type, title=ref.title)
                for ref in pending_refs
            ]
            if references:
                report = normalize_report_text(resolve_citations(report, references))

            telemetry_registry.record_report(session_id, report)
            telemetry_registry.record_state_transition(session_id, "writing", "completed", task["prompt"])
            telemetry_registry.record_terminal_state(
                session_id,
                status="completed",
                details={"completed_at": datetime.now().isoformat()},
            )
            snapshot = self._snapshot(session_id)
            snapshot["task_id"] = int(task["id"])
            snapshot["prompt"] = task["prompt"]
            snapshot["language"] = task.get("language")
            snapshot["session_id"] = session_id
            return {
                "completed": True,
                "session_id": session_id,
                "article": report,
                "metrics": snapshot,
                "failure": None,
            }
        except Exception as exc:
            telemetry_registry.record_terminal_state(
                session_id,
                status="failed",
                details={"failed_at": datetime.now().isoformat(), "error": str(exc)},
            )
            snapshot = self._snapshot(session_id)
            snapshot["task_id"] = int(task["id"])
            snapshot["prompt"] = task["prompt"]
            snapshot["language"] = task.get("language")
            snapshot["session_id"] = session_id
            return {
                "completed": False,
                "session_id": session_id,
                "article": "",
                "metrics": snapshot,
                "failure": str(exc),
            }
        finally:
            SessionBudgetRegistry.clear_session(session_id)
            reference_registry.clear(session_id)
            cost_tracker.clear_session(session_id)
            telemetry_registry.clear_session_memory(session_id)

    async def _run_no_tools(self, task: Dict[str, Any], session_id: str) -> str:
        telemetry_registry.record_state_transition(session_id, "created", "writing", task["prompt"])
        context = ContextManager(system_prompt=prompts.DIRECT_NO_TOOLS_WRITER_PROMPT)
        user_prompt = self._no_tools_prompt(task)
        context.add_message_dict(create_user_message(user_prompt), session_id)
        response = await self.provider.get_llm_response(context, tools=None, conversation_id=session_id)
        return response.get("text") or ""

    async def _run_with_tools(self, task: Dict[str, Any], session_id: str) -> str:
        telemetry_registry.record_state_transition(session_id, "created", "researching", task["prompt"])
        tools = await self._create_direct_tools(session_id)
        telemetry_registry.record_state_transition(session_id, "researching", "writing", task["prompt"])
        context = ContextManager(system_prompt=DIRECT_TOOLS_SYSTEM_PROMPT)
        user_prompt = self._tools_prompt(task)
        context.add_message_dict(create_user_message(user_prompt), session_id)

        for iteration in range(1, DIRECT_MAX_TOOL_ITERATIONS + 1):
            response = await self.provider.get_llm_response(context, tools=tools, conversation_id=session_id)
            tool_calls = response.get("tool_calls", [])
            text_response = response.get("text")
            if not tool_calls:
                return text_response or ""

            context.add_message_dict(create_assistant_tool_call_message(tool_calls), session_id)
            for tool_call in tool_calls:
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("arguments", {})
                tool_id = tool_call.get("id", f"call_{tool_name}_{iteration}")
                tool = next((candidate for candidate in tools if candidate.name == tool_name), None)
                if tool is None:
                    context.add_tool_result(tool_name or "unknown_tool", {"error": "Tool not found"}, tool_id, session_id)
                    continue
                try:
                    result = await tool.execute(**tool_args)
                except Exception as exc:
                    result = {"error": str(exc)}
                context.add_tool_result(tool.name, result, tool_id, session_id)

        context.add_message_dict(create_user_message(DIRECT_TOOLS_FINAL_ANSWER_NOTICE), session_id)
        response = await self.provider.get_llm_response(context, tools=None, conversation_id=session_id)
        return response.get("text") or ""

    async def _create_direct_tools(self, session_id: str) -> List[LLMTool]:
        max_tavily_calls = int(
            os.environ.get("DIRECT_TOOLS_MAX_TAVILY_CALLS", DEFAULT_DIRECT_TOOLS_MAX_TAVILY_CALLS)
        )
        tavily_tool = create_tavily_search_tool(
            owner_id="direct_gpt5",
            max_calls_per_agent=None,
            max_calls_per_session=max_tavily_calls,
        )
        arxiv_tools = await get_mcp_server_tools(get_arxiv_mcp_config())
        tools = [tavily_tool] + wrap_arxiv_mcp_tools(arxiv_tools)
        _set_tool_session_id(tools, session_id)
        return tools

    def _no_tools_prompt(self, task: Dict[str, Any]) -> str:
        parts = [str(task["prompt"])]
        if task.get("language") and task.get("language") != "en":
            parts.append("\n\nPlease answer in Chinese.")
        return "".join(parts)

    def _tools_prompt(self, task: Dict[str, Any]) -> str:
        parts = [str(task["prompt"])]
        if task.get("language") and task.get("language") != "en":
            parts.append("\n\nPlease answer in Chinese.")
        return "".join(parts)

    def _snapshot(self, session_id: str) -> Dict[str, Any]:
        archive_dir = self.archive_store.session_dir(session_id)
        snapshot_path = archive_dir / "session.json"
        if snapshot_path.exists():
            return json.loads(snapshot_path.read_text(encoding="utf-8"))

        return {
            "session_id": session_id,
            "query": "",
            "status": "failed",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "completed_at": datetime.now().isoformat(),
            "total_wall_clock_seconds": 0.0,
            "phase_durations_seconds": {},
            "cost": _cost_to_dict(session_id),
            "tool_usage": {"total_attempts": 0, "total_duration_seconds": 0.0, "by_source_family": {}},
            "references": {"total": 0, "by_source_family": {}},
            "coordinator": {"launch_count": 0, "wave_count": 0},
            "report": {"has_report": False, "char_count": 0, "word_count": 0},
            "archive": {
                "session_dir": str(archive_dir),
                "session_json": str(snapshot_path),
                "events_jsonl": str(archive_dir / "events.jsonl"),
                "report_md": str(archive_dir / "report.md"),
            },
        }


def run_direct_task(task: Dict[str, Any], *, model: str, use_tools: bool) -> Dict[str, Any]:
    global _DIRECT_RUNNER_LOOP
    generator = DirectBenchmarkGenerator(model=model)
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None:
        raise RuntimeError("run_direct_task cannot be called from an active event loop")

    if _DIRECT_RUNNER_LOOP is None or _DIRECT_RUNNER_LOOP.is_closed():
        _DIRECT_RUNNER_LOOP = asyncio.new_event_loop()
    return _DIRECT_RUNNER_LOOP.run_until_complete(generator.run_task(task, use_tools=use_tools))
