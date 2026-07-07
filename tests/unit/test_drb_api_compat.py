"""Tests for DRB request-profile payload generation."""

import importlib.util
from pathlib import Path


def _load_api_module(monkeypatch, profile: str):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_REQUEST_PROFILE", profile)
    module_path = Path(__file__).resolve().parents[2].parent / "deep_research_bench" / "utils" / "api.py"
    spec = importlib.util.spec_from_file_location(f"drb_api_{profile}", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class TestDrbApiRequestProfiles:
    def test_reasoning_openai_payload(self, monkeypatch):
        module = _load_api_module(monkeypatch, "reasoning_openai")
        client = module.AIClient(api_key="x", model="gpt-5.5")
        payload = client._build_payload("user", "system", "gpt-5.5", {"reasoning_effort": "medium"})
        assert payload["max_completion_tokens"] == module.MAX_OUTPUT_TOKENS
        assert payload["reasoning_effort"] == "medium"
        assert "max_tokens" not in payload

    def test_compat_openai_payload(self, monkeypatch):
        module = _load_api_module(monkeypatch, "compat_openai")
        client = module.AIClient(api_key="x", model="gpt-oss-120b")
        payload = client._build_payload("user", "system", "gpt-oss-120b", {"reasoning_effort": "medium"})
        assert payload["max_tokens"] == module.MAX_OUTPUT_TOKENS
        assert "reasoning_effort" not in payload
        assert "max_completion_tokens" not in payload
