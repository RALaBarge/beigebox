"""Analytics, telemetry, backends, and OpenRouter endpoints.

Extracted from beigebox/main.py (B-5). The largest router by endpoint
count (18+). Mostly read-only telemetry plus a few admin-write paths.

Endpoints:
- /api/v1/tools — list available tools
- /api/v1/status — detailed subsystem status
- /api/v1/stats — conversations + embeddings + timestamp
- /api/v1/costs — cost-tracker stats by model/day/conversation
- /api/v1/export — fine-tuning export (jsonl/alpaca/sharegpt)
- /api/v1/model-performance + /reset — per-model latency/throughput
- /api/v1/model-options (GET + POST) — runtime num_gpu overrides
- /api/v1/routing-stats — wiretap-derived session-cache hit rate
- /api/v1/tap — wire event reader with filters
- /api/v1/inspector — last N outbound LLM requests
- /api/v1/backends + /apply + /{name}/models — multi-backend config
- /api/v1/system-metrics — CPU/RAM/GPU snapshot
- /api/v1/openrouter/models + /pinned (GET+POST) + /balance
- /api/v1/artificial-analysis/rankings — top-15 agentic + coding models
- /api/v1/generation-params/reset — clear runtime gen overrides
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from beigebox.backends.router import MultiBackendRouter
from beigebox.config import (
    get_config,
    get_effective_backends_config,
    get_runtime_config,
    update_runtime_config,
)
from beigebox.metrics import collect_system_metrics_async
from beigebox.state import get_state


logger = logging.getLogger(__name__)


router = APIRouter()


# ── Generation params reset ──────────────────────────────────────────────

@router.post("/api/v1/generation-params/reset")
async def api_reset_generation_params():
    """Clear all runtime generation parameters so requests go untouched."""
    gen_keys = [
        "gen_temperature", "gen_top_p", "gen_top_k", "gen_num_ctx",
        "gen_repeat_penalty", "gen_max_tokens", "gen_seed", "gen_stop", "gen_force",
    ]
    cleared = []
    for key in gen_keys:
        if update_runtime_config(key, None):
            cleared.append(key)
    return JSONResponse({"cleared": cleared, "ok": True})


# ── Tools listing ─────────────────────────────────────────────────────────

@router.get("/api/v1/tools")
async def api_tools():
    """List available tools."""
    _st = get_state()
    if not _st.tool_registry:
        return JSONResponse({"tools": []})
    tools = _st.tool_registry.list_tools()
    return JSONResponse({
        "tools": tools,
        "enabled": get_config().get("tools", {}).get("enabled", False),
    })


# ── Status / stats / costs ───────────────────────────────────────────────

@router.get("/api/v1/status")
async def api_status():
    """Detailed status of all subsystems."""
    cfg = get_config()
    _st = get_state()
    return JSONResponse({
        "proxy": {
            "running": _st.proxy is not None,
            "backend_url": _st.proxy.backend_url if _st.proxy else "",
            "default_model": _st.proxy.default_model if _st.proxy else "",
        },
        "storage": {
            "sqlite": _st.conversations is not None,
            "vector": _st.vector_store is not None,
            "stats": _st.conversations.get_stats() if _st.conversations else {},
        },
        "tools": {
            "enabled": cfg.get("tools", {}).get("enabled", False),
            "available": _st.tool_registry.list_tools() if _st.tool_registry else [],
        },
        "operator": {
            "model": cfg.get("operator", {}).get("model", ""),
            "shell_enabled": cfg.get("operator", {}).get("shell", {}).get("enabled", False),
        },
        "wasm": {
            "enabled": _st.proxy.wasm_runtime.enabled if _st.proxy else False,
            "modules": _st.proxy.wasm_runtime.list_modules() if _st.proxy else [],
        },
    })


@router.get("/api/v1/stats")
async def api_stats():
    """Statistics about conversations and usage."""
    _st = get_state()
    sqlite_stats = _st.conversations.get_stats() if _st.conversations else {}
    vector_stats = _st.vector_store.get_stats() if _st.vector_store else {}
    return JSONResponse({
        "conversations": sqlite_stats,
        "embeddings": vector_stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/api/v1/costs")
async def api_costs(days: int = 30):
    """Cost tracking stats. Returns total, by_model, by_day, by_conversation."""
    _st = get_state()
    if not _st.cost_tracker:
        return JSONResponse({
            "enabled": False,
            "message": "Cost tracking is disabled. Set cost_tracking.enabled: true in config.",
        })
    stats = _st.cost_tracker.get_stats(days=days)
    stats["enabled"] = True
    return JSONResponse(stats)


# ── Export (fine-tuning) ─────────────────────────────────────────────────

@router.get("/api/v1/export")
async def api_export(format: str = "jsonl", model: str | None = None):
    """Export conversations for fine-tuning. Formats: jsonl, alpaca, sharegpt."""
    _st = get_state()
    if not _st.conversations:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    fmt = format.lower().strip()
    if fmt not in ("jsonl", "alpaca", "sharegpt"):
        return JSONResponse(
            {"error": f"Unknown format '{fmt}'. Use: jsonl, alpaca, sharegpt"},
            status_code=400,
        )

    model_filter = model or None

    if fmt == "jsonl":
        data = _st.conversations.export_jsonl(model_filter)
        filename = "conversations.jsonl"
        content = "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n"
        media_type = "application/x-ndjson"
    elif fmt == "alpaca":
        data = _st.conversations.export_alpaca(model_filter)
        filename = "conversations_alpaca.json"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:
        data = _st.conversations.export_sharegpt(model_filter)
        filename = "conversations_sharegpt.json"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"

    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Model performance ────────────────────────────────────────────────────

@router.get("/api/v1/model-performance")
async def api_model_performance(days: int = 30):
    """Per-model latency and throughput stats."""
    _st = get_state()
    if not _st.conversations:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)
    rt = get_runtime_config()
    since = rt.get("perf_stats_since") or None
    data = _st.conversations.get_model_performance(days=days, since=since)
    data["enabled"] = True
    data["since"] = since
    return JSONResponse(data)


@router.post("/api/v1/model-performance/reset")
async def api_model_performance_reset():
    """Set perf_stats_since to now, zeroing the visible stats window."""
    now = datetime.now(timezone.utc).isoformat()
    update_runtime_config("perf_stats_since", now)
    return JSONResponse({"ok": True, "since": now})


# ── Model options (GPU layers per model) ─────────────────────────────────

@router.get("/api/v1/model-options")
async def api_get_model_options():
    """Return current runtime model_options (num_gpu per model)."""
    rt = get_runtime_config()
    return JSONResponse({"model_options": rt.get("model_options", {})})


@router.post("/api/v1/model-options")
async def api_set_model_option(request: Request):
    """Set or clear num_gpu for a specific model. 0=CPU, 99=GPU, null=inherit."""
    body = await request.json()
    model_name = body.get("model", "").strip()
    if not model_name:
        return JSONResponse({"error": "model required"}, status_code=400)
    num_gpu = body.get("num_gpu")

    rt = get_runtime_config()
    model_opts = dict(rt.get("model_options") or {})

    if num_gpu is None:
        model_opts.pop(model_name, None)
    else:
        model_opts[model_name] = int(num_gpu)

    update_runtime_config("model_options", model_opts)
    return JSONResponse({"ok": True, "model": model_name, "num_gpu": num_gpu})


# ── Routing stats from wiretap ────────────────────────────────────────────

@router.get("/api/v1/routing-stats")
async def api_routing_stats(lines: int = 10000):
    """Session-cache hit rate from the wiretap (tail-scans wire.jsonl)."""
    cfg = get_config()
    wire_path = Path(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))

    hits = misses = sampled = 0

    if wire_path.exists():
        try:
            with open(wire_path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                chunk = min(size, lines * 220)
                fh.seek(-chunk, 2)
                raw = fh.read().decode("utf-8", errors="replace")

            for line in raw.splitlines()[-lines:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("role") != "decision" or entry.get("dir") != "internal":
                    continue
                sampled += 1
                model = entry.get("model", "")
                if model == "session-cache":
                    hits += 1
                elif model in ("embedding-classifier", "decision-llm"):
                    misses += 1
        except Exception as e:
            logger.warning("routing-stats: failed to read wiretap: %s", e)

    total = hits + misses
    return JSONResponse({
        "cache_hits": hits,
        "cache_misses": misses,
        "total": total,
        "hit_rate": round(hits / total, 4) if total else 0.0,
        "sampled_lines": sampled,
    })


# ── Backends config + apply + model-listing ──────────────────────────────

@router.get("/api/v1/backends")
async def api_backends():
    """Backend config + health/latency stats. API keys masked for security."""
    _st = get_state()
    if not _st.backend_router:
        cfg = get_config()
        return JSONResponse({
            "enabled": False,
            "message": "Multi-backend routing is disabled. Set backends_enabled: true in config.",
            "primary_backend": cfg.get("backend", {}).get("url", ""),
        })

    enabled, config_backends = get_effective_backends_config()
    health_list = await _st.backend_router.health()
    health_by_name = {h.get("name"): h for h in health_list}

    backends_list = []
    for config_b in config_backends:
        b = dict(config_b)
        if b.get("api_key"):
            b["api_key"] = "***"
        health = health_by_name.get(b.get("name"), {})
        b.update(health)
        backends_list.append(b)

    return JSONResponse({
        "enabled": enabled,
        "backends": backends_list,
    })


@router.post("/api/v1/backends/apply")
async def api_backends_apply(request: Request):
    """Save backends config to runtime_config.yaml + rebuild the router."""
    try:
        body = await request.json()
    except Exception as e:
        logger.debug("Invalid JSON body: %s", str(e)[:200])
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    try:
        enabled = bool(body.get("enabled", False))
        new_backends = body.get("backends", [])
        if not isinstance(new_backends, list):
            return JSONResponse({"error": "backends must be a list"}, status_code=400)

        rt = get_runtime_config()
        cfg = get_config()
        existing = rt.get("backends") if rt.get("backends") is not None else cfg.get("backends", [])
        existing_by_name = {b.get("name"): b for b in (existing or [])}

        resolved = []
        for b in new_backends:
            b = dict(b)
            raw_key = b.get("api_key", "")
            if raw_key in ("***", "***redacted***", ""):
                existing_b = existing_by_name.get(b.get("name"), {})
                b["api_key"] = existing_b.get("api_key", "")
            resolved.append(b)

        update_runtime_config("backends_enabled", enabled)
        update_runtime_config("backends", resolved)

        new_router = None
        if enabled and resolved:
            try:
                _, effective_backends = get_effective_backends_config()
                _model_routes = get_config().get("routing", {}).get("model_routes", [])
                new_router = MultiBackendRouter(effective_backends, model_routes=_model_routes)
            except Exception as e:
                logger.error("Router build failed: %s", e, exc_info=True)
                return JSONResponse({"error": f"Router build failed: {e}"}, status_code=500)

        _st = get_state()
        _st.backend_router = new_router
        if _st.proxy:
            _st.proxy.backend_router = new_router

        logger.info(
            "Backends reloaded via API: enabled=%s, %d backend(s)",
            enabled, len(resolved) if enabled else 0,
        )
        return JSONResponse({"ok": True, "enabled": enabled, "backends": len(resolved) if enabled else 0})

    except Exception as e:
        logger.error("api_backends_apply unexpected error: %s", e, exc_info=True)
        return JSONResponse({"error": f"Unexpected error: {e}"}, status_code=500)


@router.get("/api/v1/backends/{backend_name}/models")
async def api_backend_models(backend_name: str):
    """List available models from a specific backend."""
    _st = get_state()
    if not _st.backend_router:
        return JSONResponse({"models": []})

    backend = _st.backend_router.get_backend(backend_name)
    if not backend:
        return JSONResponse({"models": []})

    try:
        inner = getattr(backend, "backend", backend)
        models = await inner.list_models()
        return JSONResponse({"models": models or []})
    except Exception as e:
        logger.warning("Failed to list models from backend '%s': %s", backend_name, e)
        return JSONResponse({"models": []})


# ── System metrics ───────────────────────────────────────────────────────

@router.get("/api/v1/system-metrics")
async def api_system_metrics():
    """Real-time CPU, RAM, and GPU utilization with temperature probes."""
    data = await collect_system_metrics_async()
    return JSONResponse(data)


# ── Tap (wire log reader) ────────────────────────────────────────────────

@router.get("/api/v1/tap")
async def api_tap(
    n: int = 50,
    role: str | None = None,
    dir: str | None = None,
    source: str | None = None,
    event_type: str | None = None,
    conv_id: str | None = None,
    run_id: str | None = None,
):
    """Return recent wire events with optional filters. Primary path: SQLite
    wire_events table; falls back to JSONL parse for cold-start."""
    n = min(max(1, n), 500)
    repo = get_state().wire_events

    if repo is not None:
        try:
            rows = repo.query(
                n=n,
                event_type=event_type or None,
                source=source or None,
                conv_id=conv_id or None,
                run_id=run_id or None,
                role=role or None,
            )
            return JSONResponse({"entries": rows, "total": len(rows), "filtered": len(rows)})
        except Exception as e:
            logger.warning("api_tap wire_events query failed, falling back to JSONL: %s", e)

    cfg = get_config()
    wire_path = Path(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))
    if not wire_path.exists():
        return JSONResponse({"entries": [], "total": 0, "filtered": 0})
    entries = []
    try:
        with open(wire_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if role and entry.get("role") != role:
                        continue
                    if dir and entry.get("dir") != dir:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    pass
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    total = len(entries)
    entries = entries[-n:]
    return JSONResponse({"entries": entries, "total": total, "filtered": len(entries)})


@router.get("/api/v1/inspector")
async def api_inspector(n: int = 5):
    """Return the last N outbound LLM requests from the ring buffer, newest-first."""
    n = min(max(1, n), 5)
    proxy = get_state().proxy
    if proxy is None:
        return JSONResponse({"requests": [], "total": 0})
    entries = list(reversed(list(proxy._request_inspector)))[:n]
    logger.debug("inspector: returning %d entries (ring buffer size=%d)", len(entries), len(proxy._request_inspector))
    return JSONResponse({"requests": entries, "total": len(entries)})


# ── OpenRouter integration ───────────────────────────────────────────────

@router.get("/api/v1/openrouter/models")
async def openrouter_models_browse():
    """Fetch all OR models for the browser (rich data: name, context, pricing)."""
    _st = get_state()
    if not _st.backend_router:
        return JSONResponse({"error": "backends not enabled"}, status_code=400)
    or_backend = _st.backend_router.get_openrouter_backend()
    if not or_backend:
        return JSONResponse({"error": "no OpenRouter backend configured"}, status_code=404)
    models = await or_backend.list_models_details()
    return JSONResponse({"data": models})


@router.get("/api/v1/openrouter/pinned")
async def openrouter_pinned_get():
    """Return current pinned model IDs."""
    pinned = get_runtime_config().get("openrouter_pinned_models", [])
    return JSONResponse({"pinned": pinned})


@router.post("/api/v1/openrouter/pinned")
async def openrouter_pinned_save(request: Request):
    """Save pinned model list to runtime_config.yaml."""
    try:
        body = await request.json()
        pinned = body.get("pinned", [])
        if not isinstance(pinned, list):
            return JSONResponse({"error": "pinned must be a list"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    ok = update_runtime_config("openrouter_pinned_models", pinned)
    return JSONResponse({"ok": ok, "pinned": pinned})


@router.get("/api/v1/openrouter/balance")
async def openrouter_balance():
    """Fetch remaining credit balance from OpenRouter account API."""
    or_backend = get_state().backend_router.get_openrouter_backend() if get_state().backend_router else None
    if not or_backend:
        return JSONResponse({"balance": None, "error": "no OpenRouter backend configured"}, status_code=404)
    if not or_backend.api_key:
        return JSONResponse({"balance": None, "error": "no API key"}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {or_backend.api_key}"},
            )
            resp.raise_for_status()
            return JSONResponse(resp.json())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ── Artificial Analysis rankings (with 1-hour cache) ─────────────────────

_aa_cache: dict = {"data": None, "fetched_at": 0.0}
_AA_TTL = 3600


async def _fetch_aa_rankings() -> dict:
    """Scrape Artificial Analysis agentic/coding rankings from the public page."""
    now = time.time()
    if _aa_cache["data"] and (now - _aa_cache["fetched_at"]) < _AA_TTL:
        return _aa_cache["data"]

    url = "https://artificialanalysis.ai/models/capabilities/agentic"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.warning("AA fetch failed: %s", e)
        if _aa_cache["data"]:
            return _aa_cache["data"]
        return {"agentic": [], "coding": []}

    try:
        models = _parse_aa_models(html)
    except Exception as e:
        logger.warning("AA parse failed: %s", e)
        if _aa_cache["data"]:
            return _aa_cache["data"]
        return {"agentic": [], "coding": []}

    active = [m for m in models if not m.get("deprecated") and not m.get("deleted")]

    def _top15(field):
        scored = [m for m in active if m.get(field) is not None]
        scored.sort(key=lambda m: m[field], reverse=True)
        return [
            {
                "name": m.get("short_name") or m.get("name", ""),
                "creator": (m.get("model_creators") or {}).get("name", ""),
                "slug": m.get("slug", ""),
                "score": m[field],
                "agentic_index": m.get("agentic_index"),
                "coding_index": m.get("coding_index"),
            }
            for m in scored[:15]
        ]

    result = {"agentic": _top15("agentic_index"), "coding": _top15("coding_index")}
    _aa_cache["data"] = result
    _aa_cache["fetched_at"] = now
    logger.info("AA rankings refreshed: %d agentic, %d coding", len(result["agentic"]), len(result["coding"]))
    return result


def _parse_aa_models(html: str) -> list[dict]:
    """Extract model list from Artificial Analysis Next.js RSC payload."""
    marker = '\\"defaultData\\":'
    idx = html.find(marker)
    if idx < 0:
        raise ValueError("defaultData marker not found")

    push_prefix = 'self.__next_f.push([1,"'
    push_start = html.rfind(push_prefix, 0, idx)
    if push_start < 0:
        raise ValueError("push() wrapper not found")
    content_start = push_start + len(push_prefix)

    i = content_start
    while i < len(html):
        if html[i] == '"' and html[i - 1] != '\\':
            if html[i + 1: i + 3] == '])':
                break
        i += 1
    else:
        raise ValueError("push() terminator not found")

    js_string = html[content_start:i]
    unescaped = js_string.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

    dd_idx = unescaped.find('"defaultData":')
    if dd_idx < 0:
        raise ValueError("defaultData not found in unescaped content")
    arr_start = dd_idx + len('"defaultData":')
    remaining = unescaped[arr_start:]

    depth = 0
    in_str = False
    escape = False
    end = -1
    for j, ch in enumerate(remaining):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = j + 1
                break

    if end < 0:
        raise ValueError("array end not found")
    return json.loads(remaining[:end])


@router.get("/api/v1/artificial-analysis/rankings")
async def artificial_analysis_rankings():
    """Return top-15 agentic and coding model rankings from Artificial Analysis."""
    data = await _fetch_aa_rankings()
    return JSONResponse(data)
