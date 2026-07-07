"""Tests for the benchmark runner helpers."""

import json
from pathlib import Path
import sys

from bench.benchmark_runner import (
    PILOT10_IDS,
    RunnerConfig,
    RaceBenchmarkRunner,
    aggregate_results,
    backfill_missing_costs_from_archive,
    flatten_metrics_row,
    resolve_profile_tasks,
)


def _tasks():
    return [
        {"id": idx, "prompt": f"Prompt {idx}", "language": "en" if idx % 2 else "zh"}
        for idx in range(1, 101)
    ]


class TestProfileResolution:
    def test_pilot1_en(self):
        tasks = resolve_profile_tasks(_tasks(), "pilot1_en")
        assert [task["id"] for task in tasks] == [51]

    def test_pilot10_mix(self):
        tasks = resolve_profile_tasks(_tasks(), "pilot10_mix")
        assert [task["id"] for task in tasks] == PILOT10_IDS

    def test_full100(self):
        tasks = resolve_profile_tasks(_tasks(), "full100")
        assert len(tasks) == 100


class TestRunnerManifestAndResume:
    def test_manifest_written_immediately_after_session_creation(self, tmp_path, monkeypatch):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
        )
        runner = RaceBenchmarkRunner(config)
        task = {"id": 51, "prompt": "Prompt 51", "language": "en"}
        manifest = runner._build_manifest([task])

        def fake_request(method, path, payload=None):
            if method == "POST":
                return {"session_id": "session-51", "status": "created"}
            return {"status": "completed"}

        snapshot = {
            "session_id": "session-51",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:10:00",
            "completed_at": "2026-01-01T00:10:00",
            "total_wall_clock_seconds": 600,
            "phase_durations_seconds": {"planning": 10},
            "cost": {
                "total_cost": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "call_count": 0,
                "pricing_model": "openai/gpt-5-mini",
                "last_response_model": "gpt-5-mini",
                "cost_source": "litellm_model_pricing_table",
            },
            "tool_usage": {"total_attempts": 0, "total_duration_seconds": 0.0, "by_source_family": {}},
            "references": {"total": 0, "by_source_family": {}},
            "coordinator": {"launch_count": 0, "wave_count": 0},
            "report": {"word_count": 1, "char_count": 6},
            "archive": {"report_md": str(tmp_path / "report.md")},
        }
        Path(snapshot["archive"]["report_md"]).write_text("hello\n", encoding="utf-8")

        monkeypatch.setattr(runner, "_request", fake_request)
        monkeypatch.setattr(runner, "_load_archive_snapshot", lambda session_id: snapshot)
        runner._run_task(task, manifest)

        saved = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
        assert saved["sessions"]["51"]["session_id"] == "session-51"

    def test_manifest_records_generator_and_system_name(self, tmp_path):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
            generator="direct_tools",
            variant_name="gpt5_tools",
            system_name="custom_direct_tools",
            direct_model="openai/gpt-5",
        )
        runner = RaceBenchmarkRunner(config)
        manifest = runner._build_manifest([{"id": 51, "prompt": "Prompt 51", "language": "en"}])

        assert manifest["system_name"] == "custom_direct_tools"
        assert manifest["generator"] == "direct_tools"
        assert manifest["variant_name"] == "gpt5_tools"
        assert manifest["direct_model"] == "openai/gpt-5"

    def test_resume_skips_completed_tasks(self, tmp_path, monkeypatch):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
            resume=True,
        )
        runner = RaceBenchmarkRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        runner.metrics_jsonl_path.write_text(json.dumps({"task_id": 51}) + "\n", encoding="utf-8")
        runner.manifest_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

        called = []
        monkeypatch.setattr("bench.benchmark_runner.load_tasks", lambda path: _tasks())
        monkeypatch.setattr(runner, "_run_task", lambda task, manifest: called.append(task["id"]))
        monkeypatch.setattr(runner, "_run_drb_scoring", lambda: None)
        monkeypatch.setattr(runner, "_write_post_run_outputs", lambda: None)
        runner.run()
        assert called == []

    def test_incremental_scoring_runs_after_completed_task(self, tmp_path, monkeypatch):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
        )
        runner = RaceBenchmarkRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        runner.raw_data_path.parent.mkdir(parents=True, exist_ok=True)
        runner.raw_data_path.write_text("", encoding="utf-8")

        score_calls = []
        monkeypatch.setattr("bench.benchmark_runner.load_tasks", lambda path: _tasks())
        monkeypatch.setattr(runner, "_run_task", lambda task, manifest: True)
        monkeypatch.setattr(runner, "_run_drb_scoring", lambda force=False: score_calls.append(force))
        monkeypatch.setattr(runner, "_write_post_run_outputs", lambda: None)

        runner.run()

        assert score_calls == [False, False]

    def test_score_only_skips_task_execution_and_runs_scoring(self, tmp_path, monkeypatch):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
            score_only=True,
        )
        runner = RaceBenchmarkRunner(config)
        runner.run_dir.mkdir(parents=True, exist_ok=True)
        runner.raw_data_path.parent.mkdir(parents=True, exist_ok=True)
        runner.raw_data_path.write_text(json.dumps({"id": 51, "prompt": "Prompt 51", "article": "hello"}) + "\n", encoding="utf-8")
        runner.manifest_path.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

        score_calls = []
        monkeypatch.setattr("bench.benchmark_runner.load_tasks", lambda path: (_ for _ in ()).throw(AssertionError("load_tasks should not be called")))
        monkeypatch.setattr(runner, "_run_drb_scoring", lambda force=False: score_calls.append(force))
        monkeypatch.setattr(runner, "_write_post_run_outputs", lambda: None)

        runner.run()

        assert score_calls == [False]

    def test_drb_scoring_uses_local_wrapper(self, tmp_path, monkeypatch):
        config = RunnerConfig(
            profile="pilot1_en",
            task_ids=None,
            run_id="run1",
            output_dir=tmp_path,
        )
        runner = RaceBenchmarkRunner(config)

        captured = {}

        def fake_run(cmd, cwd, env, check):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["check"] = check

        monkeypatch.setattr("bench.benchmark_runner.subprocess.run", fake_run)

        runner._run_drb_scoring(force=False)

        assert captured["cmd"][0] == sys.executable
        assert captured["cmd"][1].endswith("scripts/run_drb_scoring.py")
        assert captured["cwd"] == Path(__file__).resolve().parents[2]
        assert captured["check"] is True


class TestAggregation:
    def test_backfills_missing_cost_from_archived_events(self, tmp_path):
        archive_dir = tmp_path / "session-1"
        archive_dir.mkdir()
        events_path = archive_dir / "events.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps({"event_type": "session_created", "data": {}}),
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {
                                "total_cost": 0.25,
                                "prompt_tokens": 100,
                                "completion_tokens": 20,
                                "total_tokens": 120,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 50,
                                "call_count": 1,
                                "pricing_model": "gpt-5",
                                "last_response_model": "gpt-5",
                                "cost_source": "litellm_response_cost",
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        rows = [
            {
                "task_id": 1,
                "cost": {"total_cost": 0.0, "total_tokens": 0, "call_count": 0},
                "archive": {"events_jsonl": str(events_path)},
            }
        ]

        repaired = backfill_missing_costs_from_archive(rows)

        assert repaired[0]["cost"]["total_cost"] == 0.25
        assert repaired[0]["cost"]["total_tokens"] == 120
        assert repaired[0]["cost_backfilled_from_archive"] is True

    def test_backfills_partial_cost_from_later_archived_event(self, tmp_path):
        archive_dir = tmp_path / "session-1"
        archive_dir.mkdir()
        events_path = archive_dir / "events.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {"total_cost": 0.01, "total_tokens": 100, "call_count": 1},
                        }
                    ),
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {"total_cost": 0.08, "total_tokens": 800, "call_count": 2},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        rows = [
            {
                "task_id": 1,
                "cost": {"total_cost": 0.01, "total_tokens": 100, "call_count": 1},
                "archive": {"events_jsonl": str(events_path)},
            }
        ]

        repaired = backfill_missing_costs_from_archive(rows)

        assert repaired[0]["cost"]["total_cost"] == 0.08
        assert repaired[0]["cost"]["total_tokens"] == 800
        assert repaired[0]["cost"]["call_count"] == 2

    def test_sums_late_cost_segment_after_tracker_reset(self, tmp_path):
        archive_dir = tmp_path / "session-1"
        archive_dir.mkdir()
        events_path = archive_dir / "events.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {"total_cost": 0.01, "total_tokens": 100, "call_count": 1},
                        }
                    ),
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {"total_cost": 0.03, "total_tokens": 300, "call_count": 2},
                        }
                    ),
                    json.dumps(
                        {
                            "event_type": "cost_updated",
                            "data": {"total_cost": 0.04, "total_tokens": 400, "call_count": 1},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        rows = [
            {
                "task_id": 1,
                "cost": {"total_cost": 0.03, "total_tokens": 300, "call_count": 2},
                "archive": {"events_jsonl": str(events_path)},
            }
        ]

        repaired = backfill_missing_costs_from_archive(rows)

        assert repaired[0]["cost"]["total_cost"] == 0.07
        assert repaired[0]["cost"]["total_tokens"] == 700
        assert repaired[0]["cost"]["call_count"] == 3

    def test_summary_joins_scores_and_metrics(self):
        metrics = [
            {
                "task_id": 51,
                "language": "en",
                "status": "completed",
                "total_wall_clock_seconds": 10.0,
                "phase_durations_seconds": {"planning": 1.0},
                "cost": {
                    "total_cost": 2.0,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 4,
                    "call_count": 2,
                    "pricing_model": "openai/gpt-5-mini",
                    "last_response_model": "gpt-5-mini",
                    "cost_source": "litellm_model_pricing_table",
                },
                "tool_usage": {
                    "total_attempts": 3,
                    "by_source_family": {
                        "tavily": {
                            "attempts": 3,
                            "duration_seconds": 4.0,
                            "tools": {
                                "tavily_search": {
                                    "attempts": 3,
                                    "successes": 2,
                                    "failures": 1,
                                    "duration_seconds": 4.0,
                                }
                            },
                        }
                    },
                },
                "references": {"total": 1, "by_source_family": {"tavily": 1}},
                "report": {"word_count": 100},
            }
        ]
        scores = [
            {
                "id": 51,
                "comprehensiveness": 0.8,
                "insight": 0.7,
                "instruction_following": 0.9,
                "readability": 0.6,
                "overall_score": 0.75,
            }
        ]

        summary = aggregate_results(metrics, scores)
        assert summary["race"]["overall"] == 0.75
        assert summary["completion_rate"] == 1.0
        assert summary["tool_calls"]["total"] == 3
        assert summary["tool_calls"]["distribution_by_tool"] == [
            {
                "source_family": "tavily",
                "tool_name": "tavily_search",
                "tool_key": "tavily_tavily_search",
                "total_tool_attempts": 3,
                "avg_tool_attempts": 3.0,
                "total_tool_successes": 2,
                "avg_tool_successes": 2.0,
                "total_tool_failures": 1,
                "avg_tool_failures": 1.0,
                "total_tool_duration_seconds": 4.0,
                "avg_tool_duration_seconds": 4.0,
            }
        ]
        assert summary["cost"]["research_total"] == 2.0
        assert summary["cost"]["judge_total"] is None
        assert summary["cost"]["judge_cost_status"] == "not_tracked"
        assert summary["tokens"]["cache_read_input_total"] == 4

    def test_flatten_metrics_row(self):
        row = {
            "task_id": 1,
            "language": "en",
            "prompt": "Prompt",
            "session_id": "s1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:01:00",
            "completed_at": "2026-01-01T00:01:00",
            "total_wall_clock_seconds": 60.0,
            "phase_durations_seconds": {"planning": 5.0},
            "cost": {
                "total_cost": 1.0,
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "cache_creation_input_tokens": 1,
                "cache_read_input_tokens": 2,
                "call_count": 1,
                "pricing_model": "openai/gpt-5-mini",
                "last_response_model": "gpt-5-mini",
                "cost_source": "litellm_model_pricing_table",
            },
            "tool_usage": {
                "total_attempts": 2,
                "total_duration_seconds": 3.0,
                "by_source_family": {
                    "tavily": {
                        "attempts": 2,
                        "successes": 2,
                        "failures": 0,
                        "duration_seconds": 3.0,
                        "tools": {
                            "tavily_search": {
                                "attempts": 2,
                                "successes": 2,
                                "failures": 0,
                                "duration_seconds": 3.0,
                            }
                        },
                    }
                },
            },
            "references": {"total": 2, "by_source_family": {"tavily": 2}},
            "coordinator": {"launch_count": 1, "wave_count": 1},
            "report": {"word_count": 10, "char_count": 50},
            "archive": {"session_dir": "/tmp/archive"},
        }
        flat = flatten_metrics_row(row)
        assert flat["phase_planning_seconds"] == 5.0
        assert flat["tavily_tool_attempts"] == 2
        assert flat["tavily_tavily_search_attempts"] == 2
        assert flat["tavily_tavily_search_successes"] == 2
        assert flat["tavily_tavily_search_failures"] == 0
        assert flat["tavily_tavily_search_duration_seconds"] == 3.0
        assert flat["cache_creation_input_tokens"] == 1
        assert flat["cache_read_input_tokens"] == 2
        assert flat["pricing_model"] == "openai/gpt-5-mini"
