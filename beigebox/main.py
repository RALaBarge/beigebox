"""
FastAPI application — the BeigeBox entry point.
Implements OpenAI-compatible endpoints that proxy to Ollama.

Now with:
  - Decision LLM initialization and preloading
  - Hook manager setup
  - Embedding model preloading
  - Enhanced stats with token tracking
  - Multi-backend routing with fallback (v0.6)
  - Cost tracking for API backends (v0.6)
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from beigebox.config import get_config, get_runtime_config, update_runtime_config
from beigebox.proxy import Proxy
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.tools.registry import ToolRegistry
from beigebox.agents.decision import DecisionAgent
from beigebox.agents.embedding_classifier import get_embedding_classifier
from beigebox.hooks import HookManager
from beigebox.backends.router import MultiBackendRouter
from beigebox.costs import CostTracker


# ---------------------------------------------------------------------------
# Globals — initialized at startup
# ---------------------------------------------------------------------------
proxy: Proxy | None = None
tool_registry: ToolRegistry | None = None
sqlite_store: SQLiteStore | None = None
vector_store: VectorStore | None = None
decision_agent: DecisionAgent | None = None
hook_manager: HookManager | None = None
backend_router: MultiBackendRouter | None = None
cost_tracker: CostTracker | None = None


def _setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        from pathlib import Path
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


async def _preload_embedding_model(cfg: dict):
    """Pin the embedding model in Ollama's memory at startup."""
    embed_cfg = cfg.get("embedding", {})
    model = embed_cfg.get("model", "")
    url = embed_cfg.get("backend_url", cfg["backend"]["url"]).rstrip("/")

    if not model:
        return

    logger = logging.getLogger(__name__)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": -1},
            )
            resp.raise_for_status()
            logger.info("Embedding model '%s' preloaded and pinned", model)
    except Exception as e:
        logger.warning("Failed to preload embedding model: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global proxy, tool_registry, sqlite_store, vector_store
    global decision_agent, hook_manager, backend_router, cost_tracker

    cfg = get_config()
    _setup_logging(cfg)
    logger = logging.getLogger(__name__)

    # Storage
    sqlite_store = SQLiteStore(cfg["storage"]["sqlite_path"])
    vector_store = VectorStore(
        chroma_path=cfg["storage"]["chroma_path"],
        embedding_model=cfg["embedding"]["model"],
        embedding_url=cfg["embedding"]["backend_url"],
    )

    # Tools (pass vector_store for the memory tool)
    tool_registry = ToolRegistry(vector_store=vector_store)

    # Decision Agent
    decision_agent = DecisionAgent.from_config(
        available_tools=tool_registry.list_tools()
    )

    # Hooks
    hooks_cfg = cfg.get("hooks", {})
    hook_manager = HookManager(
        hooks_dir=hooks_cfg.get("directory", "./hooks"),
        hook_configs=hooks_cfg.get("hooks", []),
    )

    # Embedding classifier (fast path for routing)
    embedding_classifier = get_embedding_classifier()
    ec_status = "ready" if embedding_classifier.ready else "no centroids — will auto-build at startup"

    # Multi-backend router (v0.6)
    backend_router = None
    if cfg.get("backends_enabled", False):
        backends_cfg = cfg.get("backends", [])
        if backends_cfg:
            backend_router = MultiBackendRouter(backends_cfg)
            logger.info("Multi-backend router: enabled (%d backends)", len(backend_router.backends))
        else:
            logger.warning("backends_enabled=true but no backends configured")
    else:
        logger.info("Multi-backend router: disabled")

    # Cost tracker (v0.6)
    cost_tracker = None
    if cfg.get("cost_tracking", {}).get("enabled", False):
        cost_tracker = CostTracker(sqlite_store)
        logger.info("Cost tracking: enabled")
    else:
        logger.info("Cost tracking: disabled")

    # Proxy (with decision agent, hooks, embedding classifier, tools, and router)
    proxy = Proxy(
        sqlite=sqlite_store,
        vector=vector_store,
        decision_agent=decision_agent,
        hook_manager=hook_manager,
        embedding_classifier=embedding_classifier,
        tool_registry=tool_registry,
        backend_router=backend_router,
    )

    logger.info(
        "BeigeBox started — listening on %s:%s, backend %s",
        cfg["server"]["host"],
        cfg["server"]["port"],
        cfg["backend"]["url"],
    )
    logger.info("Storage: SQLite=%s, Chroma=%s", cfg["storage"]["sqlite_path"], cfg["storage"]["chroma_path"])
    logger.info("Tools: %s", tool_registry.list_tools())
    logger.info("Hooks: %s", hook_manager.list_hooks())
    logger.info("Decision LLM: %s", "enabled" if decision_agent.enabled else "disabled")
    logger.info("Embedding classifier: %s", ec_status)
    logger.info("Z-commands: enabled (prefix messages with 'z: <directive>')")

    # Preload models in background
    await _preload_embedding_model(cfg)
    if decision_agent:
        await decision_agent.preload()

    # Auto-build centroids if they don't exist yet
    if not embedding_classifier.ready:
        import asyncio as _asyncio

        async def _auto_build_centroids():
            logger.info("Embedding centroids not found — auto-building in background…")
            try:
                success = embedding_classifier.build_centroids()
                if success:
                    logger.info("Embedding centroids auto-built successfully")
                else:
                    logger.warning("Auto-build centroids returned False — check Ollama is running with nomic-embed-text")
            except Exception as _e:
                logger.warning("Auto-build centroids failed: %s", _e)

        _asyncio.create_task(_auto_build_centroids())

    yield

    logger.info("BeigeBox shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="BeigeBox",
    description="Tap the line. Control the carrier.",
    version="0.8.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Main proxy endpoint. Accepts OpenAI-format chat completion requests,
    intercepts for logging/embedding, forwards to backend.
    """
    body = await request.json()
    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            proxy.forward_chat_completion_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        data = await proxy.forward_chat_completion(body)
        return JSONResponse(data)


@app.get("/v1/models")
async def list_models():
    """Forward model listing to backend."""
    data = await proxy.list_models()
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# BeigeBox-specific endpoints
# ---------------------------------------------------------------------------

@app.get("/beigebox/stats")
async def stats():
    """Return storage and usage statistics."""
    sqlite_stats = sqlite_store.get_stats() if sqlite_store else {}
    vector_stats = vector_store.get_stats() if vector_store else {}
    tools = tool_registry.list_tools() if tool_registry else []
    hooks = hook_manager.list_hooks() if hook_manager else []

    return JSONResponse({
        "sqlite": sqlite_stats,
        "vector": vector_stats,
        "tools": tools,
        "hooks": hooks,
        "decision_llm": {
            "enabled": decision_agent.enabled if decision_agent else False,
            "model": decision_agent.model if decision_agent else "",
        },
    })


@app.get("/beigebox/search")
async def search_conversations(q: str, n: int = 5, role: str | None = None):
    """Semantic search over stored conversations (raw message hits)."""
    if not vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = vector_store.search(q, n_results=n, role_filter=role)
    return JSONResponse({"query": q, "results": results})


@app.get("/api/v1/search")
async def api_search_conversations(q: str, n: int = 5, role: str | None = None):
    """
    Semantic search grouped by conversation.
    Returns conversations ranked by best message match, with excerpt.
    """
    if not vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = vector_store.search_grouped(q, n_conversations=n, role_filter=role)
    return JSONResponse({"query": q, "results": results, "count": len(results)})


@app.get("/beigebox/health")
async def health():
    """Health check."""
    return JSONResponse({
        "status": "ok",
        "version": "0.8.0",
        "decision_llm": decision_agent.enabled if decision_agent else False,
    })


# ---------------------------------------------------------------------------
# API v1 endpoints (for web UI and other clients)
# ---------------------------------------------------------------------------

@app.get("/api/v1/info")
async def api_info():
    """System info — what features are available."""
    cfg = get_config()
    return JSONResponse({
        "version": "0.8.0",
        "name": "BeigeBox",
        "description": "Transparent Pythonic LLM Proxy",
        "server": {
            "host": cfg["server"].get("host", "0.0.0.0"),
            "port": cfg["server"].get("port", 8000),
        },
        "backend": {
            "url": cfg["backend"].get("url", ""),
            "default_model": cfg["backend"].get("default_model", ""),
        },
        "features": {
            "routing": True,
            "decision_llm": decision_agent.enabled if decision_agent else False,
            "embedding_classifier": embedding_classifier.ready if embedding_classifier else False,
            "storage": sqlite_store is not None and vector_store is not None,
            "tools": tool_registry is not None and cfg.get("tools", {}).get("enabled", False),
            "hooks": hook_manager is not None,
            "operator": True,  # Always available if Operator can init
        },
        "model_advertising": cfg.get("model_advertising", {}).get("mode", "hidden"),
    })


@app.get("/api/v1/config")
async def api_config():
    """
    Full configuration — all config.yaml settings plus runtime overrides.
    Runtime keys (from runtime_config.yaml) take precedence where they exist.
    Safe to expose: no secrets, API keys are redacted.
    """
    cfg = get_config()
    rt = get_runtime_config()

    def _redact(v):
        """Redact anything that looks like an API key or password."""
        if isinstance(v, str) and len(v) > 8 and any(k in v.lower() for k in ("key", "secret", "token", "password")):
            return "***redacted***"
        return v

    # Merge runtime overrides onto config values
    return JSONResponse({
        # ── Backend ──────────────────────────────────────────────────
        "backend": {
            "url":           cfg.get("backend", {}).get("url", ""),
            "default_model": rt.get("default_model") or cfg.get("backend", {}).get("default_model", ""),
            "timeout":       cfg.get("backend", {}).get("timeout", 120),
        },
        # ── Server ───────────────────────────────────────────────────
        "server": {
            "host": cfg.get("server", {}).get("host", "0.0.0.0"),
            "port": cfg.get("server", {}).get("port", 8000),
        },
        # ── Embedding ────────────────────────────────────────────────
        "embedding": {
            "model":       cfg.get("embedding", {}).get("model", ""),
            "backend_url": cfg.get("embedding", {}).get("backend_url", ""),
        },
        # ── Storage ──────────────────────────────────────────────────
        "storage": {
            "sqlite_path":        cfg.get("storage", {}).get("sqlite_path", ""),
            "chroma_path":        cfg.get("storage", {}).get("chroma_path", ""),
            "log_conversations":  rt.get("log_conversations", cfg.get("storage", {}).get("log_conversations", True)),
        },
        # ── Tools ────────────────────────────────────────────────────
        "tools": {
            "enabled":      rt.get("tools_enabled", cfg.get("tools", {}).get("enabled", False)),
            "web_search":   cfg.get("tools", {}).get("web_search", {}),
            "web_scraper":  cfg.get("tools", {}).get("web_scraper", {}),
            "calculator":   cfg.get("tools", {}).get("calculator", {}),
            "datetime":     cfg.get("tools", {}).get("datetime", {}),
            "system_info":  cfg.get("tools", {}).get("system_info", {}),
            "memory":       cfg.get("tools", {}).get("memory", {}),
        },
        # ── Decision LLM ─────────────────────────────────────────────
        "decision_llm": {
            "enabled":     rt.get("decision_llm_enabled", decision_agent.enabled if decision_agent else False),
            "model":       decision_agent.model if decision_agent else cfg.get("decision_llm", {}).get("model", ""),
            "timeout":     cfg.get("decision_llm", {}).get("timeout", 5),
            "max_tokens":  cfg.get("decision_llm", {}).get("max_tokens", 256),
        },
        # ── Operator ─────────────────────────────────────────────────
        "operator": {
            "model":          cfg.get("operator", {}).get("model", ""),
            "max_iterations": cfg.get("operator", {}).get("max_iterations", 10),
            "shell_enabled":  cfg.get("operator", {}).get("shell", {}).get("enabled", False),
        },
        # ── Model advertising ─────────────────────────────────────────
        "model_advertising": cfg.get("model_advertising", {}),
        # ── Multi-backend ─────────────────────────────────────────────
        "backends_enabled": cfg.get("backends_enabled", False),
        "backends": [
            {k: ("***redacted***" if "key" in k.lower() else v)
             for k, v in b.items()}
            for b in cfg.get("backends", [])
        ],
        # ── Feature flags ─────────────────────────────────────────────
        "cost_tracking": {
            **cfg.get("cost_tracking", {}),
            "enabled": rt.get("cost_tracking_enabled", cfg.get("cost_tracking", {}).get("enabled", False)),
            "track_openrouter": rt.get("cost_track_openrouter", cfg.get("cost_tracking", {}).get("track_openrouter", True)),
            "track_local": rt.get("cost_track_local", cfg.get("cost_tracking", {}).get("track_local", False)),
        },
        "orchestrator": {
            **cfg.get("orchestrator", {}),
            "enabled": rt.get("orchestrator_enabled", cfg.get("orchestrator", {}).get("enabled", False)),
            "max_parallel_tasks": rt.get("orchestrator_max_parallel", cfg.get("orchestrator", {}).get("max_parallel_tasks", 5)),
            "task_timeout_seconds": rt.get("orchestrator_task_timeout", cfg.get("orchestrator", {}).get("task_timeout_seconds", 120)),
            "total_timeout_seconds": rt.get("orchestrator_total_timeout", cfg.get("orchestrator", {}).get("total_timeout_seconds", 300)),
        },
        "flight_recorder": {
            **cfg.get("flight_recorder", {}),
            "enabled": rt.get("flight_recorder_enabled", cfg.get("flight_recorder", {}).get("enabled", False)),
            "retention_hours": rt.get("flight_retention_hours", cfg.get("flight_recorder", {}).get("retention_hours", 24)),
            "max_records": rt.get("flight_max_records", cfg.get("flight_recorder", {}).get("max_records", 1000)),
        },
        "conversation_replay": {
            "enabled": rt.get("conversation_replay_enabled", cfg.get("conversation_replay", {}).get("enabled", False)),
        },
        "semantic_map": {
            **cfg.get("semantic_map", {}),
            "enabled": rt.get("semantic_map_enabled", cfg.get("semantic_map", {}).get("enabled", False)),
            "similarity_threshold": rt.get("semantic_similarity_threshold", cfg.get("semantic_map", {}).get("similarity_threshold", 0.5)),
            "max_topics": rt.get("semantic_max_topics", cfg.get("semantic_map", {}).get("max_topics", 50)),
        },
        "auto_summarization": {
            **cfg.get("auto_summarization", {}),
            "enabled": rt.get("auto_summarization_enabled", cfg.get("auto_summarization", {}).get("enabled", False)),
            "token_budget": rt.get("auto_token_budget", cfg.get("auto_summarization", {}).get("token_budget", 3000)),
            "summary_model": rt.get("auto_summary_model", cfg.get("auto_summarization", {}).get("summary_model", "")),
            "keep_last": rt.get("auto_keep_last", cfg.get("auto_summarization", {}).get("keep_last", 4)),
        },
        # ── Routing ───────────────────────────────────────────────────
        "routing": {
            "session_ttl_seconds": cfg.get("routing", {}).get("session_ttl_seconds", 1800),
            "force_route":         rt.get("force_route", ""),
            "border_threshold":    rt.get("border_threshold"),
            "agentic_threshold":   rt.get("agentic_threshold"),
        },
        # ── Logging ───────────────────────────────────────────────────
        "logging": {
            "level": rt.get("log_level") or cfg.get("logging", {}).get("level", "INFO"),
            "file":  cfg.get("logging", {}).get("file", ""),
        },
        # ── Wiretap ───────────────────────────────────────────────────
        "wiretap": cfg.get("wiretap", {}),
        # ── Hooks ─────────────────────────────────────────────────────
        "hooks": cfg.get("hooks", []),
        # ── Web UI (runtime only) ─────────────────────────────────────
        "web_ui": {
            "vi_mode":      rt.get("web_ui_vi_mode", False),
            "palette":      rt.get("web_ui_palette", "default"),
            "voice_enabled": rt.get("voice_enabled", False),
            "voice_hotkey":  rt.get("voice_hotkey", ""),
        },
        # ── Runtime session overrides ─────────────────────────────────
        "runtime": {
            "system_prompt_prefix": rt.get("system_prompt_prefix", ""),
            "tools_disabled":       rt.get("tools_disabled", []),
        },
    })


@app.post("/api/v1/config")
async def api_config_save(request: Request):
    """
    Save runtime-adjustable settings to runtime_config.yaml.
    All keys are hot-reloaded — no restart required.

    Accepted keys:
        # Web UI
        web_ui_vi_mode, web_ui_palette, voice_enabled, voice_hotkey
        # Routing
        default_model, force_route, border_threshold, agentic_threshold
        # Features
        decision_llm_enabled, tools_enabled, log_conversations, log_level
        # Session
        system_prompt_prefix, tools_disabled
        # Feature flags (bool)
        cost_tracking_enabled, orchestrator_enabled, flight_recorder_enabled,
        conversation_replay_enabled, semantic_map_enabled, auto_summarization_enabled
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    updated = []
    errors = []

    # All runtime-adjustable keys
    allowed = {
        # Web UI
        "web_ui_vi_mode":               "web_ui_vi_mode",
        "web_ui_palette":               "web_ui_palette",
        "voice_enabled":                "voice_enabled",
        "voice_hotkey":                 "voice_hotkey",
        # Routing
        "default_model":                "default_model",
        "force_route":                  "force_route",
        "border_threshold":             "border_threshold",
        "agentic_threshold":            "agentic_threshold",
        # Features
        "decision_llm_enabled":         "decision_llm_enabled",
        "tools_enabled":                "tools_enabled",
        "log_conversations":            "log_conversations",
        "log_level":                    "log_level",
        # Session
        "system_prompt_prefix":         "system_prompt_prefix",
        "tools_disabled":               "tools_disabled",
        # Feature flag sub-options
        "cost_tracking_enabled":        "cost_tracking_enabled",
        "cost_track_openrouter":        "cost_track_openrouter",
        "cost_track_local":             "cost_track_local",
        "orchestrator_enabled":         "orchestrator_enabled",
        "orchestrator_max_parallel":    "orchestrator_max_parallel",
        "orchestrator_task_timeout":    "orchestrator_task_timeout",
        "orchestrator_total_timeout":   "orchestrator_total_timeout",
        "flight_recorder_enabled":      "flight_recorder_enabled",
        "flight_retention_hours":       "flight_retention_hours",
        "flight_max_records":           "flight_max_records",
        "conversation_replay_enabled":  "conversation_replay_enabled",
        "semantic_map_enabled":         "semantic_map_enabled",
        "semantic_similarity_threshold": "semantic_similarity_threshold",
        "semantic_max_topics":          "semantic_max_topics",
        "auto_summarization_enabled":   "auto_summarization_enabled",
        "auto_token_budget":            "auto_token_budget",
        "auto_summary_model":           "auto_summary_model",
        "auto_keep_last":               "auto_keep_last",
    }

    for key, rt_key in allowed.items():
        if key in body:
            ok = update_runtime_config(rt_key, body[key])
            if ok:
                updated.append(key)
            else:
                errors.append(key)

    # Apply live changes that don't need restart
    rt = get_runtime_config()

    if "decision_llm_enabled" in updated and decision_agent:
        decision_agent.enabled = rt.get("decision_llm_enabled", decision_agent.enabled)

    if "default_model" in updated and proxy:
        proxy.default_model = rt.get("default_model", proxy.default_model)

    if "log_conversations" in updated and proxy:
        proxy.log_enabled = rt.get("log_conversations", proxy.log_enabled)

    if errors:
        return JSONResponse({"saved": updated, "errors": errors}, status_code=207)
    return JSONResponse({"saved": updated, "ok": True})



    """List available tools."""
    if not tool_registry:
        return JSONResponse({"tools": []})
    tools = tool_registry.list_tools()
    return JSONResponse({
        "tools": tools,
        "enabled": get_config().get("tools", {}).get("enabled", False),
    })


@app.get("/api/v1/status")
async def api_status():
    """Detailed status of all subsystems."""
    cfg = get_config()
    return JSONResponse({
        "proxy": {
            "running": proxy is not None,
            "backend_url": proxy.backend_url if proxy else "",
            "default_model": proxy.default_model if proxy else "",
        },
        "storage": {
            "sqlite": sqlite_store is not None,
            "vector": vector_store is not None,
            "stats": sqlite_store.get_stats() if sqlite_store else {},
        },
        "routing": {
            "decision_llm": {
                "enabled": decision_agent.enabled if decision_agent else False,
                "model": decision_agent.model if decision_agent else "",
            },
            "embedding_classifier": {
                "ready": embedding_classifier.ready if embedding_classifier else False,
            },
        },
        "tools": {
            "enabled": cfg.get("tools", {}).get("enabled", False),
            "available": tool_registry.list_tools() if tool_registry else [],
        },
        "operator": {
            "model": cfg.get("operator", {}).get("model", ""),
            "shell_enabled": cfg.get("operator", {}).get("shell", {}).get("enabled", False),
        },
    })


@app.get("/api/v1/stats")
async def api_stats():
    """Statistics about conversations and usage."""
    sqlite_stats = sqlite_store.get_stats() if sqlite_store else {}
    vector_stats = vector_store.get_stats() if vector_store else {}
    return JSONResponse({
        "conversations": sqlite_stats,
        "embeddings": vector_stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/v1/costs")
async def api_costs(days: int = 30):
    """
    Cost tracking stats.
    Query params: ?days=30 (default 30)
    Returns total, by_model, by_day, by_conversation breakdown.
    """
    if not cost_tracker:
        return JSONResponse({
            "enabled": False,
            "message": "Cost tracking is disabled. Set cost_tracking.enabled: true in config.",
        })
    stats = cost_tracker.get_stats(days=days)
    stats["enabled"] = True
    return JSONResponse(stats)


@app.get("/api/v1/model-performance")
async def api_model_performance(days: int = 30):
    """
    Per-model latency and throughput stats.

    Query params:
        days  int  — lookback window (default 30)

    Returns avg/p50/p95 latency, request count, avg tokens, and total cost per model.
    Note: latency data is only recorded for non-streaming requests via the
    multi-backend router. Streaming latency tracking is approximate.
    """
    if not sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)
    data = sqlite_store.get_model_performance(days=days)
    data["enabled"] = True
    return JSONResponse(data)


@app.get("/api/v1/backends")
async def api_backends():
    """Health and status of all configured backends."""
    if not backend_router:
        cfg = get_config()
        return JSONResponse({
            "enabled": False,
            "message": "Multi-backend routing is disabled. Set backends_enabled: true in config.",
            "primary_backend": cfg.get("backend", {}).get("url", ""),
        })
    health = await backend_router.health()
    return JSONResponse({
        "enabled": True,
        "backends": health,
    })


# ---------------------------------------------------------------------------
# Flight Recorder (v0.6)
# ---------------------------------------------------------------------------

@app.get("/api/v1/flight-recorder")
async def api_flight_recorder_list(n: int = 10):
    """List recent flight records."""
    if not proxy or not proxy.flight_store:
        return JSONResponse({
            "enabled": False,
            "message": "Flight recorder is disabled. Set flight_recorder.enabled: true in config.",
        })
    records = proxy.flight_store.recent(n=n)
    return JSONResponse({
        "enabled": True,
        "count": proxy.flight_store.count,
        "records": [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "model": r.model,
                "total_ms": r.total_ms,
                "events": len(r.events),
            }
            for r in records
        ],
    })


@app.get("/api/v1/flight-recorder/{record_id}")
async def api_flight_recorder_detail(record_id: str):
    """Get detailed flight record by ID."""
    if not proxy or not proxy.flight_store:
        return JSONResponse({
            "enabled": False,
            "message": "Flight recorder is disabled.",
        })
    record = proxy.flight_store.get(record_id)
    if not record:
        return JSONResponse({"error": "Record not found"}, status_code=404)
    result = record.to_json()
    result["text"] = record.render_text()
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Conversation Replay (v0.6)
# ---------------------------------------------------------------------------

@app.get("/api/v1/conversation/{conv_id}/replay")
async def api_conversation_replay(conv_id: str):
    """Reconstruct a conversation with full routing context."""
    cfg = get_config()
    if not cfg.get("conversation_replay", {}).get("enabled", False):
        return JSONResponse({
            "enabled": False,
            "message": "Conversation replay is disabled. Set conversation_replay.enabled: true in config.",
        })
    if not sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    from beigebox.replay import ConversationReplayer
    wire_path = cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")
    replayer = ConversationReplayer(sqlite_store, wiretap_path=wire_path)
    result = replayer.replay(conv_id)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Conversation Fork (v0.7)
# ---------------------------------------------------------------------------

@app.post("/api/v1/conversation/{conv_id}/fork")
async def api_conversation_fork(conv_id: str, request: Request):
    """
    Fork a conversation into a new one.

    Body (JSON, all optional):
        branch_at  int   — 0-based message index to branch at (inclusive).
                           Omit to copy the full conversation.

    Returns:
        new_conversation_id  str
        messages_copied      int
        source_conversation  str
    """
    if not sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    branch_at = body.get("branch_at")  # None → full copy
    if branch_at is not None:
        try:
            branch_at = int(branch_at)
        except (ValueError, TypeError):
            return JSONResponse({"error": "branch_at must be an integer"}, status_code=400)

    from uuid import uuid4
    new_conv_id = uuid4().hex

    try:
        copied = sqlite_store.fork_conversation(
            source_conv_id=conv_id,
            new_conv_id=new_conv_id,
            branch_at=branch_at,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    if copied == 0:
        return JSONResponse(
            {"error": f"Conversation '{conv_id}' not found or empty"},
            status_code=404,
        )

    return JSONResponse({
        "new_conversation_id": new_conv_id,
        "messages_copied": copied,
        "source_conversation": conv_id,
        "branch_at": branch_at,
    })


# ---------------------------------------------------------------------------
# Tap — wire log reader with filters (v0.7)
# ---------------------------------------------------------------------------

@app.get("/api/v1/tap")
async def api_tap(
    n: int = 50,
    role: str | None = None,
    dir: str | None = None,
):
    """
    Return recent wire log entries with optional filters.

    Query params:
        n     int    — max entries to return (default 50, max 500)
        role  str    — filter by role: user|assistant|system|decision|tool
        dir   str    — filter by direction: inbound|outbound|internal
    """
    import json as _json
    from pathlib import Path as _P
    cfg = get_config()
    wire_path = _P(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))

    if not wire_path.exists():
        return JSONResponse({"entries": [], "total": 0, "filtered": 0})

    n = min(max(1, n), 500)
    entries = []
    try:
        with open(wire_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                    if role and entry.get("role") != role:
                        continue
                    if dir and entry.get("dir") != dir:
                        continue
                    entries.append(entry)
                except _json.JSONDecodeError:
                    pass
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    total = len(entries)
    entries = entries[-n:]
    return JSONResponse({"entries": entries, "total": total, "filtered": len(entries)})


@app.get("/api/v1/conversation/{conv_id}/semantic-map")
async def api_semantic_map(conv_id: str):
    """Generate a semantic topic map for a conversation."""
    cfg = get_config()
    sm_cfg = cfg.get("semantic_map", {})
    if not sm_cfg.get("enabled", False):
        return JSONResponse({
            "enabled": False,
            "message": "Semantic map is disabled. Set semantic_map.enabled: true in config.",
        })
    if not sqlite_store or not vector_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    from beigebox.semantic_map import SemanticMap
    mapper = SemanticMap(
        sqlite=sqlite_store,
        vector=vector_store,
        similarity_threshold=sm_cfg.get("similarity_threshold", 0.5),
        max_topics=sm_cfg.get("max_topics", 50),
    )
    result = mapper.build(conv_id)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Orchestrator (v0.6)
# ---------------------------------------------------------------------------

@app.post("/api/v1/orchestrator")
async def api_orchestrator(request: Request):
    """
    Run parallel LLM tasks.
    Body: {"plan": [{"model": "code", "prompt": "..."}, ...]}
    """
    cfg = get_config()
    orch_cfg = cfg.get("orchestrator", {})
    if not orch_cfg.get("enabled", False):
        return JSONResponse({
            "enabled": False,
            "message": "Orchestrator is disabled. Set orchestrator.enabled: true in config.",
        })

    try:
        body = await request.json()
        plan = body.get("plan", [])
        if not plan:
            return JSONResponse({"error": "plan required (array of tasks)"}, status_code=400)

        from beigebox.orchestrator import ParallelOrchestrator
        orchestrator = ParallelOrchestrator(
            max_parallel_tasks=orch_cfg.get("max_parallel_tasks", 5),
            task_timeout_seconds=orch_cfg.get("task_timeout_seconds", 120),
            total_timeout_seconds=orch_cfg.get("total_timeout_seconds", 300),
        )
        result = await orchestrator.run(plan)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/harness/orchestrate")
async def api_harness_orchestrate(request: Request):
    """
    Harness master orchestrator — goal-directed multi-agent coordinator.

    Body:
        query    str           — the goal/task to accomplish
        targets  list[str]     — available targets e.g. ["operator","model:llama3.2:3b"]
        model    str (opt)     — override the orchestrator's own model
        max_rounds int (opt)   — override max iteration rounds (default 8)

    Returns: text/event-stream of JSON events:
        {type:"start",    goal, model, targets}
        {type:"plan",     round, reasoning, tasks:[{target,prompt,rationale}]}
        {type:"dispatch", round, task_count}
        {type:"result",   round, target, prompt, content, latency_ms, status}
        {type:"evaluate", round, assessment, action}
        {type:"finish",   answer, rounds, capped?}
        {type:"error",    message}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    goal = body.get("query", "").strip()
    if not goal:
        return JSONResponse({"error": "query required"}, status_code=400)

    targets = body.get("targets", ["operator"])
    model_override = body.get("model") or None
    max_rounds = int(body.get("max_rounds", 8))
    task_stagger = float(body.get("task_stagger_seconds", 0.4))

    from beigebox.agents.harness_orchestrator import HarnessOrchestrator
    import json as _json

    orch = HarnessOrchestrator(
        available_targets=targets,
        model=model_override,
        max_rounds=max_rounds,
        task_stagger_seconds=task_stagger,
    )

    async def _event_stream():
        try:
            async for event in orch.run(goal):
                yield f"data: {_json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/operator")
async def api_operator(request: Request):
    """
    Run the operator agent.
    Body: {"query": "your question"}
    """
    try:
        body = await request.json()
        question = body.get("query", "").strip()
        if not question:
            return JSONResponse({"error": "query required"}, status_code=400)
        
        from beigebox.storage.vector_store import VectorStore
        from beigebox.agents.operator import Operator
        
        cfg = get_config()
        try:
            vs = VectorStore(
                chroma_path=cfg["storage"]["chroma_path"],
                embedding_model=cfg["embedding"]["model"],
                embedding_url=cfg["embedding"]["backend_url"],
            )
        except:
            vs = None
        
        try:
            op = Operator(vector_store=vs)
            answer = op.run(question)
            return JSONResponse({
                "success": True,
                "query": question,
                "answer": answer,
            })
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e),
                "query": question,
            }, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------------------------------------------------------------------
# Web UI settings
# ---------------------------------------------------------------------------

@app.post("/api/v1/build-centroids")
async def api_build_centroids():
    """
    Rebuild embedding classifier centroids from seed prompts.
    Equivalent to `beigebox build-centroids` CLI command.
    Runs synchronously — may take 10-30s depending on embedding model speed.
    """
    if not embedding_classifier:
        return JSONResponse({"success": False, "error": "Embedding classifier not initialized"}, status_code=503)
    try:
        success = embedding_classifier.build_centroids()
        if success:
            return JSONResponse({"success": True, "message": "Centroids built successfully"})
        else:
            return JSONResponse({"success": False, "error": "build_centroids() returned False — check Ollama is running"}, status_code=500)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)



async def toggle_vi_mode():
    """Toggle vi mode in runtime_config.yaml. Returns new state."""
    rt = get_runtime_config()
    current = rt.get("web_ui_vi_mode", False)
    new_val = not current
    ok = update_runtime_config("web_ui_vi_mode", new_val)
    return JSONResponse({"vi_mode": new_val, "ok": ok})


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


# ---------------------------------------------------------------------------
# Known OpenAI-compatible endpoints — explicit routes for observability.
# These all forward to the backend but are logged to wiretap.
# Catch-all below handles anything not listed here.
# ---------------------------------------------------------------------------

async def _wire_and_forward(request: Request, route_label: str) -> StreamingResponse:
    """
    Generic forward: log to wiretap, stream response from backend verbatim.
    Used for all known-but-not-specially-handled OpenAI/Ollama endpoints.
    """
    cfg = get_config()
    backend_url = cfg["backend"]["url"].rstrip("/")
    path = request.url.path
    query = request.url.query
    target = f"{backend_url}{path}"
    if query:
        target += f"?{query}"

    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    # Wire log entry via proxy.wire (WireLog)
    if proxy and proxy.wire:
        try:
            body_preview = body[:400].decode("utf-8", errors="replace") if body else ""
        except Exception:
            body_preview = ""
        proxy.wire.log(
            direction="internal",
            role="proxy",
            content=f"[{request.method}] {route_label} → {target}\n{body_preview}",
            model="",
            conversation_id="",
        )

    async def _stream():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                request.method,
                target,
                headers=headers,
                content=body,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(_stream(), media_type="application/octet-stream")


# OpenAI Audio — STT / TTS (forwarded to configured voice services or backend)
@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(request: Request):
    """STT — forward to configured STT service or backend."""
    return await _wire_and_forward(request, "audio/transcriptions")

@app.post("/v1/audio/speech")
async def audio_speech(request: Request):
    """TTS — forward to configured TTS service or backend."""
    return await _wire_and_forward(request, "audio/speech")

@app.post("/v1/audio/translations")
async def audio_translations(request: Request):
    """Audio translation — forward to backend."""
    return await _wire_and_forward(request, "audio/translations")

# OpenAI Embeddings
@app.post("/v1/embeddings")
async def embeddings(request: Request):
    """Embeddings — forward to backend, logged."""
    return await _wire_and_forward(request, "embeddings")

# OpenAI legacy completions (non-chat)
@app.post("/v1/completions")
async def completions(request: Request):
    """Legacy completions — forward to backend."""
    return await _wire_and_forward(request, "completions")

# OpenAI Files / Fine-tuning / Assistants (future-proofing)
@app.api_route("/v1/files/{path:path}", methods=["GET","POST","DELETE"])
async def files_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"files/{path}")

@app.api_route("/v1/fine_tuning/{path:path}", methods=["GET","POST","DELETE"])
async def fine_tuning_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"fine_tuning/{path}")

@app.api_route("/v1/assistants/{path:path}", methods=["GET","POST","DELETE","PUT"])
async def assistants_passthrough(path: str, request: Request):
    return await _wire_and_forward(request, f"assistants/{path}")

# Ollama-native endpoints (used by some frontends that speak Ollama directly)
@app.api_route("/api/tags", methods=["GET"])
async def ollama_tags(request: Request):
    """Ollama model list — forward and log."""
    return await _wire_and_forward(request, "ollama/tags")

@app.api_route("/api/chat", methods=["POST"])
async def ollama_chat(request: Request):
    """Ollama native chat — forward and log."""
    return await _wire_and_forward(request, "ollama/chat")

@app.api_route("/api/generate", methods=["POST"])
async def ollama_generate(request: Request):
    """Ollama native generate — forward and log."""
    return await _wire_and_forward(request, "ollama/generate")

@app.api_route("/api/pull", methods=["POST"])
async def ollama_pull(request: Request):
    """Ollama model pull — forward and log."""
    return await _wire_and_forward(request, "ollama/pull")

@app.api_route("/api/push", methods=["POST"])
async def ollama_push(request: Request):
    return await _wire_and_forward(request, "ollama/push")

@app.api_route("/api/delete", methods=["DELETE","POST"])
async def ollama_delete(request: Request):
    return await _wire_and_forward(request, "ollama/delete")

@app.api_route("/api/copy", methods=["POST"])
async def ollama_copy(request: Request):
    return await _wire_and_forward(request, "ollama/copy")

@app.api_route("/api/show", methods=["POST"])
async def ollama_show(request: Request):
    return await _wire_and_forward(request, "ollama/show")

@app.api_route("/api/embed", methods=["POST"])
async def ollama_embed(request: Request):
    """Ollama embed — forward and log."""
    return await _wire_and_forward(request, "ollama/embed")

@app.api_route("/api/embeddings", methods=["POST"])
async def ollama_embeddings(request: Request):
    return await _wire_and_forward(request, "ollama/embeddings")

@app.api_route("/api/ps", methods=["GET"])
async def ollama_ps(request: Request):
    return await _wire_and_forward(request, "ollama/ps")

@app.api_route("/api/version", methods=["GET"])
async def ollama_version(request: Request):
    return await _wire_and_forward(request, "ollama/version")

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
