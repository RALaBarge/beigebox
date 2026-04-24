"""Tests for the request normalizer.

Covers detection helpers, individual rule constructors, the public
`normalize_request` entry point, profile resolution, and the never-raises
contract for malformed input.
"""
import pytest

from beigebox.request_normalizer import (
    DEFAULT_PROFILES,
    NormalizedRequest,
    TargetProfile,
    canonicalize_tool_choice_rule,
    canonicalize_tool_messages_rule,
    canonicalize_tools_rule,
    coerce_messages_rule,
    collapse_system_messages_rule,
    drop_keys_rule,
    drop_tools_rule,
    is_reasoning_model,
    normalize_request,
    register_profile,
    rename_key_rule,
    set_nested_default_rule,
    strip_message_fields_rule,
)


# ---------------------------------------------------------------------------
# is_reasoning_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [
    "o1-preview", "o1-mini", "o3-mini", "o4-mini",
    "gpt-5-thinking", "deepseek-r1", "deepseek-reasoner",
    "qwq-32b", "trinity-thinking", "claude-sonnet-thinking",
])
def test_is_reasoning_model_positive(model):
    assert is_reasoning_model(model) is True


@pytest.mark.parametrize("model", [
    "gpt-4o", "claude-3.5-sonnet", "llama3.2:3b", "", None, 42,
])
def test_is_reasoning_model_negative(model):
    assert is_reasoning_model(model) is False


def test_is_reasoning_model_custom_markers():
    assert is_reasoning_model("custom-thinker", markers=["thinker"]) is True
    # default markers don't include "thinker"
    assert is_reasoning_model("custom-thinker") is False


# ---------------------------------------------------------------------------
# coerce_messages_rule
# ---------------------------------------------------------------------------


def _apply(rule, body):
    transforms: list[str] = []
    return rule(body, transforms), transforms


def test_coerce_messages_none_to_empty():
    out, t = _apply(coerce_messages_rule(), {"messages": None})
    assert out["messages"] == []


def test_coerce_messages_dict_wrapped():
    out, t = _apply(coerce_messages_rule(), {"messages": {"role": "user", "content": "hi"}})
    assert out["messages"] == [{"role": "user", "content": "hi"}]
    assert "messages_wrapped_to_list" in t


def test_coerce_messages_drops_non_dicts():
    out, t = _apply(coerce_messages_rule(), {"messages": [{"role": "user", "content": "x"}, "junk", 42]})
    assert len(out["messages"]) == 1
    assert any(s.startswith("dropped_non_dict_messages:") for s in t)


def test_coerce_messages_defaults_missing_role():
    out, t = _apply(coerce_messages_rule(), {"messages": [{"content": "x"}]})
    assert out["messages"][0]["role"] == "user"
    assert any(s.startswith("defaulted_missing_role:") for s in t)


def test_coerce_messages_non_list_replaced():
    out, t = _apply(coerce_messages_rule(), {"messages": "nope"})
    assert out["messages"] == []
    assert any("messages_replaced_with_empty" in s for s in t)


# ---------------------------------------------------------------------------
# strip_message_fields_rule
# ---------------------------------------------------------------------------


def test_strip_reasoning_from_echoed_messages():
    rule = strip_message_fields_rule(("reasoning_content", "reasoning", "thinking"))
    body = {"messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "reasoning_content": "thought"},
    ]}
    out, t = _apply(rule, body)
    assert "reasoning_content" not in out["messages"][1]
    assert any(s.startswith("stripped_message_fields:") for s in t)


def test_strip_noop_when_absent():
    rule = strip_message_fields_rule(("reasoning",))
    body = {"messages": [{"role": "user", "content": "q"}]}
    out, t = _apply(rule, body)
    assert out == body
    assert t == []


# ---------------------------------------------------------------------------
# collapse_system_messages_rule
# ---------------------------------------------------------------------------


def test_collapse_concat_merges_text():
    body = {"messages": [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": "B"},
    ]}
    out, t = _apply(collapse_system_messages_rule("concat"), body)
    sys_msgs = [m for m in out["messages"] if m["role"] == "system"]
    assert len(sys_msgs) == 1
    assert sys_msgs[0]["content"] == "A\n\nB"
    assert any("merged_system_messages" in s for s in t)


def test_collapse_first_keeps_first_drops_rest():
    body = {"messages": [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "q"},
    ]}
    out, t = _apply(collapse_system_messages_rule("first"), body)
    sys_msgs = [m for m in out["messages"] if m["role"] == "system"]
    assert len(sys_msgs) == 1
    assert sys_msgs[0]["content"] == "A"


def test_collapse_noop_with_one_system():
    body = {"messages": [{"role": "system", "content": "A"}, {"role": "user", "content": "q"}]}
    out, _ = _apply(collapse_system_messages_rule("concat"), body)
    assert out is body


def test_collapse_unknown_mode_noop():
    body = {"messages": [{"role": "system", "content": "A"}, {"role": "system", "content": "B"}]}
    out, _ = _apply(collapse_system_messages_rule("nope"), body)
    assert out is body


# ---------------------------------------------------------------------------
# drop_keys_rule / rename_key_rule / set_nested_default_rule
# ---------------------------------------------------------------------------


def test_drop_keys_removes_listed():
    body = {"a": 1, "b": 2, "c": 3}
    out, t = _apply(drop_keys_rule(["a", "c"], reason="test"), body)
    assert out == {"b": 2}
    assert any(s.startswith("dropped:test:") for s in t)


def test_drop_keys_noop_when_absent():
    out, t = _apply(drop_keys_rule(["x"]), {"a": 1})
    assert t == []


def test_rename_key_basic():
    out, t = _apply(rename_key_rule("max_tokens", "max_completion_tokens"), {"max_tokens": 10})
    assert out == {"max_completion_tokens": 10}
    assert "renamed:max_tokens->max_completion_tokens" in t


def test_rename_key_conflict_prefer_new():
    out, t = _apply(
        rename_key_rule("max_tokens", "max_completion_tokens"),
        {"max_tokens": 10, "max_completion_tokens": 20},
    )
    assert out == {"max_completion_tokens": 20}
    assert any("superseded_by" in s for s in t)


def test_rename_key_conflict_prefer_old():
    out, _ = _apply(
        rename_key_rule("max_tokens", "max_completion_tokens", on_conflict="prefer_old"),
        {"max_tokens": 10, "max_completion_tokens": 20},
    )
    assert out == {"max_completion_tokens": 10}


def test_rename_key_conflict_skip():
    body = {"max_tokens": 10, "max_completion_tokens": 20}
    out, t = _apply(rename_key_rule("max_tokens", "max_completion_tokens", on_conflict="skip"), body)
    assert out == body


def test_set_nested_default_creates_path():
    out, t = _apply(
        set_nested_default_rule(("stream_options", "include_usage"), True),
        {"stream": True},
    )
    assert out["stream_options"]["include_usage"] is True
    assert any("set:stream_options.include_usage" in s for s in t)


def test_set_nested_default_only_if_gates():
    rule = set_nested_default_rule(
        ("stream_options", "include_usage"),
        True,
        only_if=lambda b: bool(b.get("stream")),
    )
    out, t = _apply(rule, {"stream": False})
    assert "stream_options" not in out
    assert t == []


def test_set_nested_default_idempotent():
    body = {"stream_options": {"include_usage": True}}
    out, t = _apply(set_nested_default_rule(("stream_options", "include_usage"), True), body)
    assert t == []  # already set, no transform


# ---------------------------------------------------------------------------
# Tool input rules
# ---------------------------------------------------------------------------


def test_canonicalize_tools_fills_type():
    body = {"tools": [{"function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}]}
    out, t = _apply(canonicalize_tools_rule(), body)
    assert out["tools"][0]["type"] == "function"
    assert "canonicalized_tools" in t


def test_canonicalize_tools_drops_invalid():
    body = {"tools": [
        {"function": {"name": "f", "description": "", "parameters": {}}},
        "garbage",
        {"function": {"description": "no name"}},
    ]}
    out, t = _apply(canonicalize_tools_rule(), body)
    assert len(out["tools"]) == 1
    assert any(s.startswith("dropped_invalid_tools:") for s in t)


def test_canonicalize_tools_drops_key_when_empty_after_cleanup():
    body = {"tools": [{"function": {}}, "junk"]}
    out, t = _apply(canonicalize_tools_rule(), body)
    assert "tools" not in out


def test_canonicalize_tools_not_a_list_drops_key():
    out, t = _apply(canonicalize_tools_rule(), {"tools": "lol"})
    assert "tools" not in out
    assert "dropped:tools(not_a_list)" in t


def test_canonicalize_tool_choice_valid_string():
    for v in ("auto", "required", "none"):
        out, _ = _apply(canonicalize_tool_choice_rule(), {"tool_choice": v})
        assert out["tool_choice"] == v


def test_canonicalize_tool_choice_valid_dict():
    body = {"tool_choice": {"type": "function", "function": {"name": "f"}}}
    out, _ = _apply(canonicalize_tool_choice_rule(), body)
    assert out["tool_choice"] == body["tool_choice"]


def test_canonicalize_tool_choice_drops_garbage():
    out, t = _apply(canonicalize_tool_choice_rule(), {"tool_choice": {"foo": 1}})
    assert "tool_choice" not in out


def test_tool_messages_synthesize_missing_id_and_string_args():
    body = {"messages": [
        {"role": "user", "content": "q"},
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "f", "arguments": {"x": 1}}},
        ]},
    ]}
    out, t = _apply(canonicalize_tool_messages_rule(), body)
    tc = out["messages"][1]["tool_calls"][0]
    assert tc["id"]  # synthesized
    assert tc["type"] == "function"
    assert tc["function"]["arguments"] == '{"x": 1}'
    assert any("synthesized_tool_call_ids" in s for s in t)
    assert any("coerced_tool_arguments_to_string" in s for s in t)


def test_tool_messages_drop_when_missing_tool_call_id():
    body = {"messages": [
        {"role": "tool", "content": "result"},  # missing tool_call_id
        {"role": "user", "content": "q"},
    ]}
    out, t = _apply(canonicalize_tool_messages_rule(), body)
    assert all(m["role"] != "tool" for m in out["messages"])
    assert any("dropped_tool_messages_missing_id" in s for s in t)


def test_tool_messages_coerce_dict_result_to_string():
    body = {"messages": [
        {"role": "tool", "tool_call_id": "abc", "content": {"result": 42}},
    ]}
    out, t = _apply(canonicalize_tool_messages_rule(), body)
    assert out["messages"][0]["content"] == '{"result": 42}'
    assert any("coerced_tool_result_content_to_string" in s for s in t)


def test_drop_tools_rule_strips_keys():
    body = {"tools": [{}], "tool_choice": "auto", "model": "x"}
    out, t = _apply(drop_tools_rule("custom_reason"), body)
    assert "tools" not in out
    assert "tool_choice" not in out
    assert any(s.startswith("dropped:custom_reason:") for s in t)


# ---------------------------------------------------------------------------
# normalize_request — public entry point
# ---------------------------------------------------------------------------


def test_normalize_none_body_returns_empty_with_error():
    nr = normalize_request(None)
    assert isinstance(nr, NormalizedRequest)
    assert nr.body == {}
    assert "not_a_dict" in nr.errors


def test_normalize_autodetect_o_series_upgrades_target():
    nr = normalize_request(
        {"model": "o3-mini", "messages": [{"role": "user", "content": "hi"}],
         "max_tokens": 100, "temperature": 0.7},
        target="openai_compat",
    )
    assert nr.target == "openai_reasoning"
    assert "max_completion_tokens" in nr.body
    assert "max_tokens" not in nr.body
    assert "temperature" not in nr.body
    assert any("target_resolved" in s for s in nr.transforms)


def test_normalize_openai_reasoning_drops_sampling_params():
    nr = normalize_request(
        {"model": "o3-mini", "temperature": 0.5, "top_p": 0.9, "max_tokens": 50,
         "messages": [{"role": "user", "content": "x"}]},
        target="openai_reasoning",
    )
    assert "temperature" not in nr.body
    assert "top_p" not in nr.body
    assert nr.body["max_completion_tokens"] == 50


def test_normalize_openrouter_streaming_sets_include_usage():
    nr = normalize_request(
        {"model": "anthropic/claude-3.5-sonnet", "stream": True,
         "messages": [{"role": "user", "content": "x"}]},
        target="openrouter",
    )
    assert nr.body["stream_options"]["include_usage"] is True


def test_normalize_openrouter_non_streaming_no_stream_options():
    nr = normalize_request(
        {"model": "anthropic/claude-3.5-sonnet",
         "messages": [{"role": "user", "content": "x"}]},
        target="openrouter",
    )
    assert "stream_options" not in nr.body


def test_normalize_anthropic_keeps_reasoning_fields():
    body = {
        "model": "claude-3.5-sonnet",
        "messages": [
            {"role": "assistant", "content": "a", "thinking": "chain"},
            {"role": "user", "content": "q"},
        ],
    }
    nr = normalize_request(body, target="anthropic")
    # anthropic profile omits the strip rule
    assert nr.body["messages"][0].get("thinking") == "chain"


def test_normalize_unknown_target_falls_back():
    nr = normalize_request({"model": "gpt-4o", "messages": []}, target="invented")
    assert nr.target == "openai_compat"
    assert any("target_unknown" in s for s in nr.transforms)


def test_normalize_target_profile_instance_accepted():
    profile = TargetProfile("strict", [drop_keys_rule(["seed", "n"], reason="strict")])
    nr = normalize_request(
        {"model": "x", "seed": 1, "n": 4, "messages": []},
        target=profile,
    )
    assert nr.target == "strict"
    assert "seed" not in nr.body and "n" not in nr.body


def test_normalize_extra_rules_appended():
    nr = normalize_request(
        {"model": "gpt-4o", "messages": [], "secret": "remove"},
        extra_rules=[drop_keys_rule(["secret"], reason="extra")],
    )
    assert "secret" not in nr.body


def test_normalize_strips_prior_reasoning_by_default():
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1", "reasoning_content": "thought"},
            {"role": "user", "content": "q2"},
        ],
    }
    nr = normalize_request(body)
    assert "reasoning_content" not in nr.body["messages"][1]


def test_normalize_collapses_system_messages_by_default():
    body = {"messages": [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "user", "content": "q"},
    ]}
    nr = normalize_request(body)
    sys_msgs = [m for m in nr.body["messages"] if m["role"] == "system"]
    assert len(sys_msgs) == 1
    assert sys_msgs[0]["content"] == "A\n\nB"


def test_normalize_failing_rule_captured_in_errors():
    def boom(body, transforms):
        raise RuntimeError("kaboom")

    boom.__bb_name__ = "boom_rule"
    profile = TargetProfile("explosive", [boom])
    nr = normalize_request({"model": "x", "messages": []}, target=profile)
    # Pipeline should not crash; error tag should mention the rule name.
    assert any("rule_failed:boom_rule" in e for e in nr.errors)


# ---------------------------------------------------------------------------
# register_profile
# ---------------------------------------------------------------------------


def test_register_profile_into_custom_registry():
    registry: dict[str, TargetProfile] = {}
    profile = TargetProfile("custom", [drop_keys_rule(["foo"])])
    register_profile(profile, registry=registry)
    assert "custom" in registry
    assert registry["custom"] is profile


def test_default_profiles_present():
    for name in ("openai_compat", "openai_reasoning", "openrouter", "ollama", "anthropic"):
        assert name in DEFAULT_PROFILES
