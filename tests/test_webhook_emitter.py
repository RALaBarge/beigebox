"""
Tests for hooks/webhook_emitter.py.

Strategy: load the hook module by file path (mirrors how HookManager loads it
in production), monkeypatch _enqueue() so we can assert what would be POSTed
without spinning up a real HTTP server. The worker thread / queue mechanics
are exercised separately with a tiny stub HTTP server.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


HOOK_PATH = Path(__file__).parent.parent / "hooks" / "webhook_emitter.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("webhook_emitter", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook():
    """Fresh hook module per test (worker queue is module-global)."""
    return _load_hook()


@pytest.fixture
def captured(monkeypatch, hook):
    """Replace _enqueue with a list-append so we can assert payloads."""
    bag: list[tuple[str, dict, int]] = []

    def fake_enqueue(url, payload, timeout_ms, queue_size):
        bag.append((url, payload, timeout_ms))

    monkeypatch.setattr(hook, "_enqueue", fake_enqueue)
    return bag


# ── Config gating ───────────────────────────────────────────────────────────


def test_disabled_by_default_pre(hook, captured):
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    hook.pre_request(body, {"config": {"hooks": {"webhook_emitter": {"enabled": False}}}})
    assert captured == []


def test_disabled_by_default_post(hook, captured):
    hook.post_response(
        {"model": "x"},
        {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]},
        {"config": {}},
    )
    assert captured == []


def test_no_url_no_emit(hook, captured):
    """enabled=True but no url + no env var → no event."""
    cfg = {"config": {"hooks": {"webhook_emitter": {"enabled": True}}}}
    body = {"model": "x", "messages": []}
    hook.pre_request(body, cfg)
    assert captured == []


def test_env_var_overrides_config_url(hook, captured, monkeypatch):
    monkeypatch.setenv("BEIGEBOX_WEBHOOK_URL", "https://from-env.example.com/x")
    cfg = {
        "config": {
            "hooks": {
                "webhook_emitter": {
                    "enabled": True,
                    "url": "https://from-yaml.example.com/y",
                }
            }
        }
    }
    hook.pre_request({"model": "m", "messages": []}, cfg)
    assert captured[0][0] == "https://from-env.example.com/x"


# ── Payload shape ───────────────────────────────────────────────────────────


def _ctx(**overrides) -> dict:
    base = {
        "conversation_id": "conv-abc",
        "config": {
            "hooks": {
                "webhook_emitter": {
                    "enabled": True,
                    "url": "https://hook.example/x",
                    "timeout_ms": 250,
                    "queue_size": 16,
                }
            }
        },
    }
    base.update(overrides)
    return base


def test_pre_request_payload(hook, captured):
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "hello world"},
        ],
    }
    ctx = _ctx()
    out = hook.pre_request(body, ctx)
    # t0 stashed on the CONTEXT (side channel), not on the body — the body
    # is forwarded to the upstream LLM and we don't want hook-internal
    # bookkeeping leaking out.
    assert "_bb_webhook_t0" not in out
    assert "_bb_webhook_t0" in ctx
    assert len(captured) == 1
    url, payload, timeout = captured[0]
    assert url == "https://hook.example/x"
    assert timeout == 250
    assert payload["event"] == "run_start"
    assert payload["request_id"] == "conv-abc"
    assert payload["model"] == "openai/gpt-4o-mini"
    assert payload["message_count"] == 2
    assert payload["total_chars"] == len("be concise") + len("hello world")
    assert payload["schema_version"] == 1
    assert "timestamp" in payload
    # Privacy default: user message not surfaced
    assert "user_message" not in payload


def test_pre_request_includes_user_message_when_opted_in(hook, captured):
    ctx = _ctx()
    ctx["config"]["hooks"]["webhook_emitter"]["include_user_message"] = True
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "secret"},
        ],
    }
    hook.pre_request(body, ctx)
    assert captured[0][1]["user_message"] == "secret"


def test_post_response_payload(hook, captured):
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    response = {
        "choices": [
            {"finish_reason": "stop",
             "message": {"role": "assistant", "content": "hello back"}}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    ctx = _ctx()
    ctx["_bb_webhook_t0"] = time.monotonic() - 0.5  # simulate pre_request stash
    out = hook.post_response(body, response, ctx)
    assert out is response
    assert len(captured) == 1
    payload = captured[0][1]
    assert payload["event"] == "run_end"
    assert payload["request_id"] == "conv-abc"
    assert payload["prompt_tokens"] == 5
    assert payload["completion_tokens"] == 2
    assert payload["total_tokens"] == 7
    assert payload["finish_reason"] == "stop"
    assert payload["assistant_chars"] == len("hello back")
    assert payload["latency_ms"] > 0
    assert payload["latency_ms"] < 5000  # sanity
    assert payload["error"] is None


def test_post_response_handles_missing_t0(hook, captured):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    response = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {}}
    hook.post_response(body, response, _ctx())
    payload = captured[0][1]
    assert "latency_ms" not in payload  # gracefully omitted


def test_post_response_handles_malformed_response(hook, captured):
    """A None or non-dict response should be passed through and emit a sane envelope."""
    hook.post_response({"model": "m", "messages": []}, None, _ctx())
    payload = captured[0][1]
    assert payload["event"] == "run_end"
    assert payload["prompt_tokens"] is None
    assert payload["finish_reason"] is None


def test_t0_does_not_leak_into_body(hook, captured):
    """The body forwarded upstream must not have hook-internal bookkeeping
    keys (regression for the leak DeepSeek flagged in code review)."""
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    ctx = _ctx()
    body_after_pre = hook.pre_request(body, ctx)
    assert "_bb_webhook_t0" not in body_after_pre
    assert "_bb_webhook_t0" not in body  # pre_request returns the same dict ref
    assert "_bb_webhook_t0" in ctx       # but t0 lives on the side channel


def test_t0_popped_from_context_after_post(hook, captured):
    """Avoid stale t0 leaking into a subsequent request that reuses the ctx."""
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    ctx = _ctx()
    hook.pre_request(body, ctx)
    assert "_bb_webhook_t0" in ctx
    response = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {}}
    hook.post_response(body, response, ctx)
    assert "_bb_webhook_t0" not in ctx


def test_run_start_and_run_end_share_request_id(hook, captured):
    body = {"model": "m", "messages": [{"role": "user", "content": "go"}]}
    ctx = _ctx()
    body = hook.pre_request(body, ctx)
    response = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
    hook.post_response(body, response, ctx)
    assert len(captured) == 2
    assert captured[0][1]["request_id"] == captured[1][1]["request_id"]
    assert captured[0][1]["event"] == "run_start"
    assert captured[1][1]["event"] == "run_end"


# ── Worker thread + real HTTP loopback ──────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Collector(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        try:
            _Collector.received.append(json.loads(body))
        except json.JSONDecodeError:
            _Collector.received.append({"_raw": body})
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a, **kw):  # silence stderr spam
        pass


@pytest.fixture
def collector():
    _Collector.received = []
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _Collector)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/", _Collector
    finally:
        server.shutdown()
        server.server_close()


def test_worker_actually_posts(hook, collector):
    """End-to-end: real loopback server receives the JSON envelope."""
    url, sink = collector
    body = {"model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "real send"}]}
    ctx = {
        "conversation_id": "ete-1",
        "config": {
            "hooks": {
                "webhook_emitter": {
                    "enabled": True, "url": url,
                    "timeout_ms": 1000, "queue_size": 8,
                }
            }
        },
    }
    hook.pre_request(body, ctx)
    # Wait for the worker to drain
    deadline = time.time() + 3.0
    while time.time() < deadline and not sink.received:
        time.sleep(0.05)
    assert len(sink.received) == 1
    payload = sink.received[0]
    assert payload["event"] == "run_start"
    assert payload["request_id"] == "ete-1"
    assert payload["model"] == "openai/gpt-4o-mini"


def test_worker_survives_unreachable_url(hook):
    """Pointing at a port nothing's listening on must not crash the worker."""
    # 127.0.0.1:1 is reserved/unused on Linux
    body = {"model": "m", "messages": []}
    ctx = {
        "conversation_id": "dead-1",
        "config": {
            "hooks": {
                "webhook_emitter": {
                    "enabled": True, "url": "http://127.0.0.1:1/",
                    "timeout_ms": 200, "queue_size": 4,
                }
            }
        },
    }
    # Should not raise
    hook.pre_request(body, ctx)
    hook.pre_request(body, ctx)
    # Give the worker a moment to fail those POSTs and remain alive
    time.sleep(0.5)
    # If we got here, the worker absorbed the failures
