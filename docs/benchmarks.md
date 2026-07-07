# Benchmarks

This repository includes benchmark tooling for DeepResearch Bench / RACE
experiments. The benchmark runner is separate from the normal SPADE Deep
Research API quickstart.

## What Is Included

- `scripts/run_race_benchmark.py`: CLI entrypoint for benchmark runs.
- `scripts/run_ablation_matrix.py`: helper for running multiple generator
  variants with isolated API processes.
- `scripts/run_drb_scoring.py`: scoring helper that uses benchmark-specific
  model settings.
- `scripts/build_static_dashboard.py`: experimental static dashboard generator.
- `bench/benchmark_runner.py` and `bench/direct_runner.py`: benchmark
  orchestration, metrics aggregation, and direct-baseline generators.
- `src/telemetry.py`: session telemetry and archive helpers used by both the
  product runtime and benchmark runs.

Public result artifacts can be stored under `bench/results/`. Local run outputs
under `bench/eval_runs/` and `bench/session_archive/` remain ignored so that raw
logs, caches, and exploratory runs do not accidentally enter a release.

## Required Repositories

Benchmark runs expect the upstream DeepResearch Bench repository to be cloned as
a sibling directory next to this repository:

```text
../spade_deep_research
../deep_research_bench
```

The benchmark runner and scoring wrapper use `../deep_research_bench` by default for:

- RACE task inputs at `data/prompt_data/query.jsonl`
- the upstream `deepresearch_bench_race` scoring module

Clone it before running benchmark commands:

```bash
git clone https://github.com/Ayanami0730/deep_research_bench ../deep_research_bench
```

## Environment

The SPADE API and evaluator can share one `.env`, but keep runtime model settings
separate from benchmark judging settings when you want different providers or
models.

SPADE runtime keys:

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
TAVILY_API_KEY=
LLM_DEFAULT=openai/gpt-5-nano
LLM_COORDINATOR=
LLM_CRITIC=
LLM_WRITER=
LLM_PLANNER=
```

Benchmark-only keys:

```dotenv
BENCHMARK_OPENAI_API_KEY=
BENCHMARK_OPENAI_BASE_URL=
BENCHMARK_JUDGE_MODEL=gpt-5
BENCHMARK_JUDGE_REQUEST_PROFILE=compat_openai
BENCHMARK_CLEAN_MODEL=
BENCHMARK_CLEAN_BASE_URL=
BENCHMARK_CLEAN_API_KEY=
BENCHMARK_CLEAN_REQUEST_PROFILE=
BENCHMARK_CLEAN_BACKEND=
```

Notes:

- `BENCHMARK_OPENAI_API_KEY` and `BENCHMARK_OPENAI_BASE_URL` are used only by
  benchmark scoring.
- `BENCHMARK_JUDGE_MODEL` and `BENCHMARK_JUDGE_REQUEST_PROFILE` are defaults for
  `scripts/run_race_benchmark.py`.
- Cleaner settings are optional. If unset, cleaning reuses scorer settings.

## Services

Start the arXiv MCP service before running SPADE benchmark variants. The command
depends on how the MCP service is installed. The SPADE API expects it at:

```dotenv
ARXIV_MCP_URL=http://localhost:8000/mcp
```

For variants that call the API directly, run:

```bash
uv run uvicorn src.api:app --reload --port 8080
```

The ablation matrix runner can also launch temporary API servers for SPADE
variants on isolated ports.

## Run Benchmarks

Run a one-sample pilot:

```bash
uv run python scripts/run_race_benchmark.py --profile pilot1_en
```

Run a 10-task pilot:

```bash
uv run python scripts/run_race_benchmark.py --profile pilot10_mix
```

Run the full 100-task benchmark:

```bash
uv run python scripts/run_race_benchmark.py --profile full100
```

Optional scoring overrides:

```bash
uv run python scripts/run_race_benchmark.py \
  --profile pilot1_en \
  --judge-model gpt-5 \
  --judge-request-profile compat_openai
```

Resume scoring for an existing run without rerunning SPADE research tasks:

```bash
uv run python scripts/run_race_benchmark.py \
  --profile pilot10_mix \
  --run-id YOUR_EXISTING_RUN_ID \
  --score-only
```

## Ablation Matrix

Run the 10-task ablation matrix:

```bash
uv run python scripts/run_ablation_matrix.py \
  --profile pilot10_mix \
  --run-id-prefix ablation_pilot10 \
  --judge-model gpt-5 \
  --variants gpt5_no_tools,gpt5_tools,spade_summarized_no_loop,spade_direct_findings_no_loop
```

`gpt5_tools` is capped at 10 Tavily calls per task by default. Override only that
direct baseline with `DIRECT_TOOLS_MAX_TAVILY_CALLS` if needed.

The already-completed final feedback-loop variant can be rerun explicitly by
adding `spade_direct_findings_loop` to `--variants`.

## Outputs

Default local run outputs:

- `bench/eval_runs/<run_id>/raw_data/`
- `bench/eval_runs/<run_id>/cleaned_data/`
- `bench/eval_runs/<run_id>/drb_results/`
- `bench/session_archive/<session_id>/`

Scoring persists incrementally during a benchmark run. After each completed task,
the DRB scorer updates `drb_results/raw_results.jsonl`, and aggregate files are
recalculated at the end.

## Dashboard

`scripts/build_static_dashboard.py` is an experimental/internal artifact
generator. It currently emits a self-contained HTML file and keeps embedded
HTML/CSS/JavaScript in Python for portability.

Generate a dashboard for a run:

```bash
uv run python scripts/build_static_dashboard.py bench/eval_runs/full100_final
```

For the lean public full-100 artifact included in this repository, regenerate
the dashboard from the retained inputs:

```bash
uv run python scripts/build_static_dashboard.py \
  bench/results/full100 \
  --output bench/results/full100/dashboard.html
```
