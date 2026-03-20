"""
Robust JSON extraction from LLM outputs.

Consolidates JSON parsing logic used across operators, validators, and ensemble agents.
Handles multiple fallback strategies:
1. Direct JSON parsing (assumes valid JSON)
2. Markdown code block extraction (```json ... ```)
3. Regex-based JSON object search (catches partial/incomplete objects)
"""

import json
import re
from typing import Any, Optional


def extract_json(text: str) -> Optional[dict | list]:
    """
    Extract JSON from LLM output using multiple strategies.

    Tries in order:
    1. Direct JSON parse (entire text)
    2. Extract from markdown code block (```json...```)
    3. Regex search for JSON object/array

    Args:
        text: Raw LLM output potentially containing JSON

    Returns:
        Parsed JSON dict/list, or None if extraction fails
    """
    if not text or not isinstance(text, str):
        return None

    # Strategy 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Markdown code block (```json ... ```)
    try:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    except (json.JSONDecodeError, AttributeError):
        pass

    # Strategy 3: Regex search for JSON object/array
    try:
        # Match balanced braces: { ... } or [ ... ]
        # This is best-effort; doesn't handle all edge cases
        match = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\])", text)
        if match:
            return json.loads(match.group(1))
    except (json.JSONDecodeError, AttributeError):
        pass

    return None


def extract_json_list(text: str) -> list[dict] | list[str]:
    """
    Extract JSON array from LLM output.

    Returns list of dicts if array contains objects, list of strings if strings.
    Falls back to empty list if no JSON array found.

    Args:
        text: Raw LLM output potentially containing JSON array

    Returns:
        Parsed array or empty list
    """
    result = extract_json(text)
    if isinstance(result, list):
        return result
    return []


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Extract JSON object from LLM output.

    Returns empty dict if no JSON object found.

    Args:
        text: Raw LLM output potentially containing JSON object

    Returns:
        Parsed object dict or empty dict
    """
    result = extract_json(text)
    if isinstance(result, dict):
        return result
    return {}


def safe_json_get(data: dict | str, key: str, default: Any = None) -> Any:
    """
    Safely get value from dict or JSON string.

    Useful when data might be either a dict or a JSON string representation.

    Args:
        data: Dict or JSON string
        key: Key to retrieve
        default: Value if key not found

    Returns:
        Value for key, or default if not found
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return default

    if isinstance(data, dict):
        return data.get(key, default)

    return default
