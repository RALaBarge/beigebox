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
_T0_KEY = "_bb_webhook_t0"

# ─────────────────────────────────────────────────────────────────────────────
# Background emitter — single daemon thread, lazy-started, drains a queue.
# ─────────────────────────────────────────────────────────────────────────────

_emitter_lock = threading.Lock()
_emitter_queue: queue.Queue[tuple[str, dict, int]] | None = None
_emitter_thread: threading.Thread | None = None


def _emitter_worker(q: queue.Queue) -> None:
    """Pulls (url, payload, timeout_ms) off the queue and POSTs.

    Failures are logged and dropped — retry policy is "none". The hook fires
    twice per request, so a transient network blip costs at most one missing
    pair on the receiver's side."""
    while True:
        url, payload, timeout_ms = q.get()
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
        finally:
            q.task_done()


def _ensure_emitter(queue_size: int) -> queue.Queue:
    """Lazy-start the worker thread. Idempotent; safe under racing callers."""
    global _emitter_queue, _emitter_thread
    with _emitter_lock:
        if _emitter_queue is None:
            _emitter_queue = queue.Queue(maxsize=queue_size)
            _emitter_thread = threading.Thread(
                target=_emitter_worker,
                args=(_emitter_queue,),
                name="bb-webhook-emitter",
                daemon=True,
            )
            _emitter_thread.start()
            logger.info("webhook_emitter worker thread started (queue_size=%d)",
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
    """Stash a monotonic start time on the body and emit run_start."""
    hook_cfg = _hook_config(context)
    if not hook_cfg.get("enabled", False):
        return body

    url = _resolved_url(hook_cfg)
    if not url:
        return body

    body[_T0_KEY] = time.monotonic()

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

    t0 = body.get(_T0_KEY)
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
