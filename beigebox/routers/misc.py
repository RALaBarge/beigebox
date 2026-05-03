"""Small remaining routes that don't fit any subject-specific router.

Extracted from beigebox/main.py (C-5). These six handlers are wholly
independent — they share only that none of them belong with app
construction:

  - GET /.well-known/agent-card.json — A2A agent card
  - GET /                            — Web UI (index.html)
  - GET /ui                          — Alias for /
  - POST /api/v1/probe               — Server-side HTTP probe (Docker net)
  - POST /api/v1/bench/run           — Direct-to-Ollama speed benchmark (SSE)
  - GET /api/v1/cdp/status           — Chrome DevTools Protocol availability

The catch-all at ``/{path:path}`` and the ``/web`` StaticFiles mount
remain in main.py — the catch-all MUST be the last route registered,
which is easier to enforce when it lives next to ``app``.
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from beigebox import __version__ as _BB_VERSION
from beigebox.config import get_config
from beigebox.state import get_state

logger = logging.getLogger(__name__)


router = APIRouter()


# ── A2A agent card ────────────────────────────────────────────────────────

@router.get("/.well-known/agent-card.json")
async def agent_card():
    """A2A agent card — describes this node to the AMF mesh and any A2A client."""
    cfg = get_config()
    _st = get_state()
    port = cfg["server"]["port"]
    endpoint = f"http://localhost:{port}"
    tools = _st.tool_registry.list_tools() if _st.tool_registry else []
    skills = [
        {"id": t, "name": t, "description": f"BeigeBox tool: {t}", "tags": [t]}
        for t in tools
    ]
    return JSONResponse({
        "name": "beigebox",
        "description": "BeigeBox — OpenAI-compatible proxy with local LLM routing and MCP skill access",
        "url": endpoint,
        "version": _BB_VERSION,
        "capabilities": {"streaming": True, "pushNotifications": False},
        "skills": skills,
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json", "text/event-stream"],
    })


# ── Web UI — simple HTML chat interface ───────────────────────────────────

@router.get("/")
async def root():
    """Serve the web UI."""
    return FileResponse("beigebox/web/index.html", media_type="text/html")


@router.get("/ui")
async def ui():
    """Alias for root."""
    return FileResponse("beigebox/web/index.html", media_type="text/html")


# ── Network probe — server-side HTTP request (reaches internal Docker services)

@router.post("/api/v1/probe")
async def api_probe(request: Request):
    """
    Fire an HTTP request from BeigeBox's network context and return the result.
    Useful for reaching internal services (Ollama, relay, etc.) from inside Docker.

    Body: {
        "method":  "GET",
        "url":     "http://localhost:11434/api/version",
        "headers": {"key": "value"},   // optional
        "body":    "...",              // optional, for POST/PUT/PATCH
        "timeout": 10                  // optional, seconds (default 10)
    }
    Returns: {status, reason, latency_ms, headers, body}
         or: {error, latency_ms}
    """
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    method = body.get("method", "GET").upper()
    url = body.get("url", "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, status_code=400)

    req_headers = body.get("headers") or {}
    req_body = body.get("body") or None
    timeout = float(body.get("timeout", 10))

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.request(
                method,
                url,
                headers=req_headers,
                content=req_body.encode() if isinstance(req_body, str) else req_body,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            text = resp.text[:65536]
        except Exception:
            text = resp.content[:65536].decode(errors="replace")
        return JSONResponse({
            "status": resp.status_code,
            "reason": resp.reason_phrase,
            "latency_ms": latency_ms,
            "headers": dict(resp.headers),
            "body": text,
        })
    except httpx.ConnectError as e:
        return JSONResponse({
            "error": f"Connection refused: {e}",
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })
    except httpx.TimeoutException:
        return JSONResponse({
            "error": f"Timed out after {timeout}s",
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })
    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })


# ── Bench — direct-to-Ollama speed benchmark (bypasses proxy) ─────────────

@router.post("/api/v1/bench/run")
async def bench_run(request: Request):
    """
    Run a speed benchmark directly against Ollama (/api/generate), bypassing BeigeBox proxy.

    POST /api/v1/bench/run
    {
        "models": ["llama3.1:8b", "qwen2.5:7b"],
        "prompt": "...",          // optional, uses default if omitted
        "num_predict": 120,       // tokens to generate per run
        "num_runs": 5             // measured runs per model (warmup is extra)
    }

    Returns Server-Sent Events stream. Each event is a JSON line prefixed "data: ".
    Event types: start, warmup, run, model_done, done, error
    """
    from beigebox.bench import (
        BenchmarkRunner,
        DEFAULT_NUM_PREDICT,
        DEFAULT_NUM_RUNS,
        DEFAULT_PROMPT,
    )

    body = await request.json()
    models = body.get("models", [])
    if not models:
        return JSONResponse({"error": "models list required"}, status_code=400)

    prompt = body.get("prompt") or DEFAULT_PROMPT
    num_predict = int(body.get("num_predict", DEFAULT_NUM_PREDICT))
    num_runs = int(body.get("num_runs", DEFAULT_NUM_RUNS))

    cfg = get_config()
    ollama_url = cfg.get("backend", {}).get("url", "http://localhost:11434").rstrip("/")

    runner = BenchmarkRunner(ollama_url=ollama_url)

    async def event_stream():
        try:
            async for event in runner.run_stream(
                models=models,
                prompt=prompt,
                num_predict=num_predict,
                num_runs=num_runs,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            logger.exception("bench_run stream error: %s", exc)
            yield f"data: {json.dumps({'event': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── CDP (Chrome DevTools Protocol) Browser Control ────────────────────────

@router.get("/api/v1/cdp/status")
async def cdp_status():
    """
    Check if CDP (Chrome DevTools Protocol) browser is available.

    GET /api/v1/cdp/status
    Returns: {"available": true/false, "ws_url": "ws://...", "http_url": "http://..."}
    """
    cdp_ws_url = "ws://localhost:9222"
    cdp_http_url = "http://localhost:9222"

    try:
        # Quick health check: try to fetch the CDP protocol version
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{cdp_http_url}/json/version")
            if resp.status_code == 200:
                return JSONResponse({
                    "available": True,
                    "ws_url": cdp_ws_url,
                    "http_url": cdp_http_url,
                })
    except Exception:
        pass

    return JSONResponse({
        "available": False,
        "ws_url": cdp_ws_url,
        "http_url": cdp_http_url,
        "note": "Chrome/Chromium not running. Start with: docker compose --profile cdp up -d",
    })


__all__ = ["router"]
