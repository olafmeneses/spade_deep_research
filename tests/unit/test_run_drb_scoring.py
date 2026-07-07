"""Tests for the local DRB scoring wrapper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_run_drb_scoring_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "run_drb_scoring.py"
    spec = importlib.util.spec_from_file_location("run_drb_scoring_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_filtered_query_file_excludes_existing_ids(tmp_path):
    module = _load_run_drb_scoring_module()
    query_file = tmp_path / "query.jsonl"
    rows = [
        {"id": 1, "prompt": "one", "language": "en"},
        {"id": 2, "prompt": "two", "language": "zh"},
        {"id": 3, "prompt": "three", "language": "en"},
    ]
    query_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    filtered_path = Path(module._build_filtered_query_file(str(query_file), {1, 3}))

    filtered_rows = [json.loads(line) for line in filtered_path.read_text(encoding="utf-8").splitlines()]
    assert filtered_rows == [{"id": 2, "prompt": "two", "language": "zh"}]


def test_build_filtered_query_file_returns_original_when_no_pending_tasks(tmp_path):
    module = _load_run_drb_scoring_module()
    query_file = tmp_path / "query.jsonl"
    rows = [
        {"id": 1, "prompt": "one", "language": "en"},
        {"id": 2, "prompt": "two", "language": "zh"},
    ]
    query_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    filtered_path = module._build_filtered_query_file(str(query_file), {1, 2})

    assert filtered_path != str(query_file)
    assert Path(filtered_path).read_text(encoding="utf-8") == ""
