"""Tests for Option B — agentic tool prompting improvements.

Covers:
  - ToolResult dataclass (result.py)
  - BrowserMetaTool lazy loading (browser_meta.py)
  - Operator tool profile resolution helpers
  - _build_tool_rubric()
  - _is_small_model() / _resolve_tool_profile()
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from beigebox.tools.result import ToolResult
from beigebox.tools.browser_meta import BrowserMetaTool, _NAMESPACES
from beigebox.agents.operator import (
    _build_tool_rubric,
    _is_small_model,
    _resolve_tool_profile,
    _SMALL_MODEL_ADDENDUM,
)


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class TestToolResult:
    def test_ok_observation_includes_status_and_data(self):
        r = ToolResult(status="ok", data="hello world")
        obs = r.to_observation()
        assert "[status: ok]" in obs
        assert "hello world" in obs

    def test_ok_observation_includes_hint(self):
        r = ToolResult(status="ok", data="data", hint="do this next")
        obs = r.to_observation()
        assert "hint: do this next" in obs

    def test_error_observation_includes_recovery(self):
        r = ToolResult(status="error", data="failed", recovery_hint="try again")
        obs = r.to_observation()
        assert "[status: error]" in obs
        assert "recovery_hint: try again" in obs

    def test_error_does_not_show_hint(self):
        r = ToolResult(status="error", data="bad", hint="success hint", recovery_hint="fix it")
        obs = r.to_observation()
        assert "success hint" not in obs
        assert "recovery_hint: fix it" in obs

    def test_ok_does_not_show_recovery_hint(self):
        r = ToolResult(status="ok", data="good", recovery_hint="unused")
        obs = r.to_observation()
        assert "recovery_hint" not in obs

    def test_str_delegates_to_observation(self):
        r = ToolResult(status="ok", data="hello")
        assert str(r) == r.to_observation()

    def test_partial_status_works(self):
        r = ToolResult(status="partial", data="incomplete")
        obs = r.to_observation()
        assert "[status: partial]" in obs

    def test_metadata_is_stored_but_not_in_observation(self):
        r = ToolResult(status="ok", data="d", metadata={"url": "http://x.com"})
        assert r.metadata["url"] == "http://x.com"
        assert "url" not in r.to_observation()

    def test_no_hint_when_none(self):
        r = ToolResult(status="ok", data="x")
        obs = r.to_observation()
        assert "hint" not in obs


# ---------------------------------------------------------------------------
# BrowserMetaTool
# ---------------------------------------------------------------------------

def _make_browser_meta(run_return="ok result") -> BrowserMetaTool:
    bb = MagicMock()
    bb.run.return_value = run_return
    return BrowserMetaTool(bb)


class TestBrowserMetaToolDiscover:
    def test_empty_input_returns_discover(self):
        tool = _make_browser_meta()
        obs = tool.run("{}")
        result = ToolResult.__new__(ToolResult)
        # Discover returns status ok with namespace data
        assert "[status: ok]" in obs
        data = json.loads(obs.split("[status: ok]\n", 1)[1].split("\nhint:")[0])
        assert "namespaces" in data

    def test_discover_action_returns_namespaces(self):
        tool = _make_browser_meta()
        obs = tool.run('{"action": "discover"}')
        assert "[status: ok]" in obs
        assert "tabs" in obs
        assert "dom" in obs

    def test_discover_includes_hint(self):
        tool = _make_browser_meta()
        obs = tool.run('{"action": "discover"}')
        assert "hint:" in obs

    def test_all_expected_namespaces_in_discover(self):
        tool = _make_browser_meta()
        obs = tool.run('{"action": "discover"}')
        for ns in _NAMESPACES:
            assert ns in obs


class TestBrowserMetaToolProxy:
    def test_proxies_to_browserbox(self):
        bb = MagicMock()
        bb.run.return_value = "snapshot data"
        tool = BrowserMetaTool(bb)
        result = tool.run('{"action": "dom.snapshot", "input": ""}')
        bb.run.assert_called_once()
        call_arg = json.loads(bb.run.call_args[0][0])
        assert call_arg["tool"] == "dom.snapshot"
        assert "[status: ok]" in result
        assert "snapshot data" in result

    def test_tabs_open_includes_hint(self):
        bb = MagicMock()
        bb.run.return_value = "opened"
        tool = BrowserMetaTool(bb)
        result = tool.run('{"action": "tabs.open", "input": "https://example.com"}')
        assert "hint:" in result
        assert "dom.snapshot" in result or "dom.get_text" in result

    def test_error_from_browserbox_is_wrapped(self):
        bb = MagicMock()
        bb.run.return_value = "Error: could not connect to BrowserBox relay at ws://localhost:9009 — Connection refused"
        tool = BrowserMetaTool(bb)
        result = tool.run('{"action": "dom.snapshot", "input": ""}')
        assert "[status: error]" in result
        assert "recovery_hint:" in result
        assert "ws_relay.py" in result

    def test_timeout_error_gives_recovery(self):
        bb = MagicMock()
        bb.run.return_value = "Error: timed out waiting for response from BrowserBox"
        tool = BrowserMetaTool(bb)
        result = tool.run('{"action": "dom.snapshot", "input": ""}')
        assert "[status: error]" in result
        assert "recovery_hint:" in result

    def test_browser_not_connected_gives_recovery(self):
        bb = MagicMock()
        bb.run.return_value = "Error: browser not connected to relay"
        tool = BrowserMetaTool(bb)
        result = tool.run('{"action": "dom.get_text", "input": ""}')
        assert "[status: error]" in result
        assert "Chrome" in result or "extension" in result


class TestBrowserMetaToolValidation:
    def test_unknown_namespace_returns_error(self):
        tool = _make_browser_meta()
        result = tool.run('{"action": "bogus.action", "input": ""}')
        assert "[status: error]" in result
        assert "recovery_hint:" in result
        assert "discover" in result

    def test_invalid_json_returns_error(self):
        tool = _make_browser_meta()
        result = tool.run("not json")
        assert "[status: error]" in result

    def test_input_forwarded_to_browserbox(self):
        bb = MagicMock()
        bb.run.return_value = "clicked"
        tool = BrowserMetaTool(bb)
        tool.run('{"action": "dom.click", "input": "#submit"}')
        call_arg = json.loads(bb.run.call_args[0][0])
        assert call_arg["input"] == "#submit"


# ---------------------------------------------------------------------------
# Tool profile helpers
# ---------------------------------------------------------------------------

class TestResolveToolProfile:
    def _cfg(self, profiles=None, model_map=None):
        return {
            "operator": {
                "tool_profiles": profiles or {},
                "model_tool_profiles": model_map or {},
            }
        }

    def test_no_profiles_configured_returns_none(self):
        assert _resolve_tool_profile("qwen3:4b", {}) is None

    def test_exact_pattern_match(self):
        cfg = self._cfg(model_map={"qwen3:4b": "minimal"})
        assert _resolve_tool_profile("qwen3:4b", cfg) == "minimal"

    def test_wildcard_pattern_match(self):
        cfg = self._cfg(model_map={"*:3b": "minimal"})
        assert _resolve_tool_profile("llama3.2:3b", cfg) == "minimal"
        assert _resolve_tool_profile("qwen2.5:3b", cfg) == "minimal"

    def test_no_match_uses_default(self):
        cfg = self._cfg(model_map={"*:3b": "minimal", "default": "standard"})
        assert _resolve_tool_profile("llama3.1:70b", cfg) == "standard"

    def test_full_profile_returns_none(self):
        cfg = self._cfg(model_map={"default": "full"})
        assert _resolve_tool_profile("any-model", cfg) is None

    def test_case_insensitive_match(self):
        cfg = self._cfg(model_map={"*:3B": "minimal"})
        assert _resolve_tool_profile("llama3.2:3b", cfg) == "minimal"


class TestIsSmallModel:
    def _cfg(self, model_map=None):
        return {"operator": {"model_tool_profiles": model_map or {}}}

    def test_3b_suffix_is_small_heuristic(self):
        assert _is_small_model("llama3.2:3b", {}) is True

    def test_1b_suffix_is_small_heuristic(self):
        assert _is_small_model("qwen2.5:1b", {}) is True

    def test_7b_suffix_is_not_small(self):
        assert _is_small_model("llama3.2:8b", {}) is False

    def test_minimal_profile_maps_to_small(self):
        cfg = self._cfg({"*:3b": "minimal"})
        assert _is_small_model("llama3.2:3b", cfg) is True

    def test_standard_profile_not_small(self):
        cfg = self._cfg({"*:7b": "standard"})
        assert _is_small_model("llama3.1:7b", cfg) is False

    def test_full_profile_not_small(self):
        cfg = self._cfg({"default": "full"})
        assert _is_small_model("llama3.1:70b", cfg) is False


# ---------------------------------------------------------------------------
# _build_tool_rubric
# ---------------------------------------------------------------------------

class TestBuildToolRubric:
    def test_known_tools_included(self):
        rubric = _build_tool_rubric(["web_search", "calculator"])
        assert "web_search" in rubric
        assert "calculator" in rubric

    def test_unknown_tool_excluded(self):
        rubric = _build_tool_rubric(["completely_unknown_tool"])
        assert rubric == ""

    def test_empty_list_returns_empty(self):
        assert _build_tool_rubric([]) == ""

    def test_rubric_contains_guidance_text(self):
        rubric = _build_tool_rubric(["web_search"])
        assert "current events" in rubric or "search" in rubric.lower()

    def test_browser_meta_tool_in_rubric(self):
        rubric = _build_tool_rubric(["browser"])
        assert "browser" in rubric

    def test_partial_match_only_known_tools(self):
        rubric = _build_tool_rubric(["web_search", "unknown_xyz"])
        assert "web_search" in rubric
        assert "unknown_xyz" not in rubric


# ---------------------------------------------------------------------------
# _SMALL_MODEL_ADDENDUM content sanity
# ---------------------------------------------------------------------------

class TestSmallModelAddendum:
    def test_contains_one_tool_per_turn(self):
        assert "ONE tool" in _SMALL_MODEL_ADDENDUM or "one tool" in _SMALL_MODEL_ADDENDUM.lower()

    def test_contains_simplest_tool_guidance(self):
        assert "simplest" in _SMALL_MODEL_ADDENDUM.lower()
