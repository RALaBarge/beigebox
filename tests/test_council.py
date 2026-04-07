"""
Tests for beigebox/agents/council.py — council then commander pattern.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from beigebox.agents.council import (
    _strip_think,
    _is_thinker,
    _extract_json_array,
    propose,
    execute,
)


# ── _strip_think ──────────────────────────────────────────────────────────────

class TestStripThink:
    def test_no_think_block(self):
        assert _strip_think("hello") == "hello"

    def test_strips_think_block(self):
        text = "<think>internal reasoning</think>final answer"
        assert _strip_think(text) == "final answer"

    def test_strips_multiline_think(self):
        text = "<think>\nline1\nline2\n</think>answer"
        assert _strip_think(text) == "answer"

    def test_strips_multiple_think_blocks(self):
        text = "<think>a</think>mid<think>b</think>end"
        assert _strip_think(text) == "midend"

    def test_empty_string(self):
        assert _strip_think("") == ""


# ── _is_thinker ───────────────────────────────────────────────────────────────

class TestIsThinker:
    def test_qwen3_is_thinker(self):
        assert _is_thinker("qwen3:8b") is True

    def test_qwen3_case_insensitive(self):
        assert _is_thinker("Qwen3:8b") is True

    def test_deepseek_r1_is_thinker(self):
        assert _is_thinker("deepseek-r1:7b") is True

    def test_deepseek_r_prefix(self):
        assert _is_thinker("deepseek-r:latest") is True

    def test_llama_not_thinker(self):
        assert _is_thinker("qwen3:4b") is False

    def test_qwen25_not_thinker(self):
        assert _is_thinker("qwen2.5:7b") is False

    def test_empty_not_thinker(self):
        assert _is_thinker("") is False


# ── _extract_json_array ───────────────────────────────────────────────────────

class TestExtractJsonArray:
    def test_clean_array(self):
        text = '[{"name": "A", "model": "m", "task": "t"}]'
        result = _extract_json_array(text)
        assert result == [{"name": "A", "model": "m", "task": "t"}]

    def test_with_markdown_fence(self):
        text = '```json\n[{"name": "A", "model": "m", "task": "t"}]\n```'
        result = _extract_json_array(text)
        assert result is not None
        assert len(result) == 1

    def test_array_embedded_in_prose(self):
        text = 'Here is the council:\n[{"name": "X", "model": "y", "task": "z"}]\nDone.'
        result = _extract_json_array(text)
        assert result is not None
        assert result[0]["name"] == "X"

    def test_multiple_members(self):
        arr = [
            {"name": "Analyst", "model": "qwen3:4b", "task": "analyse"},
            {"name": "Coder",   "model": "qwen2.5:7b",  "task": "code review"},
        ]
        result = _extract_json_array(json.dumps(arr))
        assert result == arr

    def test_returns_none_on_garbage(self):
        assert _extract_json_array("not json at all") is None

    def test_returns_none_for_object_not_array(self):
        assert _extract_json_array('{"name": "A"}') is None

    def test_empty_array(self):
        result = _extract_json_array("[]")
        assert result == []

    def test_strips_backtick_fence(self):
        text = "```[{\"name\": \"A\", \"model\": \"m\", \"task\": \"t\"}]```"
        result = _extract_json_array(text)
        assert result is not None


# ── propose (mocked) ──────────────────────────────────────────────────────────

class TestPropose:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        proposal = [
            {"name": "Analyst", "model": "qwen3:4b", "task": "analyse the query"},
            {"name": "Coder",   "model": "qwen2.5:7b",  "task": "write code"},
        ]
        with patch("beigebox.agents.council._fetch_models", new=AsyncMock(return_value=["qwen3:4b", "qwen2.5:7b"])), \
             patch("beigebox.agents.council._chat",         new=AsyncMock(return_value=json.dumps(proposal))):
            result = await propose("write a sorting algorithm", "http://localhost:11434", "qwen3:4b")

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["name"] == "Analyst"

    @pytest.mark.asyncio
    async def test_fallback_on_bad_json(self):
        with patch("beigebox.agents.council._fetch_models", new=AsyncMock(return_value=["qwen3:4b"])), \
             patch("beigebox.agents.council._chat",         new=AsyncMock(return_value="not json")):
            result = await propose("query", "http://localhost:11434", "qwen3:4b")

        assert isinstance(result, list)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_sanitises_name_length(self):
        long_name = "X" * 200
        proposal = [{"name": long_name, "model": "qwen3:4b", "task": "do stuff"}]
        with patch("beigebox.agents.council._fetch_models", new=AsyncMock(return_value=["qwen3:4b"])), \
             patch("beigebox.agents.council._chat",         new=AsyncMock(return_value=json.dumps(proposal))):
            result = await propose("q", "http://localhost:11434", "qwen3:4b")

        assert len(result[0]["name"]) <= 60

    @pytest.mark.asyncio
    async def test_sanitises_task_length(self):
        long_task = "T" * 500
        proposal = [{"name": "A", "model": "qwen3:4b", "task": long_task}]
        with patch("beigebox.agents.council._fetch_models", new=AsyncMock(return_value=["qwen3:4b"])), \
             patch("beigebox.agents.council._chat",         new=AsyncMock(return_value=json.dumps(proposal))):
            result = await propose("q", "http://localhost:11434", "qwen3:4b")

        assert len(result[0]["task"]) <= 300


# ── execute (mocked) ──────────────────────────────────────────────────────────

class TestExecute:
    @pytest.mark.asyncio
    async def test_yields_member_done_and_synthesis(self):
        council = [
            {"name": "A", "model": "qwen3:4b", "task": "task A"},
            {"name": "B", "model": "qwen2.5:7b",  "task": "task B"},
        ]

        async def fake_chat(backend_url, model, messages, timeout=180.0):
            return f"result from {model}"

        with patch("beigebox.agents.council._chat", side_effect=fake_chat):
            events = []
            async for evt in execute("query", council, "http://localhost:11434", "qwen3:4b"):
                events.append(evt)

        types = [e["type"] for e in events]
        assert "member_start" in types
        assert "member_done"  in types
        assert "synthesizing" in types
        assert "synthesis"    in types

    @pytest.mark.asyncio
    async def test_empty_council_yields_error(self):
        events = []
        async for evt in execute("q", [], "http://localhost:11434", "m"):
            events.append(evt)
        assert any(e["type"] == "error" for e in events)

    @pytest.mark.asyncio
    async def test_member_error_on_chat_failure(self):
        council = [{"name": "Fail", "model": "bad-model", "task": "do it"}]

        async def failing_chat(*a, **kw):
            raise RuntimeError("model not found")

        with patch("beigebox.agents.council._chat", side_effect=failing_chat):
            events = []
            async for evt in execute("q", council, "http://localhost:11434", "bad-model"):
                events.append(evt)

        assert any(e["type"] == "member_error" for e in events)

    @pytest.mark.asyncio
    async def test_synthesis_receives_all_results(self):
        council = [
            {"name": "A", "model": "m1", "task": "t1"},
            {"name": "B", "model": "m2", "task": "t2"},
        ]
        synthesis_inputs = []

        async def fake_chat(backend_url, model, messages, timeout=180.0):
            # Capture what the synthesis call receives
            if any("Synthesise" in (m.get("content") or "") for m in messages):
                synthesis_inputs.append(messages[-1]["content"])
                return "synthesised"
            return "member result"

        with patch("beigebox.agents.council._chat", side_effect=fake_chat):
            events = []
            async for evt in execute("my query", council, "http://localhost:11434", "m1"):
                events.append(evt)

        assert synthesis_inputs, "synthesis was never called"
        assert "member result" in synthesis_inputs[0]
