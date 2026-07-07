"""Tests for direct benchmark baseline helpers."""

from src.config.prompts import DIRECT_NO_TOOLS_WRITER_PROMPT
from bench.direct_runner import DIRECT_TOOLS_SYSTEM_PROMPT, DirectBenchmarkGenerator


def test_direct_no_tools_prompt_does_not_require_reference_list_citations():
    assert "Available References" not in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "Use numbered citations" not in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "ONLY cite sources" not in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "avoid inventing citations" in DIRECT_NO_TOOLS_WRITER_PROMPT


def test_direct_no_tools_system_prompt_is_chatgpt_like():
    assert "You are ChatGPT" in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "formal research-report template" in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "Writer Agent" not in DIRECT_NO_TOOLS_WRITER_PROMPT
    assert "Executive Summary" not in DIRECT_NO_TOOLS_WRITER_PROMPT


def test_no_tools_prompt_is_simple_user_query(tmp_path):
    generator = DirectBenchmarkGenerator(model="openai/gpt-5", archive_root=tmp_path)
    task = {"id": 1, "prompt": "Analyze a topic", "language": "en"}

    no_tools_prompt = generator._no_tools_prompt(task)

    assert no_tools_prompt == "Analyze a topic"
    assert "NO-TOOLS BASELINE REQUIREMENTS" not in no_tools_prompt
    assert "REPORT DEPTH AND DEVELOPMENT REQUIREMENTS" not in no_tools_prompt


def test_no_tools_prompt_preserves_chinese_target_language(tmp_path):
    generator = DirectBenchmarkGenerator(model="openai/gpt-5", archive_root=tmp_path)

    no_tools_prompt = generator._no_tools_prompt({"id": 1, "prompt": "分析这个主题", "language": "zh"})

    assert no_tools_prompt == "分析这个主题\n\nPlease answer in Chinese."


def test_tools_prompt_is_simple_user_query(tmp_path):
    generator = DirectBenchmarkGenerator(model="openai/gpt-5", archive_root=tmp_path)
    task = {"id": 1, "prompt": "Analyze a topic", "language": "en"}

    tools_prompt = generator._tools_prompt(task)

    assert tools_prompt == "Analyze a topic"
    assert "REPORT DEPTH AND DEVELOPMENT REQUIREMENTS" not in tools_prompt
    assert "Research Context" not in tools_prompt


def test_tools_system_prompt_is_single_agent_baseline():
    assert "Use available tools as needed" in DIRECT_TOOLS_SYSTEM_PROMPT
    assert "answer the user's request directly" in DIRECT_TOOLS_SYSTEM_PROMPT
    assert "Do not produce the final report yet" not in DIRECT_TOOLS_SYSTEM_PROMPT
    assert "research dossier" not in DIRECT_TOOLS_SYSTEM_PROMPT
    assert "REPORT DEPTH AND DEVELOPMENT REQUIREMENTS" not in DIRECT_TOOLS_SYSTEM_PROMPT
