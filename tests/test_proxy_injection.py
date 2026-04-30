"""
Tests for proxy generation parameter injection.

Covers:
  - _inject_generation_params: default (no-op), inject when set, force override
  - _inject_generation_params: None values not injected

(HarnessOrchestrator._parse_json tests that used to live here were removed
in v3 along with the orchestrator itself.)
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

