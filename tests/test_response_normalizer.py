"""Tests for the response normalizer.

Contract: every function is total — never raises on malformed input —
and always returns a populated Normalized* dataclass with errors describing
what was missing or malformed.
"""
from beigebox.response_normalizer import (
    NormalizedDelta,
    NormalizedResponse,
    NormalizedUsage,
    coerce_content_to_string,
    estimate_tokens,
    normalize_response,
    normalize_stream_delta,
    normalize_stream_line,
)


# ---------------------------------------------------------------------------
# coerce_content_to_string
# ---------------------------------------------------------------------------


def test_coerce_none_to_empty_string():
    assert coerce_content_to_string(None) == ""


def test_coerce_string_passthrough():
    assert coerce_content_to_string("hello") == "hello"


def test_coerce_text_parts_joined():
    parts = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
    assert coerce_content_to_string(parts) == "hello world"


def test_coerce_image_part_skipped():
    parts = [
        {"type": "text", "text": "describe: "},
        {"type": "image_url", "image_url": {"url": "http://x"}},
    ]
    assert coerce_content_to_string(parts) == "describe: "


def test_coerce_dict_serialized_as_json():
    out = coerce_content_to_string({"k": "v"})
    assert "k" in out and "v" in out


def test_coerce_unhashable_dict_falls_back_to_str():
    class Weird:
        def __repr__(self):
            return "<weird>"
    out = coerce_content_to_string({"k": Weird()})
    # json.dumps fails on Weird → str() fallback engages
    assert "weird" in out.lower()


def test_coerce_list_of_strings():
    assert coerce_content_to_string(["a", "b", "c"]) == "abc"


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_none_zero():
    assert estimate_tokens(None) == 0


def test_estimate_empty_string_zero():
    assert estimate_tokens("") == 0


def test_estimate_short_string_min_one():
    assert estimate_tokens("a") == 1


def test_estimate_string_chars_per_4():
    assert estimate_tokens("a" * 16) == 4


def test_estimate_messages_list_sums_per_message():
    msgs = [
        {"role": "user", "content": "a" * 16},
        {"role": "assistant", "content": "b" * 8},
    ]
    assert estimate_tokens(msgs) == 4 + 2


def test_estimate_content_parts_list():
    parts = [{"type": "text", "text": "a" * 12}]
    assert estimate_tokens(parts) == 3


def test_estimate_dict_via_json():
    # Any dict is json-serialized then estimated; non-zero result.
    assert estimate_tokens({"a": "b"}) > 0


def test_estimate_messages_with_none_content_zero():
    # An assistant tool-call message has content=None.
    assert estimate_tokens([{"role": "assistant", "content": None}]) == 0


# ---------------------------------------------------------------------------
# normalize_response — happy paths
# ---------------------------------------------------------------------------


def _resp(message: dict, *, usage: dict | None = None, **extra) -> dict:
    out = {"choices": [{"message": message, "finish_reason": "stop"}]}
    if usage is not None:
        out["usage"] = usage
    out.update(extra)
    return out


def test_normalize_basic_assistant():
    nr = normalize_response(_resp(
        {"role": "assistant", "content": "hi"},
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    ))
    assert nr.content == "hi"
    assert nr.reasoning is None
    assert nr.tool_calls is None
    assert nr.finish_reason == "stop"
    assert nr.role == "assistant"
    assert nr.errors == []


def test_normalize_vision_content_array():
    parts = [{"type": "text", "text": "x"}, {"type": "image_url", "image_url": {"url": "u"}}]
    nr = normalize_response(_resp({"role": "assistant", "content": parts}))
    assert nr.content == "x"


def test_normalize_reasoning_content_extracted():
    nr = normalize_response(
        _resp({"role": "assistant", "content": None, "reasoning_content": "deep thought"})
    )
    assert nr.content == ""
    assert nr.reasoning == "deep thought"


def test_normalize_reasoning_alias_thinking():
    nr = normalize_response(
        _resp({"role": "assistant", "content": "answer", "thinking": "chain"})
    )
    assert nr.content == "answer"
    assert nr.reasoning == "chain"


def test_normalize_tool_only_response():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    }
    nr = normalize_response(_resp(msg))
    assert nr.content == ""
    assert nr.tool_calls is not None and len(nr.tool_calls) == 1


def test_normalize_empty_tool_calls_becomes_none():
    msg = {"role": "assistant", "content": "ok", "tool_calls": []}
    nr = normalize_response(_resp(msg))
    assert nr.tool_calls is None


# ---------------------------------------------------------------------------
# normalize_response — usage + cost
# ---------------------------------------------------------------------------


def test_normalize_usage_total_computed_when_missing():
    nr = normalize_response(
        _resp({"role": "assistant", "content": "x"}, usage={"prompt_tokens": 5, "completion_tokens": 3})
    )
    assert nr.usage.total_tokens == 8


def test_normalize_reasoning_tokens_from_completion_details():
    usage = {
        "prompt_tokens": 1, "completion_tokens": 4, "total_tokens": 5,
        "completion_tokens_details": {"reasoning_tokens": 2},
    }
    nr = normalize_response(_resp({"role": "assistant", "content": "x"}, usage=usage))
    assert nr.usage.reasoning_tokens == 2


def test_normalize_reasoning_tokens_flat_fallback():
    usage = {"prompt_tokens": 1, "completion_tokens": 4, "reasoning_tokens": 9}
    nr = normalize_response(_resp({"role": "assistant", "content": "x"}, usage=usage))
    assert nr.usage.reasoning_tokens == 9


def test_normalize_cost_top_level():
    nr = normalize_response(_resp({"role": "assistant", "content": "x"}, cost_usd=0.0042))
    assert nr.cost_usd == 0.0042


def test_normalize_cost_nested_string_coerced():
    usage = {"cost": "0.0001"}
    nr = normalize_response(_resp({"role": "assistant", "content": "x"}, usage=usage))
    assert nr.cost_usd == 0.0001


def test_normalize_no_usage_records_error():
    nr = normalize_response({"choices": [{"message": {"role": "assistant", "content": "x"}}]})
    assert "no_usage" in nr.errors
    assert nr.usage == NormalizedUsage()


# ---------------------------------------------------------------------------
# normalize_response — malformed inputs (the "never raises" contract)
# ---------------------------------------------------------------------------


def test_normalize_none_returns_empty_with_error():
    nr = normalize_response(None)
    assert isinstance(nr, NormalizedResponse)
    assert nr.content == ""
    assert "not_a_dict" in nr.errors


def test_normalize_wrong_type_returns_empty_with_error():
    nr = normalize_response("nope")  # type: ignore[arg-type]
    assert "not_a_dict" in nr.errors


def test_normalize_missing_choices():
    nr = normalize_response({})
    assert "no_choices" in nr.errors
    assert nr.content == ""


def test_normalize_empty_choices_list():
    nr = normalize_response({"choices": []})
    assert "no_choices" in nr.errors


def test_normalize_choice_falls_back_to_delta():
    # Some providers ship the final chunk with `delta` instead of `message`.
    data = {"choices": [{"delta": {"content": "streaming-finalize"}, "finish_reason": "stop"}]}
    nr = normalize_response(data)
    assert nr.content == "streaming-finalize"


def test_normalize_message_missing_records_error():
    nr = normalize_response({"choices": [{"finish_reason": "stop"}]})
    assert "no_message" in nr.errors


# ---------------------------------------------------------------------------
# normalize_stream_delta
# ---------------------------------------------------------------------------


def test_stream_delta_content():
    nd = normalize_stream_delta({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]})
    assert nd.content_delta == "hi"
    assert nd.reasoning_delta == ""
    assert nd.is_final is False


def test_stream_delta_reasoning_aliases():
    for k in ("reasoning_content", "reasoning", "thinking"):
        nd = normalize_stream_delta({"choices": [{"delta": {k: "t"}}]})
        assert nd.reasoning_delta == "t", f"alias {k} should map to reasoning_delta"


def test_stream_delta_tool_calls():
    chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c"}]}}]}
    nd = normalize_stream_delta(chunk)
    assert nd.tool_calls_delta is not None


def test_stream_delta_finish_marks_final():
    nd = normalize_stream_delta({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    assert nd.is_final is True


def test_stream_delta_malformed_input():
    nd = normalize_stream_delta(None)  # type: ignore[arg-type]
    assert isinstance(nd, NormalizedDelta)
    assert "not_a_dict" in nd.errors


def test_stream_delta_no_choices():
    nd = normalize_stream_delta({})
    assert "no_choices" in nd.errors


# ---------------------------------------------------------------------------
# normalize_stream_line
# ---------------------------------------------------------------------------


def test_stream_line_done_sentinel():
    nd = normalize_stream_line("data: [DONE]")
    assert nd is not None
    assert nd.is_final is True


def test_stream_line_data_payload():
    nd = normalize_stream_line('data: {"choices":[{"delta":{"content":"x"}}]}')
    assert nd is not None
    assert nd.content_delta == "x"


def test_stream_line_non_data_returns_none():
    for line in ("", "  ", ":heartbeat", "event: foo", "id: 1"):
        assert normalize_stream_line(line) is None


def test_stream_line_malformed_json_returns_empty_delta():
    nd = normalize_stream_line("data: {not json")
    assert nd is not None
    assert nd.content_delta == ""
    assert "json_decode_error" in nd.errors


def test_stream_line_non_string_returns_none():
    assert normalize_stream_line(None) is None  # type: ignore[arg-type]
    assert normalize_stream_line(42) is None  # type: ignore[arg-type]
