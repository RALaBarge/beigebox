"""Normalize OpenAI-compatible chat completion responses into a single shape.

One chokepoint for every upstream response BeigeBox sees. Every function in
this module is total: it never raises on malformed input, and always returns
a populated Normalized* dataclass with `errors` describing what was missing
or malformed.

Covers:
- Reasoning models where `message.content` is None and the answer lives in
  `message.reasoning_content` / `message.reasoning` / `message.thinking`
  (Arcee Trinity Thinking, OpenAI o-series, DeepSeek-R1, Claude thinking).
- Tool-only responses (`content: null`, `tool_calls: [...]`).
- Vision content arrays (`content: [{"type": "text", ...}, {"type": "image_url", ...}]`).
- Missing `usage`, missing `choices`, or empty `choices`.
- OpenAI o-series reasoning token accounting
  (`usage.completion_tokens_details.reasoning_tokens`).
- OpenRouter cost extraction (top-level `cost_usd` or `usage.cost`).
- SSE streaming deltas that carry reasoning separately from content.
- Fully malformed input (None, "", wrong types).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# Order matters: first non-empty string wins. Covers OpenRouter, OpenAI o-series,
# DeepSeek-R1, Anthropic thinking, and the Arcee Trinity shape we hit in the wild.
_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "reasoning", "thinking")


@dataclass
class NormalizedUsage:
    """Token accounting with every field defaulting to 0 (never None)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


@dataclass
class NormalizedResponse:
    """Uniform shape for one non-streaming OpenAI-compatible chat completion.

    `content` is guaranteed to be a string (possibly empty). `reasoning` is the
    thinking-model chain-of-thought when present, otherwise None. Callers that
    previously treated `content` as possibly-None should now treat it as
    possibly-empty.
    """

    content: str
    reasoning: str | None
    tool_calls: list | None
    finish_reason: str | None
    role: str
    usage: NormalizedUsage
    cost_usd: float | None
    raw: dict
    errors: list[str] = field(default_factory=list)


@dataclass
class NormalizedDelta:
    """Uniform shape for one streaming SSE chunk (or the [DONE] sentinel).

    Fields mirror NormalizedResponse but represent incremental content; strings
    may be empty when this delta carried no content for that channel.
    """

    content_delta: str
    reasoning_delta: str
    tool_calls_delta: list | None
    finish_reason: str | None
    is_final: bool
    raw: dict
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def coerce_content_to_string(content: Any) -> str:
    """Collapse any OpenAI-style content value into a plain string.

    - None -> ""
    - str -> unchanged
    - list (vision / multipart) -> joined text parts; non-text parts skipped
    - dict / other -> json-serialized, then stringified (best-effort fallback)
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                # Text parts in the OpenAI/Anthropic content-parts shape.
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                # image_url, input_audio, tool_result, etc. are intentionally
                # skipped — we're coercing for text-only consumers (logging,
                # token estimate, embedding).
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    if isinstance(content, dict):
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)
    return str(content)


def estimate_tokens(text: Any) -> int:
    """Safe, shape-tolerant token estimator.

    Replaces proxy.py's local `_estimate_tokens` which crashed on None and
    inflated on vision-style messages lists. Rules:

    - None / "" -> 0
    - str -> max(1, len(text) // 4)
    - list of message dicts (has "role") -> sum over each message's content
    - list of content parts -> extract text parts, then estimate
    - dict -> estimate over json.dumps
    """
    if text is None:
        return 0
    if isinstance(text, str):
        return max(1, len(text) // 4) if text else 0
    if isinstance(text, list):
        if text and isinstance(text[0], dict) and "role" in text[0]:
            total = 0
            for m in text:
                if not isinstance(m, dict):
                    continue
                total += estimate_tokens(coerce_content_to_string(m.get("content")))
            return total
        return estimate_tokens(coerce_content_to_string(text))
    if isinstance(text, dict):
        try:
            return estimate_tokens(json.dumps(text, ensure_ascii=False))
        except (TypeError, ValueError):
            return estimate_tokens(str(text))
    return estimate_tokens(str(text))


# ---------------------------------------------------------------------------
# Internal extraction helpers
# ---------------------------------------------------------------------------


def _first_non_empty_str(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _extract_usage(data: dict, errors: list[str]) -> NormalizedUsage:
    usage_raw = data.get("usage")
    if not isinstance(usage_raw, dict):
        errors.append("no_usage")
        return NormalizedUsage()

    def _int(v: Any) -> int:
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    prompt = _int(usage_raw.get("prompt_tokens"))
    completion = _int(usage_raw.get("completion_tokens"))
    total_raw = usage_raw.get("total_tokens")
    total = _int(total_raw) if total_raw is not None else (prompt + completion)

    # OpenAI o-series / OpenRouter passthrough shape.
    reasoning = 0
    details = usage_raw.get("completion_tokens_details")
    if isinstance(details, dict):
        reasoning = _int(details.get("reasoning_tokens"))
    # Some providers flatten this to the usage top level.
    if not reasoning:
        reasoning = _int(usage_raw.get("reasoning_tokens"))

    return NormalizedUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def _extract_cost(data: dict) -> float | None:
    """Mirror the cost extraction logic from backends/openrouter.py:62-85.

    OpenRouter surfaces cost either at the top level (`cost_usd`) or nested
    under `usage.cost`. Either may be a string.
    """

    def _to_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    top = _to_float(data.get("cost_usd"))
    if top is not None:
        return top
    usage = data.get("usage")
    if isinstance(usage, dict):
        nested = _to_float(usage.get("cost"))
        if nested is not None:
            return nested
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_response(data: dict | None) -> NormalizedResponse:
    """Normalize a full chat-completion response dict. Never raises."""
    errors: list[str] = []

    if not isinstance(data, dict):
        errors.append("not_a_dict")
        return NormalizedResponse(
            content="",
            reasoning=None,
            tool_calls=None,
            finish_reason=None,
            role="assistant",
            usage=NormalizedUsage(),
            cost_usd=None,
            raw={},
            errors=errors,
        )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        errors.append("no_choices")
        return NormalizedResponse(
            content="",
            reasoning=None,
            tool_calls=None,
            finish_reason=None,
            role="assistant",
            usage=_extract_usage(data, errors),
            cost_usd=_extract_cost(data),
            raw=data,
            errors=errors,
        )

    choice = choices[0] if isinstance(choices[0], dict) else {}
    if not choice:
        errors.append("choice_not_dict")

    message = choice.get("message") if isinstance(choice.get("message"), dict) else None
    if message is None:
        # Streaming-ish chunks sometimes land here; try delta as a fallback.
        delta = choice.get("delta")
        if isinstance(delta, dict):
            message = delta
        else:
            errors.append("no_message")
            message = {}

    finish_raw = choice.get("finish_reason")
    finish_reason = finish_raw if isinstance(finish_raw, str) else None

    content = coerce_content_to_string(message.get("content"))
    reasoning = _first_non_empty_str(message, _REASONING_FIELDS)

    tool_calls = message.get("tool_calls")
    if tool_calls is not None and not isinstance(tool_calls, list):
        errors.append("tool_calls_not_list")
        tool_calls = None
    if isinstance(tool_calls, list) and not tool_calls:
        tool_calls = None  # empty list == "no tool calls"

    role_raw = message.get("role")
    role = role_raw if isinstance(role_raw, str) and role_raw else "assistant"

    return NormalizedResponse(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        role=role,
        usage=_extract_usage(data, errors),
        cost_usd=_extract_cost(data),
        raw=data,
        errors=errors,
    )


def normalize_stream_delta(chunk: dict | None) -> NormalizedDelta:
    """Normalize one parsed SSE JSON chunk (the object from `data: {...}`)."""
    errors: list[str] = []

    if not isinstance(chunk, dict):
        errors.append("not_a_dict")
        return NormalizedDelta(
            content_delta="",
            reasoning_delta="",
            tool_calls_delta=None,
            finish_reason=None,
            is_final=False,
            raw={},
            errors=errors,
        )

    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        errors.append("no_choices")
        return NormalizedDelta(
            content_delta="",
            reasoning_delta="",
            tool_calls_delta=None,
            finish_reason=None,
            is_final=False,
            raw=chunk,
            errors=errors,
        )

    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        # Some providers attach final info as `message` on the last chunk
        # instead of `delta`; try that shape too.
        delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        if not delta:
            errors.append("no_delta")

    content_delta = coerce_content_to_string(delta.get("content"))
    # OpenRouter ships reasoning deltas on the "reasoning" field; other
    # providers may use reasoning_content or thinking.
    reasoning_source = (
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or delta.get("thinking")
    )
    reasoning_delta = coerce_content_to_string(reasoning_source)

    tool_calls_delta = delta.get("tool_calls")
    if tool_calls_delta is not None and not isinstance(tool_calls_delta, list):
        errors.append("tool_calls_delta_not_list")
        tool_calls_delta = None

    finish_raw = choice.get("finish_reason")
    finish_reason = finish_raw if isinstance(finish_raw, str) and finish_raw else None

    return NormalizedDelta(
        content_delta=content_delta,
        reasoning_delta=reasoning_delta,
        tool_calls_delta=tool_calls_delta,
        finish_reason=finish_reason,
        is_final=bool(finish_reason),
        raw=chunk,
        errors=errors,
    )


def normalize_stream_line(line: str | None) -> NormalizedDelta | None:
    """Parse + normalize a raw SSE line.

    Returns:
        - None for non-data lines (empty keepalives, `event:`, `id:`, `retry:`).
        - A NormalizedDelta with is_final=True for the `[DONE]` sentinel.
        - A NormalizedDelta for data payloads (malformed JSON produces a
          delta with empty fields and `errors=["json_decode_error"]`).
    """
    if not isinstance(line, str):
        return None
    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None

    payload = stripped[5:].strip()
    if payload == "[DONE]":
        return NormalizedDelta(
            content_delta="",
            reasoning_delta="",
            tool_calls_delta=None,
            finish_reason=None,
            is_final=True,
            raw={},
            errors=[],
        )

    try:
        chunk = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return NormalizedDelta(
            content_delta="",
            reasoning_delta="",
            tool_calls_delta=None,
            finish_reason=None,
            is_final=False,
            raw={},
            errors=["json_decode_error"],
        )

    return normalize_stream_delta(chunk)
