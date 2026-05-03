"""Single chokepoint for request/response telemetry capture.

Every chat-completion that flows through the proxy gets captured **once**,
**completely**, at the normalizer boundary, and fans out from there to
``wire_events`` / ``messages`` / ``vector_store``.

This module owns three small dataclasses (``CaptureContext``,
``CapturedRequest``, ``CapturedResponse``) plus the factories that build them
from ``NormalizedRequest`` / ``NormalizedResponse``. The fan-out class lives
alongside but is intentionally thin — the heavy lifting (normalization,
storage) belongs to the modules it dispatches to.

Design notes:

- Request and response are split into two envelopes so request-only captures
  (e.g. guardrail-rejected before any upstream call) don't need a synthetic
  response. Each gets its own fan-out call.
- Every response — including failures, aborts, and client disconnects — must
  produce a row. The ``from_partial`` factory exists for those paths so
  proxy.py can call it from a ``finally`` block with whatever partial content
  it has accumulated.
- Side-channel events (guardrail blocks, hook blocks, validation warns,
  routing decisions, classifier runs, …) are NOT routed through this module.
  They keep their existing direct ``wire.log()`` calls. This module owns the
  request/response pair, and only that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from beigebox.request_normalizer import NormalizedRequest
from beigebox.response_normalizer import NormalizedResponse


CaptureOutcome = Literal[
    "ok",                 # response normalized successfully
    "upstream_error",     # upstream call raised before/at response
    "stream_aborted",     # mid-stream upstream error
    "client_disconnect",  # caller went away mid-stream (asyncio.CancelledError)
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CaptureContext:
    """Caller-supplied identity + timing.

    Explicit fields, no opaque dict. ``ended_at`` and ``latency_ms`` are
    optional for the request-side capture (no response yet); they get filled
    in on the response side.
    """

    conv_id: str
    turn_id: str
    model: str                         # requested model id
    backend: str                       # resolved backend name
    started_at: datetime               # when the request was received
    run_id: str | None = None
    request_id: str | None = None      # upstream provider's request id
    ended_at: datetime | None = None
    ttft_ms: float | None = None
    latency_ms: float | None = None
    user_id: str | None = None         # carried through for HMAC integrity signing


@dataclass
class CapturedRequest:
    """One outgoing chat completion request, captured at the normalizer."""

    ctx: CaptureContext
    target: str
    transforms: list[str]
    errors: list[str]
    messages: list[dict]               # the user/system/assistant turns sent in
    has_tools: bool
    stream: bool

    @classmethod
    def from_normalized(
        cls,
        nr: NormalizedRequest,
        ctx: CaptureContext,
        messages: list[dict],
    ) -> "CapturedRequest":
        body = nr.body if isinstance(nr.body, dict) else {}
        return cls(
            ctx=ctx,
            target=nr.target,
            transforms=list(nr.transforms),
            errors=list(nr.errors),
            messages=list(messages),
            has_tools=bool(body.get("tools")),
            stream=bool(body.get("stream")),
        )


@dataclass
class CapturedResponse:
    """One assistant response captured at the normalizer.

    The ``outcome`` field decides which downstream paths run. Successful
    captures (``outcome="ok"``) get the full normalizer fields and are
    eligible for vector embedding. Error/abort/disconnect captures still
    produce a row, but with whatever partial data was assembled — and
    ``error_kind`` / ``error_message`` set so consumers can tell the
    failure class apart.
    """

    ctx: CaptureContext
    outcome: CaptureOutcome
    error_kind: str | None
    error_message: str | None

    role: str                          # always "assistant" for table consistency
    content: str                       # accumulated text (possibly empty on failure)
    reasoning: str | None
    tool_calls: list | None
    finish_reason: str | None
    response_errors: list[str]         # NormalizedResponse.errors

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None

    @classmethod
    def from_normalized(
        cls,
        nr: NormalizedResponse,
        ctx: CaptureContext,
        outcome: CaptureOutcome = "ok",
    ) -> "CapturedResponse":
        return cls(
            ctx=ctx,
            outcome=outcome,
            error_kind=None if outcome == "ok" else outcome,
            error_message=None,
            role=nr.role or "assistant",
            content=nr.content or "",
            reasoning=nr.reasoning,
            tool_calls=nr.tool_calls,
            finish_reason=nr.finish_reason,
            response_errors=list(nr.errors),
            prompt_tokens=nr.usage.prompt_tokens,
            completion_tokens=nr.usage.completion_tokens,
            reasoning_tokens=nr.usage.reasoning_tokens,
            total_tokens=nr.usage.total_tokens,
            cost_usd=nr.cost_usd,
        )

    @classmethod
    def from_partial(
        cls,
        ctx: CaptureContext,
        outcome: CaptureOutcome,
        content: str = "",
        error: BaseException | None = None,
        partial_resp: NormalizedResponse | None = None,
    ) -> "CapturedResponse":
        """Build a response envelope when the request didn't complete cleanly.

        Used from the proxy's try/finally on stream errors, client disconnects,
        and upstream failures. ``content`` should be whatever text was
        accumulated up to the failure point. ``partial_resp`` (when provided)
        contributes any reasoning / tool_calls / usage that did get parsed
        before the abort.
        """
        if outcome == "ok":
            raise ValueError("from_partial is for failure outcomes; use from_normalized for ok")

        error_kind = outcome
        error_message = str(error) if error is not None else None

        if partial_resp is not None:
            return cls(
                ctx=ctx,
                outcome=outcome,
                error_kind=error_kind,
                error_message=error_message,
                role=partial_resp.role or "assistant",
                content=content or partial_resp.content or "",
                reasoning=partial_resp.reasoning,
                tool_calls=partial_resp.tool_calls,
                finish_reason=partial_resp.finish_reason or _finish_reason_for_outcome(outcome),
                response_errors=list(partial_resp.errors),
                prompt_tokens=partial_resp.usage.prompt_tokens,
                completion_tokens=partial_resp.usage.completion_tokens,
                reasoning_tokens=partial_resp.usage.reasoning_tokens,
                total_tokens=partial_resp.usage.total_tokens,
                cost_usd=partial_resp.cost_usd,
            )

        return cls(
            ctx=ctx,
            outcome=outcome,
            error_kind=error_kind,
            error_message=error_message,
            role="assistant",
            content=content or "",
            reasoning=None,
            tool_calls=None,
            finish_reason=_finish_reason_for_outcome(outcome),
            response_errors=[],
        )


def _finish_reason_for_outcome(outcome: CaptureOutcome) -> str:
    if outcome == "client_disconnect":
        return "aborted"
    if outcome == "stream_aborted":
        return "error"
    if outcome == "upstream_error":
        return "error"
    return "stop"


class CaptureFanout:
    """Fans out one captured request/response pair to all registered sinks.

    Sinks (``wire``, ``conversations``, ``vector``) are passed in at
    construction so the fanout has no module-level singletons. Each sink
    is dispatched inside a try/except so one failing sink doesn't break
    the others — but the failure is logged and reraised in tests.

    The split between ``capture_request`` and ``capture_response`` is
    deliberate: a request-only capture (e.g. guardrail-rejected before
    upstream call) doesn't need a synthetic response, and a response that
    fails (``outcome != "ok"``) shouldn't get embedded into the vector
    store.
    """

    def __init__(self, *, conversations, wire, vector=None) -> None:
        # ``conversations`` is the ConversationRepo (per-entity repo on
        # BaseDB) — owns the messages table and the capture-pipeline
        # writers (store_captured_request / store_captured_response).
        self.conversations = conversations
        self.wire = wire
        self.vector = vector
        # logger imported lazily to avoid pulling logging into pure-data tests
        import logging
        self._log = logging.getLogger("beigebox.capture")

    def capture_request(self, req: "CapturedRequest") -> list[str]:
        """Fan out a captured request. Returns inserted message IDs."""
        inserted_ids: list[str] = []
        try:
            inserted_ids = self.conversations.store_captured_request(req)
        except Exception as exc:
            self._log.warning(
                "capture_request: SQLite store failed (conv=%s): %s",
                req.ctx.conv_id, exc,
            )
        try:
            self.wire.write_request(req)
        except Exception as exc:
            self._log.warning(
                "capture_request: wire.write_request failed (conv=%s): %s",
                req.ctx.conv_id, exc,
            )
        # Vector embedding for input messages — fire-and-forget so we never
        # block the request path. Only the user-role messages are embedded
        # (matching the old _log_messages behaviour); IDs and content come
        # from the rows we just inserted, paired with messages by index.
        if self.vector is not None and inserted_ids:
            self._embed_request(req, inserted_ids)
        return inserted_ids

    def capture_response(self, resp: "CapturedResponse") -> str | None:
        """Fan out a captured response. Returns inserted message ID."""
        inserted_id: str | None = None
        try:
            inserted_id = self.conversations.store_captured_response(resp)
        except Exception as exc:
            self._log.warning(
                "capture_response: SQLite store failed (conv=%s outcome=%s): %s",
                resp.ctx.conv_id, resp.outcome, exc,
            )
        try:
            self.wire.write_response(resp)
        except Exception as exc:
            self._log.warning(
                "capture_response: wire.write_response failed (conv=%s): %s",
                resp.ctx.conv_id, exc,
            )
        # Only embed successful responses with non-empty content. Failures
        # and aborts get persisted but not indexed.
        if (
            self.vector is not None
            and inserted_id is not None
            and resp.outcome == "ok"
            and resp.content
        ):
            self._embed_response(resp, inserted_id)
        return inserted_id

    def _embed_request(self, req: "CapturedRequest", ids: list[str]) -> None:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        # Pair inserted IDs back with the messages that were inserted (skipping
        # system / empty entries the same way store_captured_request does).
        i = 0
        for m in req.messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            content = m.get("content", "")
            if not content or role == "system":
                continue
            if i >= len(ids):
                break
            msg_id = ids[i]
            i += 1
            content_str = content if isinstance(content, str) else str(content)
            self._spawn_embed(msg_id, req.ctx, role, content_str)

    def _embed_response(self, resp: "CapturedResponse", msg_id: str) -> None:
        self._spawn_embed(msg_id, resp.ctx, resp.role or "assistant", resp.content)

    def _spawn_embed(self, msg_id: str, ctx: "CaptureContext", role: str, content: str) -> None:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        timestamp = (ctx.ended_at or ctx.started_at or _utcnow()).isoformat()
        try:
            task = loop.create_task(self.vector.store_message_async(
                message_id=msg_id,
                conversation_id=ctx.conv_id,
                role=role,
                content=content,
                model=ctx.model,
                timestamp=timestamp,
            ))
            task.add_done_callback(
                lambda t: t.exception() and self._log.warning(
                    "capture: vector embed failed: %s", t.exception()
                )
            )
        except RuntimeError:
            # No running loop (e.g. tests). Fine — embedding is best-effort.
            pass


def attach_response_timing(
    ctx: CaptureContext,
    *,
    ended_at: datetime | None = None,
    ttft_ms: float | None = None,
    request_id: str | None = None,
) -> CaptureContext:
    """Return a copy of ctx with response-side timing/IDs filled in.

    Computes ``latency_ms`` from ``started_at`` to ``ended_at`` automatically.
    Mutates nothing — proxy.py builds one CaptureContext at request time and
    swaps in an enriched copy for the response capture.
    """
    end = ended_at or _utcnow()
    latency_ms = (end - ctx.started_at).total_seconds() * 1000.0
    return CaptureContext(
        conv_id=ctx.conv_id,
        turn_id=ctx.turn_id,
        model=ctx.model,
        backend=ctx.backend,
        started_at=ctx.started_at,
        run_id=ctx.run_id,
        request_id=request_id if request_id is not None else ctx.request_id,
        ended_at=end,
        ttft_ms=ttft_ms if ttft_ms is not None else ctx.ttft_ms,
        latency_ms=latency_ms,
        user_id=ctx.user_id,
    )


# ---------------------------------------------------------------------------
# Envelope builders — orchestrator-side helpers
# ---------------------------------------------------------------------------
# These pair the proxy's request body with the BackendResponse / accumulated
# stream text and produce the dataclasses above. They were previously
# methods on ``Proxy`` (``_build_capture_context``, ``_build_captured_request``,
# ``_capture_stream_response``); they had no remaining dependency on Proxy
# state once the capture pipeline was the only path. Lifted here in G-2.
# ---------------------------------------------------------------------------


def build_capture_context(
    conversation_id: str,
    model: str,
    *,
    backend: str = "",
    run_id: str | None = None,
    user_id: str | None = None,
) -> CaptureContext:
    """Build a fresh ``CaptureContext`` for a request entering the proxy."""
    from uuid import uuid4
    return CaptureContext(
        conv_id=conversation_id,
        turn_id=uuid4().hex,
        model=model,
        backend=backend,
        started_at=_utcnow(),
        run_id=run_id,
        user_id=user_id,
    )


def build_captured_request(
    ctx: CaptureContext,
    body: dict,
    response,
) -> CapturedRequest:
    """Construct a ``CapturedRequest`` from the body + (optional) response.

    When ``response`` is the ``BackendResponse`` from the router, prefer the
    ``NormalizedRequest`` it stashed (full transforms / errors / target).
    When unavailable (direct httpx fallback or upstream error before the
    router returned), fall back to a best-effort envelope built from the
    request body alone.
    """
    nr = getattr(response, "normalized_request", None) if response is not None else None
    messages = body.get("messages", []) if isinstance(body, dict) else []
    if nr is not None:
        return CapturedRequest.from_normalized(nr, ctx, messages)
    return CapturedRequest(
        ctx=ctx,
        target="(unavailable)",
        transforms=[],
        errors=[],
        messages=list(messages),
        has_tools=bool(body.get("tools")) if isinstance(body, dict) else False,
        stream=bool(body.get("stream")) if isinstance(body, dict) else False,
    )


def capture_stream_response(
    fanout: "CaptureFanout | None",
    ctx: CaptureContext,
    body: dict,
    full_response: list[str],
    stages: dict[str, float],
    backend_name: str,
    cost_usd: float | None,
    prompt_tokens: int,
    *,
    outcome: str,
    error: BaseException | None,
    req_already_captured: bool,
) -> None:
    """Emit a captured response envelope for a streaming request.

    On normal completion (``outcome="ok"``), synthesizes a
    ``NormalizedResponse`` from the accumulated text + token estimates so
    the row carries the same fields the non-streaming path produces. On
    failure outcomes (``stream_aborted`` / ``client_disconnect``), uses
    ``from_partial`` so partial content is preserved with the right
    outcome marker.

    No-op when ``fanout`` is ``None``.
    """
    if fanout is None:
        return
    import logging
    from beigebox.response_normalizer import (
        estimate_tokens as _estimate_tokens,
        normalize_response,
    )
    log = logging.getLogger("beigebox.capture")

    ttft_ms = stages.get("ttft_ms")
    ctx_after = attach_response_timing(ctx, ttft_ms=ttft_ms)
    # Make sure backend got populated in ctx_after; carry the resolved
    # name through if the caller already knows it.
    if backend_name and ctx_after.backend != backend_name:
        ctx_after = CaptureContext(
            conv_id=ctx_after.conv_id,
            turn_id=ctx_after.turn_id,
            model=ctx_after.model,
            backend=backend_name,
            started_at=ctx_after.started_at,
            run_id=ctx_after.run_id,
            request_id=ctx_after.request_id,
            ended_at=ctx_after.ended_at,
            ttft_ms=ctx_after.ttft_ms,
            latency_ms=ctx_after.latency_ms,
            user_id=ctx_after.user_id,
        )

    # Belt-and-suspenders: emit a request envelope if the chunk loop never
    # reached its first chunk (upstream errored before any output arrived).
    if not req_already_captured:
        try:
            fanout.capture_request(build_captured_request(ctx, body, None))
        except Exception as exc:
            log.warning("stream capture_request fallback failed: %s", exc)

    complete_text = "".join(full_response)
    try:
        if outcome == "ok":
            synth = normalize_response({
                "choices": [{
                    "message": {"role": "assistant", "content": complete_text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens or 0,
                    "completion_tokens": _estimate_tokens(complete_text),
                },
            })
            # OpenRouter cost arrives via the cost sentinel (not the synthesized
            # data dict) — slot it onto the normalized response so the captured
            # row's cost_usd is populated.
            synth.cost_usd = cost_usd
            fanout.capture_response(
                CapturedResponse.from_normalized(synth, ctx_after, "ok")
            )
        else:
            fanout.capture_response(
                CapturedResponse.from_partial(
                    ctx=ctx_after,
                    outcome=outcome,
                    content=complete_text,
                    error=error,
                )
            )
    except Exception as exc:
        log.warning("stream capture_response failed: %s", exc)
