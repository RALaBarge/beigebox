"""
Tests for proxy generation parameter injection and harness JSON parsing.

Covers:
  - _inject_generation_params: default (no-op), inject when set, force override
  - _inject_generation_params: None values not injected
  - HarnessOrchestrator._parse_json: fences, trailing commas, truncation recovery,
    embedded object extraction, fallback on total failure
"""

import pytest
from unittest.mock import patch


# ─────────────────────────────────────────────────────────────────────────────
# Generation Parameter Injection
#
# proxy.py can't be imported without chromadb, so we test the logic directly
# by extracting the method body into a standalone helper that mirrors it exactly.
# This tests the algorithm, not the wiring (wiring is covered by smoke.sh).
# ─────────────────────────────────────────────────────────────────────────────

def _inject_generation_params(body: dict, runtime: dict) -> dict:
    """
    Standalone copy of LLMProxy._inject_generation_params for unit testing.
    Mirrors the implementation in beigebox/proxy.py exactly.
    """
    force = runtime.get("gen_force", False)

    param_map = {
        "gen_temperature":    "temperature",
        "gen_top_p":          "top_p",
        "gen_top_k":          "top_k",
        "gen_num_ctx":        "num_ctx",
        "gen_repeat_penalty": "repeat_penalty",
        "gen_max_tokens":     "max_tokens",
        "gen_seed":           "seed",
        "gen_stop":           "stop",
    }

    for rt_key, body_key in param_map.items():
        val = runtime.get(rt_key)
        if val is None:
            continue
        if force or body_key not in body or body[body_key] is None:
            body[body_key] = val

    return body


class TestInjectGenerationParams:
    def test_no_op_when_runtime_empty(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {})
        assert "temperature" not in result
        assert "top_p" not in result

    def test_injects_temperature(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {"gen_temperature": 0.7})
        assert result["temperature"] == 0.7

    def test_injects_multiple_params(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {
            "gen_temperature": 0.5, "gen_top_p": 0.9, "gen_max_tokens": 512
        })
        assert result["temperature"] == 0.5
        assert result["top_p"] == 0.9
        assert result["max_tokens"] == 512

    def test_does_not_override_frontend_value_by_default(self):
        body = {"messages": [], "temperature": 1.0}
        result = _inject_generation_params(body, {"gen_temperature": 0.1})
        assert result["temperature"] == 1.0

    def test_force_overrides_frontend_value(self):
        body = {"messages": [], "temperature": 1.0}
        result = _inject_generation_params(body, {"gen_temperature": 0.1, "gen_force": True})
        assert result["temperature"] == 0.1

    def test_none_value_not_injected(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {"gen_temperature": None, "gen_top_p": 0.8})
        assert "temperature" not in result
        assert result["top_p"] == 0.8

    def test_injects_stop_sequences(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {"gen_stop": ["<|end|>", "STOP"]})
        assert result["stop"] == ["<|end|>", "STOP"]

    def test_injects_seed(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {"gen_seed": 42})
        assert result["seed"] == 42

    def test_injects_num_ctx(self):
        body = {"messages": []}
        result = _inject_generation_params(body, {"gen_num_ctx": 4096})
        assert result["num_ctx"] == 4096

    def test_returns_body_unchanged_type(self):
        body = {"messages": [], "model": "llama3.2"}
        result = _inject_generation_params(body, {})
        assert isinstance(result, dict)
        assert result["model"] == "llama3.2"

    def test_force_false_preserves_existing_none(self):
        """If frontend explicitly passed None for a key, and force=False, we still inject."""
        body = {"messages": [], "temperature": None}
        result = _inject_generation_params(body, {"gen_temperature": 0.5})
        assert result["temperature"] == 0.5

    def test_all_params_injected(self):
        body = {"messages": []}
        rt = {
            "gen_temperature": 0.8,
            "gen_top_p": 0.95,
            "gen_top_k": 40,
            "gen_num_ctx": 8192,
            "gen_repeat_penalty": 1.1,
            "gen_max_tokens": 1024,
            "gen_seed": 123,
            "gen_stop": ["END"],
        }
        result = _inject_generation_params(body, rt)
        assert result["temperature"] == 0.8
        assert result["top_p"] == 0.95
        assert result["top_k"] == 40
        assert result["num_ctx"] == 8192
        assert result["repeat_penalty"] == 1.1
        assert result["max_tokens"] == 1024
        assert result["seed"] == 123
        assert result["stop"] == ["END"]


# ─────────────────────────────────────────────────────────────────────────────
# HarnessOrchestrator._parse_json
# ─────────────────────────────────────────────────────────────────────────────

class TestHarnessParseJson:
    """
    Tests for HarnessOrchestrator._parse_json static method.

    This is pure string → dict logic with no I/O.
    """

    @pytest.fixture
    def parse(self):
        with patch("beigebox.agents.harness_orchestrator.get_config", return_value={
            "backend": {"url": "http://fake:11434", "default_model": "test"},
            "operator": {"model": "test"},
            "server": {"port": 8000},
        }):
            from beigebox.agents.harness_orchestrator import HarnessOrchestrator
            return HarnessOrchestrator._parse_json

    def test_clean_json(self, parse):
        raw = '{"action": "finish", "answer": "done"}'
        result = parse(raw, fallback={"action": "error"})
        assert result["action"] == "finish"
        assert result["answer"] == "done"

    def test_strips_json_fence(self, parse):
        raw = '```json\n{"action": "finish"}\n```'
        result = parse(raw, fallback={})
        assert result["action"] == "finish"

    def test_strips_plain_fence(self, parse):
        raw = '```\n{"action": "continue"}\n```'
        result = parse(raw, fallback={})
        assert result["action"] == "continue"

    def test_trailing_comma_in_object(self, parse):
        raw = '{"action": "finish", "answer": "ok",}'
        result = parse(raw, fallback={})
        assert result["action"] == "finish"

    def test_trailing_comma_in_array(self, parse):
        raw = '{"tasks": ["a", "b",]}'
        result = parse(raw, fallback={})
        assert result["tasks"] == ["a", "b"]

    def test_embedded_object_with_prose(self, parse):
        raw = 'Sure! Here is the JSON: {"action": "dispatch", "tasks": []} Hope that helps!'
        result = parse(raw, fallback={})
        assert result["action"] == "dispatch"

    def test_truncated_json_recovery(self, parse):
        """JSON truncated after a complete value (missing closing brace) is repaired."""
        # Truncated after a complete key:value pair — brace depth = 1
        raw = '{"action": "finish", "answer": "done"'
        result = parse(raw, fallback={"action": "error"})
        assert result.get("action") != "error"
        assert result.get("action") == "finish"

    def test_truncated_nested_json_recovery(self, parse):
        """Nested object truncated — multiple missing closing braces."""
        # Depth 2 — outer object + inner object both unclosed
        raw = '{"action": "dispatch", "meta": {"key": "value"'
        result = parse(raw, fallback={"action": "error"})
        assert result.get("action") != "error"

    def test_fallback_on_garbage(self, parse):
        raw = "this is not json at all, no braces nothing"
        fallback = {"action": "continue", "assessment": "fallback"}
        result = parse(raw, fallback=fallback)
        assert result == fallback

    def test_fallback_on_empty_string(self, parse):
        fallback = {"action": "error"}
        result = parse("", fallback=fallback)
        assert result == fallback

    def test_fenced_with_trailing_comma(self, parse):
        """Combination: fence + trailing comma."""
        raw = '```json\n{"action": "finish", "answer": "ok",}\n```'
        result = parse(raw, fallback={})
        assert result["action"] == "finish"

    def test_returns_dict(self, parse):
        raw = '{"x": 1}'
        result = parse(raw, fallback={})
        assert isinstance(result, dict)

    def test_fallback_is_returned_as_dict(self, parse):
        result = parse("garbage %%%", fallback={"x": "y"})
        assert isinstance(result, dict)
        assert result["x"] == "y"
