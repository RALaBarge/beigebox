"""Unit tests for beigebox.capture envelope + factories.

Pure-function tests against the dataclasses and ``from_normalized`` /
``from_partial`` factories. No DB, no proxy, no I/O.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from beigebox.capture import (
    CaptureContext,
    CapturedRequest,
    CapturedResponse,
    attach_response_timing,
)
from beigebox.request_normalizer import NormalizedRequest
from beigebox.response_normalizer import NormalizedResponse, NormalizedUsage


def _ctx(**overrides) -> CaptureContext:
    base = dict(
        conv_id="c1",
        turn_id="t1",
        model="x-ai/grok-4.3",
        backend="openrouter",
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return CaptureContext(**base)


# --- CapturedRequest --------------------------------------------------------


class TestCapturedRequestFromNormalized:
    def test_full_body_with_tools_and_stream(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "foo"}}],
            "stream": True,
        }
        nr = NormalizedRequest(
            body=body,
            target="openrouter",
            transforms=["renamed_max_tokens", "stripped_temperature"],
            errors=[],
            raw=body,
        )
        cr = CapturedRequest.from_normalized(nr, _ctx(), body["messages"])

        assert cr.target == "openrouter"
        assert cr.transforms == ["renamed_max_tokens", "stripped_temperature"]
        assert cr.errors == []
        assert cr.has_tools is True
        assert cr.stream is True
        assert cr.messages == [{"role": "user", "content": "hi"}]

    def test_empty_body_no_tools_no_stream(self):
        nr = NormalizedRequest(body={}, target="openai_compat", transforms=[], errors=[], raw={})
        cr = CapturedRequest.from_normalized(nr, _ctx(), [])

        assert cr.has_tools is False
        assert cr.stream is False
        assert cr.messages == []
        assert cr.transforms == []

    def test_transforms_list_is_copied_not_aliased(self):
        transforms = ["a", "b"]
        nr = NormalizedRequest(body={}, target="t", transforms=transforms, errors=[], raw={})
        cr = CapturedRequest.from_normalized(nr, _ctx(), [])

        cr.transforms.append("c")
        assert transforms == ["a", "b"]   # caller's list untouched

    def test_messages_list_is_copied_not_aliased(self):
        msgs = [{"role": "user", "content": "x"}]
        nr = NormalizedRequest(body={}, target="t", transforms=[], errors=[], raw={})
        cr = CapturedRequest.from_normalized(nr, _ctx(), msgs)

        cr.messages.append({"role": "system", "content": "y"})
        assert len(msgs) == 1

    def test_non_dict_body_does_not_crash(self):
        nr = NormalizedRequest(body=None, target="t", transforms=[], errors=["malformed"], raw={})  # type: ignore[arg-type]
        cr = CapturedRequest.from_normalized(nr, _ctx(), [])

        assert cr.has_tools is False
        assert cr.stream is False
        assert cr.errors == ["malformed"]


# --- CapturedResponse.from_normalized ---------------------------------------


class TestCapturedResponseFromNormalized:
    def test_full_response_with_reasoning_and_tools(self):
        nr = NormalizedResponse(
            content="answer text",
            reasoning="step 1\nstep 2",
            tool_calls=[{"id": "tc1", "function": {"name": "search", "arguments": "{}"}}],
            finish_reason="tool_calls",
            role="assistant",
            usage=NormalizedUsage(prompt_tokens=10, completion_tokens=20, reasoning_tokens=5, total_tokens=35),
            cost_usd=0.0042,
            raw={},
            errors=[],
        )
        cr = CapturedResponse.from_normalized(nr, _ctx())

        assert cr.outcome == "ok"
        assert cr.error_kind is None
        assert cr.error_message is None
        assert cr.content == "answer text"
        assert cr.reasoning == "step 1\nstep 2"
        assert cr.tool_calls and cr.tool_calls[0]["id"] == "tc1"
        assert cr.finish_reason == "tool_calls"
        assert cr.prompt_tokens == 10
        assert cr.completion_tokens == 20
        assert cr.reasoning_tokens == 5
        assert cr.total_tokens == 35
        assert cr.cost_usd == 0.0042

    def test_minimal_response_no_reasoning_no_tools(self):
        nr = NormalizedResponse(
            content="ok",
            reasoning=None,
            tool_calls=None,
            finish_reason="stop",
            role="assistant",
            usage=NormalizedUsage(),
            cost_usd=None,
            raw={},
            errors=[],
        )
        cr = CapturedResponse.from_normalized(nr, _ctx())

        assert cr.reasoning is None
        assert cr.tool_calls is None
        assert cr.cost_usd is None
        assert cr.prompt_tokens == 0
        assert cr.total_tokens == 0

    def test_empty_content_string_preserved(self):
        nr = NormalizedResponse(
            content="",
            reasoning="thinking only",
            tool_calls=None,
            finish_reason="stop",
            role="assistant",
            usage=NormalizedUsage(reasoning_tokens=42),
            cost_usd=None,
            raw={},
            errors=[],
        )
        cr = CapturedResponse.from_normalized(nr, _ctx())

        assert cr.content == ""
        assert cr.reasoning == "thinking only"
        assert cr.reasoning_tokens == 42

    def test_response_errors_list_is_copied(self):
        errs = ["missing_usage", "unparseable_choice"]
        nr = NormalizedResponse(
            content="x", reasoning=None, tool_calls=None, finish_reason="stop",
            role="assistant", usage=NormalizedUsage(), cost_usd=None, raw={}, errors=errs,
        )
        cr = CapturedResponse.from_normalized(nr, _ctx())

        cr.response_errors.append("third")
        assert errs == ["missing_usage", "unparseable_choice"]

    def test_role_defaults_to_assistant_when_blank(self):
        nr = NormalizedResponse(
            content="x", reasoning=None, tool_calls=None, finish_reason="stop",
            role="", usage=NormalizedUsage(), cost_usd=None, raw={}, errors=[],
        )
        cr = CapturedResponse.from_normalized(nr, _ctx())
        assert cr.role == "assistant"


# --- CapturedResponse.from_partial ------------------------------------------


class TestCapturedResponseFromPartial:
    def test_upstream_error_no_content(self):
        cr = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="upstream_error",
            content="",
            error=RuntimeError("connection refused"),
            partial_resp=None,
        )
        assert cr.outcome == "upstream_error"
        assert cr.error_kind == "upstream_error"
        assert cr.error_message == "connection refused"
        assert cr.content == ""
        assert cr.finish_reason == "error"
        assert cr.role == "assistant"

    def test_client_disconnect_with_partial_content(self):
        cr = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="client_disconnect",
            content="partial text up to disconnect",
            error=None,
            partial_resp=None,
        )
        assert cr.outcome == "client_disconnect"
        assert cr.error_message is None
        assert cr.content == "partial text up to disconnect"
        assert cr.finish_reason == "aborted"

    def test_stream_aborted_uses_partial_resp_when_provided(self):
        partial = NormalizedResponse(
            content="will be overridden by content arg",
            reasoning="some thinking we got",
            tool_calls=None,
            finish_reason=None,
            role="assistant",
            usage=NormalizedUsage(prompt_tokens=8, completion_tokens=3, total_tokens=11),
            cost_usd=None,
            raw={},
            errors=["truncated"],
        )
        cr = CapturedResponse.from_partial(
            ctx=_ctx(),
            outcome="stream_aborted",
            content="real partial text",
            error=ValueError("upstream 502"),
            partial_resp=partial,
        )
        assert cr.outcome == "stream_aborted"
        assert cr.error_message == "upstream 502"
        assert cr.content == "real partial text"
        assert cr.reasoning == "some thinking we got"
        assert cr.prompt_tokens == 8
        assert cr.response_errors == ["truncated"]
        # finish_reason filled in from outcome since partial.finish_reason was None
        assert cr.finish_reason == "error"

    def test_partial_resp_finish_reason_preserved_when_set(self):
        partial = NormalizedResponse(
            content="", reasoning=None, tool_calls=None, finish_reason="length",
            role="assistant", usage=NormalizedUsage(), cost_usd=None, raw={}, errors=[],
        )
        cr = CapturedResponse.from_partial(
            ctx=_ctx(), outcome="stream_aborted", content="x", partial_resp=partial,
        )
        assert cr.finish_reason == "length"

    def test_outcome_ok_raises(self):
        with pytest.raises(ValueError, match="from_partial"):
            CapturedResponse.from_partial(ctx=_ctx(), outcome="ok", content="x")


# --- attach_response_timing --------------------------------------------------


class TestAttachResponseTiming:
    def test_computes_latency_from_started_to_ended(self):
        start = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(milliseconds=750)
        ctx = _ctx(started_at=start)

        new_ctx = attach_response_timing(ctx, ended_at=end, ttft_ms=120.5, request_id="req-abc")

        assert new_ctx.ended_at == end
        assert new_ctx.latency_ms == pytest.approx(750.0, abs=0.001)
        assert new_ctx.ttft_ms == 120.5
        assert new_ctx.request_id == "req-abc"
        # original untouched
        assert ctx.ended_at is None
        assert ctx.latency_ms is None
        assert ctx.request_id is None

    def test_preserves_identity_fields(self):
        ctx = _ctx(run_id="r1", request_id="existing-id", ttft_ms=80.0)
        new_ctx = attach_response_timing(ctx)

        assert new_ctx.conv_id == ctx.conv_id
        assert new_ctx.turn_id == ctx.turn_id
        assert new_ctx.model == ctx.model
        assert new_ctx.backend == ctx.backend
        assert new_ctx.run_id == "r1"
        # request_id and ttft_ms preserved when not overridden
        assert new_ctx.request_id == "existing-id"
        assert new_ctx.ttft_ms == 80.0

    def test_ended_at_defaults_to_now(self):
        ctx = _ctx()
        new_ctx = attach_response_timing(ctx)
        assert new_ctx.ended_at is not None
        assert new_ctx.latency_ms is not None
        assert new_ctx.latency_ms >= 0
