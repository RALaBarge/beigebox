"""
Webhook Emitter Hook — POST a JSON envelope to a remote URL on every request.

Fires once at pre_request (event="run_start") and once at post_response
(event="run_end") so a downstream observability service (Datadog, Honeycomb,
Logfire, n8n, a homemade collector, etc.) can track every chat-completion the
proxy serves without poking at logs.

Design choices the proposal landed on:

- Fire-and-forget on a daemon thread with a bounded queue. The hook NEVER
  blocks the request path; if the queue overflows or the receiver is slow,
  the event gets dropped and a warning is logged. Retries belong in the
  receiver, not in the proxy.
- Hard per-POST timeout (default 1500 ms). Slow webhook ≠ slow user request.
- The two hooks share a `request_id` (= conversation_id) so the receiver can
  pair start/end frames.
- User message text is OPT-IN (`include_user_message: true` in config).
  Default off so a misconfigured webhook can't exfiltrate prompts.
- `schema_version: 1` in every payload — future-proof the contract.

Enable in config.yaml:

    hooks:
      enabled: true
      directory: "./hooks"
      webhook_emitter:
        enabled: true
        url: "https://hooks.example.com/beigebox"   # falls back to BEIGEBOX_WEBHOOK_URL
        timeout_ms: 1500
        queue_size: 256
        include_user_message: false

Env var `BEIGEBOX_WEBHOOK_URL` overrides `hooks.webhook_emitter.url` if set.
Useful for staging vs prod where the YAML wants to stay env-agnostic.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_MS = 1500
DEFAULT_QUEUE_SIZE = 256
_T0_KEY = "_bb_webhook_t0"  # stashed on `context` (side channel), not on body

# ─────────────────────────────────────────────────────────────────────────────
# Background emitter — single daemon thread, lazy-started, drains a queue.
# A supervisor wraps the worker so a fatal exception (MemoryError, etc.) gets
# a clean re-spawn rather than silently halting all future events.
# ─────────────────────────────────────────────────────────────────────────────

_emitter_lock = threading.Lock()
_emitter_queue: queue.Queue[tuple[str, dict, int]] | None = None
_emitter_thread: threading.Thread | None = None


def _emit_one(url: str, payload: dict, timeout_ms: int) -> None:
    """POST a single payload. Catches expected HTTP / network failures."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "beigebox-webhook-emitter/1"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as resp:
            if resp.status >= 400:
                logger.warning(
                    "webhook_emitter: %s returned HTTP %d", url, resp.status
                )
    except urllib.error.URLError as e:
        logger.warning("webhook_emitter: %s -> URLError: %s", url, e.reason)
    except (TimeoutError, OSError) as e:
        logger.warning("webhook_emitter: %s -> %s: %s", url, type(e).__name__, e)
    except Exception as e:  # noqa: BLE001 — last resort, mustn't kill worker
        logger.exception("webhook_emitter: unexpected error: %s", e)


def _emitter_worker(q: queue.Queue) -> None:
    """Drain the queue forever. Per-iteration BaseException safety: any
    catastrophe (MemoryError, etc.) breaks the loop and lets the supervisor
    re-spawn rather than silently strand the queue."""
    while True:
        url, payload, timeout_ms = q.get()
        try:
            _emit_one(url, payload, timeout_ms)
        except BaseException as e:  # noqa: BLE001 — see above
            logger.error("webhook_emitter: worker hit %s: %s — supervisor will respawn",
                         type(e).__name__, e)
            q.task_done()
            raise
        finally:
            try:
                q.task_done()
            except ValueError:
                # task_done called twice (we already called it in the BaseException
                # branch) — benign
                pass


def _emitter_supervisor(q: queue.Queue, queue_size: int) -> None:
    """Wraps _emitter_worker. If the worker dies, log loudly and respawn after
    a short backoff so the next event isn't lost. Bounded restart rate."""
    backoff_s = 1.0
    max_backoff_s = 30.0
    while True:
        try:
            _emitter_worker(q)
            # _emitter_worker is an infinite loop; if it returns cleanly we
            # treat it like a death and respawn anyway.
        except BaseException as e:  # noqa: BLE001
            logger.error("webhook_emitter: supervisor caught %s: %s; respawning in %.1fs",
                         type(e).__name__, e, backoff_s)
        time.sleep(backoff_s)
        backoff_s = min(backoff_s * 2, max_backoff_s)


def _ensure_emitter(queue_size: int) -> queue.Queue:
    """Lazy-start the supervised worker. Idempotent; safe under racing callers."""
    global _emitter_queue, _emitter_thread
    with _emitter_lock:
        if _emitter_queue is None:
            _emitter_queue = queue.Queue(maxsize=queue_size)
            _emitter_thread = threading.Thread(
                target=_emitter_supervisor,
                args=(_emitter_queue, queue_size),
                name="bb-webhook-emitter-supervisor",
                daemon=True,
            )
            _emitter_thread.start()
            logger.info("webhook_emitter supervisor started (queue_size=%d)",
                        queue_size)
        return _emitter_queue


def _enqueue(url: str, payload: dict, timeout_ms: int, queue_size: int) -> None:
    """Push to the queue. Drops + logs on overflow."""
    q = _ensure_emitter(queue_size)
    try:
        q.put_nowait((url, payload, timeout_ms))
    except queue.Full:
        logger.warning(
            "webhook_emitter: queue full (size=%d); dropping event for %s",
            queue_size, url,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Config plumbing
# ─────────────────────────────────────────────────────────────────────────────


def _hook_config(context: dict) -> dict:
    """Locate this hook's config block.

    Supports both shapes the project uses:
      hooks:                                            # dict-style (current default)
        enabled: true
        webhook_emitter: {url: ..., timeout_ms: ...}
      hooks:                                            # list-style
        - name: webhook_emitter
          ...
    """
    cfg = (context.get("config") or {}).get("hooks") or {}
    if isinstance(cfg, dict):
        sub = cfg.get("webhook_emitter")
        if isinstance(sub, dict):
            return sub
        return {}
    if isinstance(cfg, list):
        for h in cfg:
            if isinstance(h, dict) and h.get("name") == "webhook_emitter":
                return h
    return {}


def _resolved_url(hook_cfg: dict) -> str | None:
    """Env var wins over config file. Returns None if neither is set."""
    return os.environ.get("BEIGEBOX_WEBHOOK_URL") or hook_cfg.get("url") or None


# ─────────────────────────────────────────────────────────────────────────────
# Payload construction
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _request_id(context: dict) -> str:
    """Use conversation_id as the correlation key if present; else timestamp."""
    return context.get("conversation_id") or f"req-{int(time.time()*1000)}"


def _common_envelope(event: str, body: dict, context: dict, hook_cfg: dict) -> dict:
    """Fields shared by run_start and run_end."""
    messages = body.get("messages") or []
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event": event,
        "timestamp": _now_iso(),
        "request_id": _request_id(context),
        "model": body.get("model") or context.get("model") or "",
        "message_count": len(messages),
        "synthetic": bool(body.get("_beigebox_synthetic")),
    }
    if hook_cfg.get("include_user_message"):
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                payload["user_message"] = (
                    content if isinstance(content, str) else json.dumps(content)
                )
                break
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Hook entry points
# ─────────────────────────────────────────────────────────────────────────────


def pre_request(body: dict, context: dict) -> dict:
    """Stash a monotonic start time on the CONTEXT (not the body) and emit
    run_start. Putting the timestamp on the body would leak `_bb_webhook_t0`
    upstream to the LLM provider — context is per-request and never forwarded.
    """
    hook_cfg = _hook_config(context)
    if not hook_cfg.get("enabled", False):
        return body

    url = _resolved_url(hook_cfg)
    if not url:
        return body

    context[_T0_KEY] = time.monotonic()

    payload = _common_envelope("run_start", body, context, hook_cfg)
    payload["total_chars"] = sum(
        len(m.get("content", "")) if isinstance(m.get("content"), str) else 0
        for m in body.get("messages", [])
    )

    timeout_ms = int(hook_cfg.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    queue_size = int(hook_cfg.get("queue_size", DEFAULT_QUEUE_SIZE))
    _enqueue(url, payload, timeout_ms, queue_size)
    return body


def post_response(body: dict, response: dict, context: dict) -> dict:
    """Compute latency, pull usage from the (already normalized) response, emit run_end."""
    hook_cfg = _hook_config(context)
    if not hook_cfg.get("enabled", False):
        return response

    url = _resolved_url(hook_cfg)
    if not url:
        return response

    payload = _common_envelope("run_end", body, context, hook_cfg)

    # Read t0 from the context (where pre_request stashed it). Fall back to
    # body for backwards compatibility with any caller still using the old
    # convention; pop it so a stale value can't leak forward.
    t0 = context.pop(_T0_KEY, None)
    if t0 is None:
        t0 = body.pop(_T0_KEY, None)
    if isinstance(t0, (int, float)):
        payload["latency_ms"] = round((time.monotonic() - t0) * 1000.0, 1)

    usage = (response.get("usage") or {}) if isinstance(response, dict) else {}
    payload["prompt_tokens"] = usage.get("prompt_tokens")
    payload["completion_tokens"] = usage.get("completion_tokens")
    payload["total_tokens"] = usage.get("total_tokens")

    choice0 = ((response.get("choices") or [{}])[0]
               if isinstance(response, dict) else {})
    payload["finish_reason"] = choice0.get("finish_reason")
    msg = choice0.get("message") or {}
    content = msg.get("content")
    payload["assistant_chars"] = len(content) if isinstance(content, str) else 0

    payload["error"] = response.get("error") if isinstance(response, dict) else None

    timeout_ms = int(hook_cfg.get("timeout_ms", DEFAULT_TIMEOUT_MS))
    queue_size = int(hook_cfg.get("queue_size", DEFAULT_QUEUE_SIZE))
    _enqueue(url, payload, timeout_ms, queue_size)
    return response
