"""Tests for benchmark telemetry and durable session archiving."""

from datetime import datetime, timedelta

from src.telemetry import SessionArchiveStore, SessionTelemetryRegistry


class TestSessionArchiveStore:
    def test_writes_snapshot_and_appends_events(self, tmp_path):
        store = SessionArchiveStore(tmp_path / "archive")
        store.write_snapshot("s1", {"session_id": "s1", "status": "created"})
        store.append_event("s1", "created", {"x": 1}, datetime(2026, 1, 1, 12, 0, 0))
        store.append_event("s1", "updated", {"x": 2}, datetime(2026, 1, 1, 12, 0, 1))

        session_json = tmp_path / "archive" / "s1" / "session.json"
        events_jsonl = tmp_path / "archive" / "s1" / "events.jsonl"
        assert session_json.exists()
        lines = events_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


class TestSessionTelemetryRegistry:
    def test_phase_durations_and_cost_updates(self, tmp_path):
        registry = SessionTelemetryRegistry(SessionArchiveStore(tmp_path / "archive"))
        created_at = datetime(2026, 1, 1, 12, 0, 0)
        registry.initialize_session("s1", "query", "created", created_at=created_at)
        registry.record_state_transition(
            "s1",
            previous_status="created",
            new_status="planning",
            query="query",
            timestamp=created_at + timedelta(seconds=5),
        )
        registry.record_state_transition(
            "s1",
            previous_status="planning",
            new_status="researching",
            query="query",
            timestamp=created_at + timedelta(seconds=15),
        )
        registry.record_tool_call("s1", "tavily", "tavily_search", True, 2.5)
        registry.record_reference("s1", "tavily", "https://example.com")
        registry.record_cost("s1", 1.25, 10, 5, 15, 1, 2, 2, "openai/gpt-5-mini", "gpt-5-mini")
        snapshot = registry.record_terminal_state("s1", "completed", {"completed_at": "2026-01-01T12:00:20"})

        assert snapshot["phase_durations_seconds"]["planning"] == 10.0
        assert snapshot["tool_usage"]["by_source_family"]["tavily"]["attempts"] == 1
        assert snapshot["references"]["by_source_family"]["tavily"] == 1
        assert snapshot["cost"]["total_cost"] == 1.25
        assert snapshot["cost"]["cache_creation_input_tokens"] == 1
        assert snapshot["cost"]["cache_read_input_tokens"] == 2
        assert snapshot["cost"]["pricing_model"] == "openai/gpt-5-mini"

    def test_report_written(self, tmp_path):
        registry = SessionTelemetryRegistry(SessionArchiveStore(tmp_path / "archive"))
        registry.initialize_session("s1", "query", "created")
        registry.record_report("s1", "# Report\n\nHello world")

        report_path = tmp_path / "archive" / "s1" / "report.md"
        assert report_path.exists()
        assert "Hello world" in report_path.read_text(encoding="utf-8")

    def test_report_normalizes_narrow_no_break_spaces(self, tmp_path):
        registry = SessionTelemetryRegistry(SessionArchiveStore(tmp_path / "archive"))
        registry.initialize_session("s1", "query", "created")
        registry.record_report(
            "s1",
            "Yield: 200\u202fPa; porosity 0.2\u202f%; drift ±\u202f10\u202f%; growth −\u202f0.3; adults 65\u202f+",
        )

        report_path = tmp_path / "archive" / "s1" / "report.md"
        report_text = report_path.read_text(encoding="utf-8")

        assert report_text == "Yield: 200 Pa; porosity 0.2%; drift ±10%; growth −0.3; adults 65+"
        assert "\u202f" not in report_text

    def test_non_status_updates_do_not_reset_completed_state(self, tmp_path):
        registry = SessionTelemetryRegistry(SessionArchiveStore(tmp_path / "archive"))
        created_at = datetime(2026, 1, 1, 12, 0, 0)

        registry.initialize_session("s1", "query", "created", created_at=created_at)
        registry.record_state_transition(
            "s1",
            previous_status="created",
            new_status="completed",
            query="query",
            timestamp=created_at + timedelta(seconds=10),
        )

        registry.record_report("s1", "# Report\n\nHello world")
        registry.record_cost("s1", 1.25, 10, 5, 15, 1, 2, 2, "openai/gpt-5-mini", "gpt-5-mini")

        session_json = tmp_path / "archive" / "s1" / "session.json"
        snapshot = session_json.read_text(encoding="utf-8")

        assert '"status": "completed"' in snapshot
