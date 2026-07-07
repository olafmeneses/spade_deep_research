"""Run the DRB ablation matrix with consistent benchmark settings."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.utils.otel import with_otel_flush_defaults

DEFAULT_OUTPUT_DIR = REPO_ROOT / "bench" / "eval_runs"


def _with_otel_flush_defaults(env: Dict[str, str]) -> Dict[str, str]:
    return dict(with_otel_flush_defaults(env))


@dataclass(frozen=True)
class Variant:
    name: str
    generator: str
    system_name: str
    env: Dict[str, str] = field(default_factory=dict)
    needs_api: bool = False
    needs_arxiv_mcp: bool = False


VARIANTS: Dict[str, Variant] = {
    "gpt5_no_tools": Variant(
        name="gpt5_no_tools",
        generator="direct_no_tools",
        system_name="gpt5_no_tools",
    ),
    "gpt5_tools": Variant(
        name="gpt5_tools",
        generator="direct_tools",
        system_name="gpt5_tools",
        needs_arxiv_mcp=True,
    ),
    "spade_summarized_no_loop": Variant(
        name="spade_summarized_no_loop",
        generator="spade_api",
        system_name="spade_summarized_no_loop",
        env={
            "MAX_CRITIC_ITERATIONS": "0",
            "COORDINATOR_RETURN_RAW_FINDINGS": "false",
        },
        needs_api=True,
        needs_arxiv_mcp=True,
    ),
    "spade_direct_findings_no_loop": Variant(
        name="spade_direct_findings_no_loop",
        generator="spade_api",
        system_name="spade_direct_findings_no_loop",
        env={
            "MAX_CRITIC_ITERATIONS": "0",
            "COORDINATOR_RETURN_RAW_FINDINGS": "true",
        },
        needs_api=True,
        needs_arxiv_mcp=True,
    ),
    "spade_direct_findings_loop": Variant(
        name="spade_direct_findings_loop",
        generator="spade_api",
        system_name="spade_direct_findings_loop",
        env={
            "MAX_CRITIC_ITERATIONS": "1",
            "COORDINATOR_RETURN_RAW_FINDINGS": "true",
        },
        needs_api=True,
        needs_arxiv_mcp=True,
    ),
}


DEFAULT_VARIANTS = [
    "gpt5_no_tools",
    "gpt5_tools",
    "spade_summarized_no_loop",
    "spade_direct_findings_no_loop",
]


def _find_free_port(start_port: int, used_ports: set[int]) -> int:
    port = start_port
    while True:
        if port in used_ports:
            port += 1
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                port += 1
                continue
        used_ports.add(port)
        return port


def _wait_ready(url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/ready", timeout=5) as response:
                data = response.read().decode("utf-8")
                if '"ready":true' in data.replace(" ", "").lower():
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"API did not become ready at {url}: {last_error}")


def _check_arxiv_mcp(timeout_seconds: int) -> None:
    env = _with_otel_flush_defaults(os.environ.copy())
    cmd = [
        sys.executable,
        "-c",
        "\n".join(
            [
                "import asyncio",
                "from src.config.mcp import get_arxiv_mcp_config",
                "from spade_llm.mcp.factory import get_mcp_server_tools",
                "async def main():",
                "    tools = await get_mcp_server_tools(get_arxiv_mcp_config())",
                "    print(len(tools))",
                "asyncio.run(main())",
            ]
        ),
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True, timeout=timeout_seconds)


def _start_api(variant: Variant, port: int, log_path: Path) -> subprocess.Popen:
    env = _with_otel_flush_defaults(os.environ.copy())
    env.update(variant.env)
    env["UV_CACHE_DIR"] = env.get("UV_CACHE_DIR", "/tmp/uv-cache")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    cmd = [
        "uv",
        "run",
        "uvicorn",
        "src.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    return subprocess.Popen(cmd, cwd=REPO_ROOT, env=env, stdout=log_handle, stderr=subprocess.STDOUT)


def _run_benchmark(
    *,
    variant: Variant,
    profile: str,
    run_id: str,
    output_dir: Path,
    judge_model: str,
    judge_request_profile: str,
    api_base_url: Optional[str],
    direct_model: Optional[str],
    timeout_seconds: int,
    no_incremental_scoring: bool,
) -> None:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_race_benchmark.py"),
        "--profile",
        profile,
        "--run-id",
        run_id,
        "--output-dir",
        str(output_dir),
        "--generator",
        variant.generator,
        "--variant-name",
        variant.name,
        "--system-name",
        variant.system_name,
        "--judge-model",
        judge_model,
        "--judge-request-profile",
        judge_request_profile,
        "--timeout",
        str(timeout_seconds),
    ]
    if api_base_url:
        cmd.extend(["--api-base-url", api_base_url])
    if direct_model:
        cmd.extend(["--direct-model", direct_model])
    if no_incremental_scoring:
        cmd.append("--no-incremental-scoring")

    env = _with_otel_flush_defaults(os.environ.copy())
    env.update(variant.env)
    subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True)


def _parse_variants(raw: str) -> List[Variant]:
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [name for name in names if name not in VARIANTS]
    if unknown:
        raise ValueError(f"Unknown variants: {unknown}. Available: {sorted(VARIANTS)}")
    return [VARIANTS[name] for name in names]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DRB ablation variants.")
    parser.add_argument("--profile", default="pilot10_mix", choices=["pilot1_en", "pilot10_mix", "full100"])
    parser.add_argument("--run-id-prefix", default="ablation_pilot10")
    parser.add_argument("--variants", default=",".join(DEFAULT_VARIANTS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--judge-model", default=os.environ.get("BENCHMARK_JUDGE_MODEL", "gpt-5"))
    parser.add_argument(
        "--judge-request-profile",
        default=os.environ.get("BENCHMARK_JUDGE_REQUEST_PROFILE", "compat_openai"),
        choices=["compat_openai", "reasoning_openai"],
    )
    parser.add_argument("--direct-model", default=os.environ.get("LLM_WRITER", "openai/gpt-5"))
    parser.add_argument("--api-base-url", help="Use an already-running API for spade_api variants instead of launching one")
    parser.add_argument("--start-port", type=int, default=18080)
    parser.add_argument("--api-ready-timeout", type=int, default=240)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--skip-arxiv-mcp-check", action="store_true")
    parser.add_argument("--no-incremental-scoring", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    variants = _parse_variants(args.variants)
    output_dir = Path(args.output_dir)
    used_ports: set[int] = set()

    needs_arxiv = any(variant.needs_arxiv_mcp for variant in variants)
    if needs_arxiv and not args.skip_arxiv_mcp_check and not args.dry_run:
        _check_arxiv_mcp(args.api_ready_timeout)

    for variant in variants:
        run_id = f"{args.run_id_prefix}_{variant.name}"
        manage_api = variant.needs_api and not args.api_base_url
        if manage_api and args.dry_run:
            port = args.start_port + len(used_ports)
            used_ports.add(port)
        else:
            port = _find_free_port(args.start_port, used_ports) if manage_api else None
        api_base_url = f"http://127.0.0.1:{port}" if port else (args.api_base_url if variant.needs_api else None)
        print(f"{variant.name}: run_id={run_id} generator={variant.generator} api={api_base_url or '-'}")
        if args.dry_run:
            continue

        api_process = None
        try:
            if manage_api:
                assert port is not None and api_base_url is not None
                api_process = _start_api(variant, port, output_dir / run_id / "api.log")
                _wait_ready(api_base_url, args.api_ready_timeout)

            _run_benchmark(
                variant=variant,
                profile=args.profile,
                run_id=run_id,
                output_dir=output_dir,
                judge_model=args.judge_model,
                judge_request_profile=args.judge_request_profile,
                api_base_url=api_base_url,
                direct_model=args.direct_model,
                timeout_seconds=args.timeout,
                no_incremental_scoring=args.no_incremental_scoring,
            )
        finally:
            if api_process is not None and api_process.poll() is None:
                api_process.terminate()
                try:
                    api_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    api_process.kill()
                    api_process.wait(timeout=30)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
