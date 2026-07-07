"""Tests for src.utils.json_utils."""

import json

import pytest
from pydantic import BaseModel

from src.utils.json_utils import (
    extract_json_from_llm_response,
    parse_and_validate_json,
    safe_json_loads,
    json_to_pretty_string,
)


# ── Helpers ───────────────────────────────────────────────────────────


class _SampleSchema(BaseModel):
    name: str
    count: int = 0


# ── extract_json_from_llm_response ───────────────────────────────────


class TestExtractJson:
    def test_json_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json_from_llm_response(text) == {"key": "value"}

    def test_generic_code_block(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json_from_llm_response(text) == {"key": "value"}

    def test_bare_json(self):
        assert extract_json_from_llm_response('{"a": 1}') == {"a": 1}

    def test_json_embedded_in_text(self):
        text = 'Here is the plan:\n{"a": 1}\nDone.'
        assert extract_json_from_llm_response(text) == {"a": 1}

    def test_json_array(self):
        text = "Result: [1, 2, 3]"
        assert extract_json_from_llm_response(text) == [1, 2, 3]

    def test_empty_string(self):
        assert extract_json_from_llm_response("") is None

    def test_none(self):
        assert extract_json_from_llm_response(None) is None

    def test_invalid_json(self):
        assert extract_json_from_llm_response("not json at all") is None

    def test_nested_json(self):
        data = {"outer": {"inner": [1, 2]}}
        text = f"```json\n{json.dumps(data)}\n```"
        assert extract_json_from_llm_response(text) == data


# ── parse_and_validate_json ──────────────────────────────────────────


class TestParseAndValidate:
    def test_valid(self):
        text = '{"name": "Alice", "count": 5}'
        obj = parse_and_validate_json(text, _SampleSchema)
        assert obj is not None
        assert obj.name == "Alice"
        assert obj.count == 5

    def test_missing_required_field(self):
        text = '{"count": 5}'
        assert parse_and_validate_json(text, _SampleSchema) is None

    def test_no_json(self):
        assert parse_and_validate_json("no json here", _SampleSchema) is None

    def test_extra_fields_ignored(self):
        text = '{"name": "Bob", "extra": true}'
        obj = parse_and_validate_json(text, _SampleSchema)
        assert obj is not None
        assert obj.name == "Bob"


# ── safe_json_loads ──────────────────────────────────────────────────


class TestSafeJsonLoads:
    def test_valid(self):
        assert safe_json_loads('{"a": 1}') == {"a": 1}

    def test_invalid_with_default(self):
        assert safe_json_loads("bad", default=None) is None

    def test_invalid_without_default_raises(self):
        with pytest.raises(json.JSONDecodeError):
            safe_json_loads("bad")

    def test_none_input_with_default(self):
        assert safe_json_loads(None, default={}) == {}


# ── json_to_pretty_string ───────────────────────────────────────────


class TestJsonToPrettyString:
    def test_dict(self):
        result = json_to_pretty_string({"a": 1})
        parsed = json.loads(result)
        assert parsed == {"a": 1}

    def test_non_serializable_fallback(self):
        result = json_to_pretty_string(object())
        assert isinstance(result, str)

    def test_custom_indent(self):
        result = json_to_pretty_string({"a": 1}, indent=4)
        assert "    " in result
