"""
Tests for beigebox/agents/routing_rules.py

Covers:
  - _match_rule: all conditions
  - _apply_action: all action types
  - evaluate_routing_rules: priority, continue, pass_through, auth key stripping
  - Edge cases: invalid regex, missing inject_file, empty rules
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from beigebox.agents.routing_rules import (
    _match_rule,
    _apply_action,
    _prepend_system_message,
    evaluate_routing_rules,
    BB_FORCE_BACKEND,
    BB_SKIP_SEMANTIC_CACHE,
    BB_RULE_TAG,
    BB_AUTH_KEY,
    BB_FORCED_TOOLS,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _body(model="llama3.2", msg="hello", tools=None, msgs=None, **kw):
    messages = msgs or [{"role": "user", "content": msg}]
    b = {"model": model, "messages": messages}
    if tools:
        b["tools"] = tools
    b.update(kw)
    return b


# ── _match_rule ────────────────────────────────────────────────────────────────

class TestMatchRuleMessage:
    def test_no_conditions_always_true(self):
        assert _match_rule({}, "anything", _body(), None) is True

    def test_message_regex_match(self):
        assert _match_rule({"message": "fix.*bug"}, "fix this bug", _body(), None) is True

    def test_message_regex_no_match(self):
        assert _match_rule({"message": "^deploy"}, "fix this bug", _body(), None) is False

    def test_message_regex_case_insensitive(self):
        assert _match_rule({"message": "REFACTOR"}, "please refactor this", _body(), None) is True

    def test_message_invalid_regex_returns_false(self):
        assert _match_rule({"message": "["}, "test", _body(), None) is False

    def test_message_contains_match(self):
        assert _match_rule({"message_contains": "summarize"}, "can you summarize this?", _body(), None) is True

    def test_message_contains_no_match(self):
        assert _match_rule({"message_contains": "deploy"}, "fix the bug", _body(), None) is False

    def test_message_contains_case_insensitive(self):
        assert _match_rule({"message_contains": "TLDR"}, "give me a tldr", _body(), None) is True


class TestMatchRuleModel:
    def test_exact_model_match(self):
        assert _match_rule({"model": "llama3.2"}, "", _body(model="llama3.2"), None) is True

    def test_exact_model_no_match(self):
        assert _match_rule({"model": "llama3.2"}, "", _body(model="qwen3:8b"), None) is False

    def test_glob_match(self):
        assert _match_rule({"model": "qwen3:*"}, "", _body(model="qwen3:14b"), None) is True
        assert _match_rule({"model": "qwen3:*"}, "", _body(model="llama3.2"), None) is False

    def test_wildcard_matches_all(self):
        assert _match_rule({"model": "*"}, "", _body(model="anything"), None) is True


class TestMatchRuleAuthKey:
    def test_matching_key_name(self):
        assert _match_rule({"auth_key": "ci-runner"}, "", _body(), "ci-runner") is True

    def test_wrong_key_name(self):
        assert _match_rule({"auth_key": "ci-runner"}, "", _body(), "admin") is False

    def test_no_key_when_required(self):
        assert _match_rule({"auth_key": "ci-runner"}, "", _body(), None) is False


class TestMatchRuleHasTools:
    def test_has_tools_true_matches(self):
        body = _body(tools=[{"type": "function", "name": "search"}])
        assert _match_rule({"has_tools": True}, "", body, None) is True

    def test_has_tools_false_no_tools(self):
        assert _match_rule({"has_tools": False}, "", _body(), None) is True

    def test_has_tools_mismatch(self):
        body = _body(tools=[{"type": "function", "name": "search"}])
        assert _match_rule({"has_tools": False}, "", body, None) is False


class TestMatchRuleMessageCount:
    def test_within_range(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        body = _body(msgs=msgs)
        assert _match_rule({"message_count": {"min": 1, "max": 5}}, "", body, None) is True

    def test_below_min(self):
        msgs = [{"role": "user", "content": "hi"}]
        body = _body(msgs=msgs)
        assert _match_rule({"message_count": {"min": 3}}, "", body, None) is False

    def test_above_max(self):
        msgs = [{"role": "user", "content": "hi"}] * 6
        body = _body(msgs=msgs)
        assert _match_rule({"message_count": {"max": 2}}, "", body, None) is False

    def test_only_max_set(self):
        msgs = [{"role": "user", "content": "x"}]
        body = _body(msgs=msgs)
        assert _match_rule({"message_count": {"max": 3}}, "", body, None) is True


class TestMatchRuleConversationId:
    def test_conv_id_regex_match(self):
        body = _body(**{"conversation_id": "proj-abc123"})
        assert _match_rule({"conversation_id": "^proj-"}, "", body, None) is True

    def test_conv_id_regex_no_match(self):
        body = _body(**{"conversation_id": "xyz-999"})
        assert _match_rule({"conversation_id": "^proj-"}, "", body, None) is False

    def test_conv_id_invalid_regex(self):
        body = _body(**{"conversation_id": "abc"})
        assert _match_rule({"conversation_id": "["}, "", body, None) is False


class TestMatchRuleMultipleConditions:
    def test_all_conditions_must_match(self):
        body = _body(model="qwen3:8b")
        assert _match_rule(
            {"message": "code", "model": "qwen3:*"},
            "write some code",
            body,
            None,
        ) is True

    def test_one_fails_returns_false(self):
        body = _body(model="llama3.2")
        assert _match_rule(
            {"message": "code", "model": "qwen3:*"},
            "write some code",
            body,
            None,
        ) is False


# ── _apply_action ──────────────────────────────────────────────────────────────

class TestApplyActionRouting:
    def test_sets_model(self):
        body = _body()
        result = _apply_action(body, {"model": "qwen3:14b"})
        assert result["model"] == "qwen3:14b"

    def test_sets_force_backend(self):
        body = _body()
        result = _apply_action(body, {"backend": "openrouter"})
        assert result[BB_FORCE_BACKEND] == "openrouter"

    def test_route_resolved_from_routes(self):
        body = _body()
        routes = {"complex": {"model": "qwen3:14b"}, "simple": {"model": "llama3.2:3b"}}
        result = _apply_action(body, {"route": "complex"}, routes=routes)
        assert result["model"] == "qwen3:14b"

    def test_route_string_value_resolved(self):
        body = _body()
        routes = {"fast": "llama3.2:3b"}
        result = _apply_action(body, {"route": "fast"}, routes=routes)
        assert result["model"] == "llama3.2:3b"

    def test_route_missing_warns_and_skips(self):
        body = _body()
        result = _apply_action(body, {"route": "nonexistent"}, routes={})
        assert result["model"] == "llama3.2"  # unchanged

    def test_tools_appended_to_bb_forced_tools(self):
        body = _body()
        result = _apply_action(body, {"tools": ["web_search", "memory"]})
        assert result[BB_FORCED_TOOLS] == ["web_search", "memory"]

    def test_tools_merged_no_duplicates(self):
        body = _body(**{BB_FORCED_TOOLS: ["memory"]})
        result = _apply_action(body, {"tools": ["web_search", "memory"]})
        assert result[BB_FORCED_TOOLS] == ["memory", "web_search"]


class TestApplyActionGenParams:
    def test_temperature(self):
        result = _apply_action(_body(), {"temperature": 0.1})
        assert result["temperature"] == pytest.approx(0.1)

    def test_all_gen_params(self):
        action = {
            "temperature": 0.5,
            "top_p": 0.9,
            "top_k": 40,
            "num_ctx": 8192,
            "max_tokens": 1024,
            "repeat_penalty": 1.1,
            "seed": 42,
        }
        result = _apply_action(_body(), action)
        for k, v in action.items():
            assert result[k] == v

    def test_missing_gen_param_not_set(self):
        result = _apply_action(_body(), {})
        assert "temperature" not in result


class TestApplyActionContextInjection:
    def test_system_prompt_prepended(self):
        body = _body()
        result = _apply_action(body, {"system_prompt": "Be concise."})
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "Be concise."

    def test_system_prompt_prepends_existing_system(self):
        msgs = [{"role": "system", "content": "Original."}, {"role": "user", "content": "Hi"}]
        body = _body(msgs=msgs)
        result = _apply_action(body, {"system_prompt": "Injected."})
        assert result["messages"][0]["content"].startswith("Injected.")
        assert "Original." in result["messages"][0]["content"]

    def test_inject_context_inline(self):
        body = _body()
        result = _apply_action(body, {"inject_context": "Context here."})
        assert "Context here." in result["messages"][0]["content"]

    def test_inject_file_reads_file(self, tmp_path):
        f = tmp_path / "ctx.md"
        f.write_text("File content.")
        body = _body()
        result = _apply_action(body, {"inject_file": str(f)})
        assert "File content." in result["messages"][0]["content"]

    def test_inject_file_missing_skips_gracefully(self):
        body = _body()
        result = _apply_action(body, {"inject_file": "/nonexistent/file.md"})
        # No system message added, no crash
        assert result["messages"][0]["role"] == "user"

    def test_inject_file_and_context_combined(self, tmp_path):
        f = tmp_path / "ctx.md"
        f.write_text("From file.")
        body = _body()
        result = _apply_action(body, {"inject_file": str(f), "inject_context": "Inline."})
        content = result["messages"][0]["content"]
        assert "From file." in content
        assert "Inline." in content

    def test_tag_set(self):
        result = _apply_action(_body(), {"tag": "my-rule"})
        assert result[BB_RULE_TAG] == "my-rule"

    def test_skip_semantic_cache_flag(self):
        result = _apply_action(_body(), {"skip_semantic_cache": True})
        assert result[BB_SKIP_SEMANTIC_CACHE] is True

    def test_skip_semantic_cache_not_set_by_default(self):
        result = _apply_action(_body(), {})
        assert BB_SKIP_SEMANTIC_CACHE not in result


# ── _prepend_system_message ───────────────────────────────────────────────────

class TestPrependSystemMessage:
    def test_creates_system_message_if_absent(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        result = _prepend_system_message(body, "Be helpful.")
        assert result["messages"][0] == {"role": "system", "content": "Be helpful."}

    def test_prepends_to_existing_system(self):
        body = {"messages": [{"role": "system", "content": "Base."}]}
        result = _prepend_system_message(body, "Prefix.")
        assert result["messages"][0]["content"] == "Prefix.\n\nBase."

    def test_original_messages_not_mutated(self):
        msgs = [{"role": "user", "content": "hi"}]
        body = {"messages": msgs}
        _prepend_system_message(body, "New.")
        assert msgs[0]["role"] == "user"  # original list unchanged


# ── evaluate_routing_rules ────────────────────────────────────────────────────

class TestEvaluateRoutingRules:
    def test_no_rules_returns_unchanged(self):
        body = _body()
        original_model = body["model"]
        result_body, matched, skip_sc, pass_through = evaluate_routing_rules([], body)
        assert matched == []
        assert result_body["model"] == original_model
        assert skip_sc is False
        assert pass_through is False

    def test_first_match_wins(self):
        rules = [
            {"name": "first",  "match": {"message": "hello"}, "action": {"model": "qwen3:8b"}},
            {"name": "second", "match": {"message": "hello"}, "action": {"model": "llama3.2"}},
        ]
        body = _body(msg="hello")
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["first"]
        assert result_body["model"] == "qwen3:8b"

    def test_continue_true_layers_both_rules(self):
        rules = [
            {"name": "first",  "match": {}, "action": {"model": "qwen3:8b"}, "continue": True},
            {"name": "second", "match": {}, "action": {"temperature": 0.1}},
        ]
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["first", "second"]
        assert result_body["model"] == "qwen3:8b"
        assert result_body["temperature"] == pytest.approx(0.1)

    def test_no_match_returns_unchanged_body(self):
        rules = [{"match": {"message": "^deploy"}, "action": {"model": "qwen3:14b"}}]
        body = _body(msg="fix the bug")
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == []
        assert result_body["model"] == "llama3.2"

    def test_priority_ordering(self):
        rules = [
            {"name": "low",  "priority": 100, "match": {}, "action": {"model": "low-model"}},
            {"name": "high", "priority": 1,   "match": {}, "action": {"model": "high-model"}},
        ]
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["high"]
        assert result_body["model"] == "high-model"

    def test_default_priority_50(self):
        rules = [
            {"name": "explicit-40", "priority": 40, "match": {}, "action": {"model": "m40"}},
            {"name": "default",                      "match": {}, "action": {"model": "mdefault"}},
        ]
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["explicit-40"]
        assert result_body["model"] == "m40"

    def test_auth_key_stripped_from_body(self):
        body = _body(**{BB_AUTH_KEY: "admin-key"})
        result_body, _, _, _ = evaluate_routing_rules([], body)
        assert BB_AUTH_KEY not in result_body

    def test_auth_key_used_for_matching(self):
        rules = [{"name": "r", "match": {"auth_key": "admin"}, "action": {"model": "special"}}]
        body = _body(**{BB_AUTH_KEY: "admin"})
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["r"]
        assert result_body["model"] == "special"

    def test_skip_session_cache_propagated(self):
        rules = [{"match": {}, "action": {"model": "m", "skip_session_cache": True}}]
        body = _body()
        _, _, skip_sc, _ = evaluate_routing_rules(rules, body)
        assert skip_sc is True

    def test_pass_through_propagated(self):
        rules = [{"match": {}, "action": {"inject_context": "ctx", "pass_through": True}}]
        body = _body()
        _, _, _, pass_through = evaluate_routing_rules(rules, body)
        assert pass_through is True

    def test_invalid_rule_skipped(self):
        rules = ["not-a-dict", None, {"match": {}, "action": {"model": "valid"}}]
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["<unnamed>"]
        assert result_body["model"] == "valid"

    def test_unnamed_rule_gets_default_name(self):
        rules = [{"match": {}, "action": {"model": "x"}}]
        body = _body()
        _, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["<unnamed>"]

    def test_routes_passed_through_to_apply(self):
        rules = [{"match": {}, "action": {"route": "complex"}}]
        routes = {"complex": {"model": "qwen3:14b"}}
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body, routes=routes)
        assert result_body["model"] == "qwen3:14b"
        assert matched != []

    def test_skip_sem_cache_written_to_body(self):
        rules = [{"match": {}, "action": {"skip_semantic_cache": True}}]
        body = _body()
        result_body, _, _, _ = evaluate_routing_rules(rules, body)
        assert result_body.get(BB_SKIP_SEMANTIC_CACHE) is True

    def test_force_backend_written_to_body(self):
        rules = [{"match": {}, "action": {"backend": "openrouter"}}]
        body = _body()
        result_body, _, _, _ = evaluate_routing_rules(rules, body)
        assert result_body.get(BB_FORCE_BACKEND) == "openrouter"

    def test_inject_file_hot_read(self, tmp_path):
        f = tmp_path / "context.md"
        f.write_text("Version 1")
        rules = [{"match": {}, "action": {"inject_file": str(f)}}]

        body = _body()
        result1, _, _, _ = evaluate_routing_rules(rules, body)
        assert "Version 1" in result1["messages"][0]["content"]

        f.write_text("Version 2")
        body2 = _body()
        result2, _, _, _ = evaluate_routing_rules(rules, body2)
        assert "Version 2" in result2["messages"][0]["content"]

    def test_continue_false_stops_after_first(self):
        rules = [
            {"name": "a", "match": {}, "action": {"model": "first"},  "continue": False},
            {"name": "b", "match": {}, "action": {"model": "second"}, "continue": False},
        ]
        body = _body()
        result_body, matched, _, _ = evaluate_routing_rules(rules, body)
        assert matched == ["a"]
        assert result_body["model"] == "first"
