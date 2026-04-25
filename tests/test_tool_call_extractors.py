"""
Tests for beigebox.tool_call_extractors and its integration into
normalize_response via enable_tool_call_extraction.
"""

from __future__ import annotations

import json

import pytest

from beigebox.tool_call_extractors import extract_tool_calls
from beigebox.response_normalizer import normalize_response


def _wrap(content: str, *, tool_calls=None) -> dict:
    """Build a minimal chat-completion response with the given content."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


# ── Anthropic XML — function_calls / invoke / parameter ────────────────────


def test_anthropic_function_calls_xml_single_well_formed():
    content = (
        "I'll look that up.\n"
        "<function_calls>\n"
        '<invoke name="search_web">\n'
        '<parameter name="query">solar prices 2026</parameter>\n'
        '<parameter name="limit">5</parameter>\n'
        "</invoke>\n"
        "</function_calls>\n"
    )
    calls, rewritten = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "search_web"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"query": "solar prices 2026", "limit": "5"}
    assert "function_calls" not in rewritten
    assert "I'll look that up." in rewritten


def test_anthropic_xml_two_invocations_in_one_response():
    content = (
        "<function_calls>\n"
        '<invoke name="get_weather"><parameter name="city">SF</parameter></invoke>\n'
        '<invoke name="get_weather"><parameter name="city">NYC</parameter></invoke>\n'
        "</function_calls>"
    )
    calls, rewritten = extract_tool_calls(content)
    assert len(calls) == 2
    assert [json.loads(c["function"]["arguments"])["city"] for c in calls] == ["SF", "NYC"]
    assert rewritten == ""


# ── Anthropic <tool_use> with JSON input ───────────────────────────────────


def test_anthropic_tool_use_xml_with_json_input():
    content = (
        "<tool_use><name>add</name>"
        '<input>{"a": 17, "b": 23}</input></tool_use>'
    )
    calls, rewritten = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "add"
    assert json.loads(calls[0]["function"]["arguments"]) == {"a": 17, "b": 23}
    assert rewritten == ""


# ── Explicit markers ───────────────────────────────────────────────────────


def test_explicit_marker_pipe_style():
    content = (
        "<|tool_call|>"
        '{"name": "lookup", "arguments": {"q": "hi"}}'
        "<|/tool_call|>"
    )
    calls, _ = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "lookup"


# ── Fenced JSON blocks ─────────────────────────────────────────────────────


def test_fenced_tool_call_explicit_hint():
    content = (
        "Here's the call:\n"
        "```tool_call\n"
        '{"name": "fetch_url", "arguments": {"url": "https://x"}}\n'
        "```\n"
    )
    calls, rewritten = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "fetch_url"
    assert "```tool_call" not in rewritten


def test_fenced_json_with_sniff_match():
    content = (
        "Need to call this:\n"
        "```json\n"
        '{"name": "cmd", "arguments": {"flag": true}}\n'
        "```\n"
    )
    calls, _ = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "cmd"


def test_fenced_json_that_is_just_an_example_rejected():
    """Placeholder name 'example' must be rejected by the name guard."""
    content = (
        "Tools look like this:\n"
        "```json\n"
        '{"name": "example", "arguments": {"foo": "bar"}}\n'
        "```\n"
    )
    calls, rewritten = extract_tool_calls(content)
    assert calls is None
    assert rewritten == content


# ── LangChain / ReAct ──────────────────────────────────────────────────────


def test_langchain_action_action_input_pair():
    content = (
        "Thought: I need to search.\n"
        'Action: search\n'
        'Action Input: {"q": "solar 2026"}\n'
        "Observation: ..."
    )
    calls, _ = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "search"
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "solar 2026"}


def test_react_bracket_syntax():
    content = "Thinking ...\nAction: search[q=\"solar\", limit=5]"
    calls, _ = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "search"
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "solar", "limit": "5"}


# ── Bare-JSON last resort + coverage guard ─────────────────────────────────


def test_bare_json_dominates_content():
    content = '{"name": "ping", "arguments": {"host": "1.1.1.1"}}'
    calls, rewritten = extract_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "ping"
    assert rewritten == ""


def test_bare_json_inside_prose_paragraph_not_extracted():
    content = (
        "I would call the tool with input like "
        '{"name": "ping", "arguments": {"host": "1.1.1.1"}} '
        "but I don't actually have access to it. Sorry about that, "
        "let me explain what would happen instead in detail."
    )
    calls, rewritten = extract_tool_calls(content)
    assert calls is None
    assert rewritten == content


# ── Name guards ────────────────────────────────────────────────────────────


def test_tool_name_not_in_declared_set_rejected():
    content = '{"name": "definitely_not_real", "arguments": {"x": 1}}'
    calls, _ = extract_tool_calls(content, declared_tools={"only_real_tool"})
    assert calls is None


def test_tool_name_in_declared_set_accepted():
    content = '{"name": "only_real_tool", "arguments": {"x": 1}}'
    calls, _ = extract_tool_calls(content, declared_tools={"only_real_tool"})
    assert len(calls) == 1


# ── Failure modes ──────────────────────────────────────────────────────────


def test_malformed_xml_falls_through_to_next_extractor():
    """Broken anthropic XML should not bury a perfectly good fenced block below."""
    content = (
        "<function_calls>\n"
        '<invoke name="broken">\n'  # never closed
        "still trying\n"
        "```tool_call\n"
        '{"name": "fallback", "arguments": {"ok": true}}\n'
        "```\n"
    )
    errors: list[str] = []
    calls, _ = extract_tool_calls(content, errors=errors)
    assert calls is not None
    assert calls[0]["function"]["name"] == "fallback"


def test_extraction_disabled_by_default():
    """normalize_response without the kwarg never extracts."""
    content = '{"name": "ping", "arguments": {"host": "1.1.1.1"}}'
    n = normalize_response(_wrap(content))
    assert n.tool_calls is None
    assert n.content == content  # untouched


def test_extraction_enabled_lifts_into_normalized_response():
    content = '{"name": "ping", "arguments": {"host": "1.1.1.1"}}'
    n = normalize_response(_wrap(content), enable_tool_call_extraction=True)
    assert n.tool_calls is not None
    assert len(n.tool_calls) == 1
    assert n.tool_calls[0]["function"]["name"] == "ping"
    assert n.content == ""
    # An extraction note was logged
    assert any(e.startswith("content_rewritten:") for e in n.errors)


def test_pre_existing_openai_tool_calls_skip_extraction():
    """When upstream already populated tool_calls, the shim is bypassed."""
    upstream = [{"id": "call_existing", "type": "function",
                 "function": {"name": "existing", "arguments": "{}"}}]
    content = '{"name": "different", "arguments": {}}'
    n = normalize_response(
        _wrap(content, tool_calls=upstream),
        enable_tool_call_extraction=True,
    )
    assert n.tool_calls == upstream
    # Content untouched because we didn't run extraction
    assert n.content == content


def test_multiple_calls_only_from_first_winning_extractor():
    """XML invokes win; the JSON fence below them is ignored, not double-counted."""
    content = (
        "<function_calls>\n"
        '<invoke name="search"><parameter name="q">a</parameter></invoke>\n'
        "</function_calls>\n"
        "```json\n"
        '{"name": "search", "arguments": {"q": "b"}}\n'
        "```\n"
    )
    calls, _ = extract_tool_calls(content)
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "a"}


# ── Determinism + shape ────────────────────────────────────────────────────


def test_id_is_deterministic_and_well_formed():
    content = (
        "<function_calls>"
        '<invoke name="x"><parameter name="a">1</parameter></invoke>'
        "</function_calls>"
    )
    a, _ = extract_tool_calls(content)
    b, _ = extract_tool_calls(content)
    assert a[0]["id"] == b[0]["id"]
    assert a[0]["id"].startswith("call_")
    assert len(a[0]["id"]) == len("call_") + 12


def test_canonical_shape_matches_openai():
    content = '{"name": "p", "arguments": {"x": 1}}'
    calls, _ = extract_tool_calls(content)
    c = calls[0]
    assert c["type"] == "function"
    assert set(c.keys()) >= {"id", "type", "function", "_extracted_from"}
    assert set(c["function"].keys()) == {"name", "arguments"}
    assert isinstance(c["function"]["arguments"], str)  # JSON-string per OpenAI
