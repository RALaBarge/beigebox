"""
BeigeBox — LLM Middleware Control Plane

LICENSING: Dual-licensed under AGPL-3.0 (free) and Commercial License (proprietary).
See LICENSE.md and COMMERCIAL_LICENSE.md for details.

FastAPI application — the BeigeBox entry point. Wires app construction,
middleware, lifespan, and router registration. Endpoint handlers live in
beigebox/routers/; storage, security, and proxy initialization runs through
the lifespan() startup path which delegates to ``beigebox.bootstrap``.
"""

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from beigebox import __version__ as _BB_VERSION
from beigebox.config import get_config


logger = logging.getLogger(__name__)

# Application state singleton lives in beigebox/state.py so router modules
# can reach it without an import cycle through main.py. Re-exported here so
# `from beigebox.main import get_state` keeps working.
from beigebox.state import get_state, maybe_state, set_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle — thin orchestrator over ``bootstrap``."""
    from beigebox import bootstrap
    state = await bootstrap.startup(app)
    set_state(state)
    yield
    await bootstrap.shutdown(state)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="BeigeBox",
    description="Tap the line. Control the carrier.",
    version=_BB_VERSION,
    lifespan=lifespan,
)

from beigebox.middleware import (  # noqa: E402
    ApiKeyMiddleware,
    SecurityHeadersMiddleware,
    WebAuthMiddleware,
)

app.add_middleware(ApiKeyMiddleware)
app.add_middleware(WebAuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Router registration — order matters. All include_router() calls happen
# AFTER middleware registration (so routes attach to the configured stack)
# but BEFORE the catch-all route at the bottom of this file (so they don't
# shadow specific paths).
# ---------------------------------------------------------------------------

from beigebox.routers.openai import router as openai_router  # noqa: E402
from beigebox.routers.auth import router as auth_router  # noqa: E402
from beigebox.routers.security import router as security_router  # noqa: E402
from beigebox.routers.workspace import router as workspace_router  # noqa: E402
from beigebox.routers.analytics import router as analytics_router  # noqa: E402
from beigebox.routers.tools import router as tools_router  # noqa: E402
from beigebox.routers.config import router as config_router  # noqa: E402

app.include_router(openai_router)
app.include_router(auth_router)
app.include_router(security_router)
app.include_router(workspace_router)
app.include_router(analytics_router)
app.include_router(tools_router)
app.include_router(config_router)


@app.get("/.well-known/agent-card.json")
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


# ---------------------------------------------------------------------------
# Network probe — server-side HTTP request (reaches internal Docker services)
# ---------------------------------------------------------------------------

@app.post("/api/v1/probe")
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
    import time as _time
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

    t0 = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.request(
                method,
                url,
                headers=req_headers,
                content=req_body.encode() if isinstance(req_body, str) else req_body,
            )
        latency_ms = int((_time.monotonic() - t0) * 1000)
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
        return JSONResponse({"error": f"Connection refused: {e}", "latency_ms": int((_time.monotonic() - t0) * 1000)})
    except httpx.TimeoutException:
        return JSONResponse({"error": f"Timed out after {timeout}s", "latency_ms": int((_time.monotonic() - t0) * 1000)})
    except Exception as e:
        return JSONResponse({"error": str(e), "latency_ms": int((_time.monotonic() - t0) * 1000)})


# Serve static web assets (vi.js etc.) — must come before catch-all routes
from pathlib import Path as _Path
_web_dir = _Path(__file__).parent / "web"
if _web_dir.exists():
    app.mount("/web", StaticFiles(directory=str(_web_dir)), name="web")


# ---------------------------------------------------------------------------
# Web UI — simple HTML chat interface
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the web UI."""
    return FileResponse("beigebox/web/index.html", media_type="text/html")


@app.get("/ui")
async def ui():
    """Alias for root."""
    return FileResponse("beigebox/web/index.html", media_type="text/html")


# _wire_and_forward backs the catch-all route below.
from beigebox.routers._shared import _wire_and_forward


# ---------------------------------------------------------------------------
# Bench — direct-to-Ollama speed benchmark (bypasses proxy)
# ---------------------------------------------------------------------------

@app.post("/api/v1/bench/run")
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
    from beigebox.bench import BenchmarkRunner, DEFAULT_PROMPT, DEFAULT_NUM_PREDICT, DEFAULT_NUM_RUNS

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


# ---------------------------------------------------------------------------
# CDP (Chrome DevTools Protocol) Browser Control
# ---------------------------------------------------------------------------

@app.get("/api/v1/cdp/status")
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


# ---------------------------------------------------------------------------
# Catch-all — anything not explicitly handled above is forwarded transparently.
# Logged to wiretap as "passthrough/unknown" for observability.
# MUST be the last route registered.
# ---------------------------------------------------------------------------

@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def catch_all(path: str, request: Request):
    """
    Transparent passthrough for any endpoint not explicitly handled.
    Keeps BeigeBox invisible to frontends and backends that use endpoints
    we haven't specifically implemented. All traffic is logged to wiretap.
    """
    # Don't forward requests to our own beigebox/* or api/v1/* endpoints —
    # those are handled above; if we're here it means they 404'd internally.
    if path.startswith("beigebox/") or path.startswith("api/v1/"):
        return JSONResponse({"error": "not found", "path": path}, status_code=404)

    # Silently reject common browser noise (don't log to wiretap, don't forward to backend)
    noise_paths = {
        "favicon.ico",
        ".well-known/appspecific/com.chrome.devtools.json",
        ".well-known/webfinger",
        "robots.txt",
        "sitemap.xml",
        ".git/config",
        ".env",
        "web.config",
    }
    if path in noise_paths or path.startswith(".well-known/") and "chrome" in path.lower():
        return JSONResponse({"error": "not found"}, status_code=404)

    return await _wire_and_forward(request, f"passthrough/{path}")


# ---------------------------------------------------------------------------
# Run with: python -m beigebox.main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    cfg = get_config()
    uvicorn.run(
        "beigebox.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=False,
    )
