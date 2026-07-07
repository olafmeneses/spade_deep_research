"""JSON utility functions for parsing LLM responses."""

import json
import re
import logging
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
T = TypeVar('T', bound=BaseModel)


def extract_json_from_llm_response(response: str) -> Optional[dict]:
    """Extract JSON from an LLM response that may contain markdown."""
    if not response:
        return None
    
    text = response.strip()
    
    # Try extracting from code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif text.startswith("```"):
        text = text[3:].split("```")[0]
    else:
        # Find JSON object or array
        match = re.search(r'\{[\s\S]*\}', text) or re.search(r'\[[\s\S]*\]', text)
        if match:
            text = match.group()
    
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e}")
        return None


def parse_and_validate_json(response: str, schema: Type[T]) -> Optional[T]:
    """Extract JSON from response and validate against a Pydantic schema."""
    data = extract_json_from_llm_response(response)
    if data is None:
        return None
    
    try:
        return schema(**data)
    except ValidationError as e:
        logger.warning(f"Validation failed: {e}")
        return None


def safe_json_loads(json_string: str, default: Any = ...) -> Any:
    """Safely parse JSON, returning default on failure."""
    try:
        return json.loads(json_string)
    except (json.JSONDecodeError, TypeError) as e:
        if default is ...:
            raise
        logger.warning(f"JSON parse failed: {e}")
        return default


def json_to_pretty_string(data: Any, indent: int = 2) -> str:
    """Convert object to pretty JSON string."""
    try:
        return json.dumps(data, indent=indent, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(data)
