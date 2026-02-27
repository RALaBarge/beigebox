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
import time
import json
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
embedding_classifier = None


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
    global embedding_classifier

    cfg = get_config()
    _setup_logging(cfg)
    logger = logging.getLogger(__name__)

    # Storage
    sqlite_store = SQLiteStore(cfg["storage"]["sqlite_path"])
    _storage_cfg  = cfg["storage"]
    _embed_cfg    = cfg["embedding"]
    _backend_type = _storage_cfg.get("vector_backend", "chromadb")
    _backend_path = _storage_cfg.get("chroma_path") or _storage_cfg.get("vector_store_path", "./data/chroma")
    from beigebox.storage.backends import make_backend as _make_backend
    vector_store = VectorStore(
        embedding_model=_embed_cfg["model"],
        embedding_url=_embed_cfg.get("backend_url") or cfg["backend"]["url"],
        backend=_make_backend(_backend_type, path=_backend_path),
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
    op_enabled = cfg.get("operator", {}).get("enabled", False)
    if op_enabled:
        op_allowed = cfg.get("operator", {}).get("allowed_tools", [])
        scope = f"restricted to {op_allowed}" if op_allowed else "ALL registered tools"
        logger.warning("Operator agent: ENABLED — LLM-driven tool execution active (%s)", scope)
    else:
        logger.info("Operator agent: disabled (set operator.enabled: true to activate)")

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
    version="1.0.0",
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
        "version": "1.0.0",
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
        "version": "1.0.0",
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
        "conversation_replay": {
            "enabled": rt.get("conversation_replay_enabled", cfg.get("conversation_replay", {}).get("enabled", False)),
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
        # ── Voice / Audio ─────────────────────────────────────────────
        "voice": {
            "stt_url":      rt.get("stt_url") or cfg.get("voice", {}).get("stt_url", ""),
            "tts_url":      rt.get("tts_url") or cfg.get("voice", {}).get("tts_url", ""),
            "tts_model":    rt.get("tts_model") or cfg.get("voice", {}).get("tts_model", "tts-1"),
            "tts_voice":    rt.get("tts_voice") or cfg.get("voice", {}).get("tts_voice", "alloy"),
            "tts_speed":    rt.get("tts_speed") or cfg.get("voice", {}).get("tts_speed", 1.0),
            "tts_autoplay": rt.get("tts_autoplay", cfg.get("voice", {}).get("tts_autoplay", False)),
        },
        # ── Runtime session overrides ─────────────────────────────────
        "runtime": {
            "system_prompt_prefix": rt.get("system_prompt_prefix", ""),
            "tools_disabled":       rt.get("tools_disabled", []),
        },
        # ── System Context ────────────────────────────────────────────
        "system_context": {
            "enabled": rt.get("system_context_enabled", cfg.get("system_context", {}).get("enabled", False)),
            "path":    cfg.get("system_context", {}).get("path", "./system_context.md"),
        },
        # ── Generation Parameters ─────────────────────────────────────
        "generation": {
            "temperature":    rt.get("gen_temperature"),
            "top_p":          rt.get("gen_top_p"),
            "top_k":          rt.get("gen_top_k"),
            "num_ctx":        rt.get("gen_num_ctx"),
            "repeat_penalty": rt.get("gen_repeat_penalty"),
            "max_tokens":     rt.get("gen_max_tokens"),
            "seed":           rt.get("gen_seed"),
            "stop":           rt.get("gen_stop"),
            "force":          rt.get("gen_force", False),
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
        cost_tracking_enabled, orchestrator_enabled,
        conversation_replay_enabled, auto_summarization_enabled
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
        "conversation_replay_enabled":  "conversation_replay_enabled",
        "auto_summarization_enabled":   "auto_summarization_enabled",
        "auto_token_budget":            "auto_token_budget",
        "auto_summary_model":           "auto_summary_model",
        "auto_keep_last":               "auto_keep_last",
        # System context
        "system_context_enabled":       "system_context_enabled",
        # Generation parameters
        "gen_temperature":              "gen_temperature",
        "gen_top_p":                    "gen_top_p",
        "gen_top_k":                    "gen_top_k",
        "gen_num_ctx":                  "gen_num_ctx",
        "gen_repeat_penalty":           "gen_repeat_penalty",
        "gen_max_tokens":               "gen_max_tokens",
        "gen_seed":                     "gen_seed",
        "gen_stop":                     "gen_stop",
        "gen_force":                    "gen_force",
        # Voice / Audio
        "stt_url":                      "stt_url",
        "tts_url":                      "tts_url",
        "tts_model":                    "tts_model",
        "tts_voice":                    "tts_voice",
        "tts_speed":                    "tts_speed",
        "tts_autoplay":                 "tts_autoplay",
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


@app.get("/api/v1/system-context")
async def api_get_system_context():
    """
    Return the current contents of system_context.md.
    Returns empty string if file does not exist yet.
    """
    cfg = get_config()
    from beigebox.system_context import read_context_file
    content = read_context_file(cfg)
    rt = get_runtime_config()
    sc_cfg = cfg.get("system_context", {})
    enabled = rt.get("system_context_enabled", sc_cfg.get("enabled", False))
    return JSONResponse({
        "content": content,
        "enabled": enabled,
        "path": sc_cfg.get("path", "./system_context.md"),
        "length": len(content),
    })


@app.post("/api/v1/system-context")
async def api_set_system_context(request: Request):
    """
    Write new contents to system_context.md.
    Hot-reloads immediately — next proxied request picks it up.

    Body: {"content": "You are a helpful assistant..."}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    cfg = get_config()
    from beigebox.system_context import write_context_file
    ok = write_context_file(cfg, content)
    if ok:
        return JSONResponse({"ok": True, "length": len(content)})
    return JSONResponse({"error": "failed to write system_context.md"}, status_code=500)


@app.post("/api/v1/generation-params/reset")
async def api_reset_generation_params():
    """
    Clear all runtime generation parameters (temperature, top_p, etc.)
    so requests are forwarded with whatever the frontend sends.
    """
    gen_keys = [
        "gen_temperature", "gen_top_p", "gen_top_k", "gen_num_ctx",
        "gen_repeat_penalty", "gen_max_tokens", "gen_seed", "gen_stop", "gen_force",
    ]
    cleared = []
    for key in gen_keys:
        if update_runtime_config(key, None):
            cleared.append(key)
    return JSONResponse({"cleared": cleared, "ok": True})



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


@app.get("/api/v1/export")
async def api_export(
    format: str = "jsonl",
    model: str | None = None,
):
    """
    Export conversations for fine-tuning.

    Query params:
        format  str  — jsonl | alpaca | sharegpt  (default: jsonl)
        model   str  — filter to a specific model (optional)

    Returns a JSON file download.

    Formats:
        jsonl      — {"messages": [{"role": ..., "content": ...}]} per conversation
        alpaca     — {"instruction": ..., "input": "", "output": ...} per turn pair
        sharegpt   — {"id": ..., "conversations": [{"from": "human"|"gpt", "value": ...}]}
    """
    if not sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    fmt = format.lower().strip()
    if fmt not in ("jsonl", "alpaca", "sharegpt"):
        return JSONResponse(
            {"error": f"Unknown format '{fmt}'. Use: jsonl, alpaca, sharegpt"},
            status_code=400,
        )

    model_filter = model or None

    if fmt == "jsonl":
        data = sqlite_store.export_jsonl(model_filter)
        filename = "conversations.jsonl"
        # True JSONL: one JSON object per line
        content = "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n"
        media_type = "application/x-ndjson"
    elif fmt == "alpaca":
        data = sqlite_store.export_alpaca(model_filter)
        filename = "conversations_alpaca.json"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:  # sharegpt
        data = sqlite_store.export_sharegpt(model_filter)
        filename = "conversations_sharegpt.json"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"

    from fastapi.responses import Response
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@app.get("/api/v1/routing-stats")
async def api_routing_stats(lines: int = 10000):
    """
    Session-cache hit rate from the wiretap.

    Scans the tail of wire.jsonl (default last 10 000 entries) and counts:
      - cache_hits   — requests served directly from session cache
      - cache_misses — requests that required a real routing decision
                       (embedding classifier or decision LLM)

    Returns:
        {
            "cache_hits":    int,
            "cache_misses":  int,
            "total":         int,
            "hit_rate":      float,   # 0.0 – 1.0
            "sampled_lines": int,
        }
    """
    import json as _json
    from pathlib import Path as _Path

    cfg = get_config()
    wire_path = _Path(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))

    hits = misses = sampled = 0

    if wire_path.exists():
        try:
            # Read tail efficiently — open, seek from end, grab last `lines` newlines
            with open(wire_path, "rb") as fh:
                # Estimate ~200 bytes/entry; read enough to cover `lines` entries
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
                    entry = _json.loads(line)
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
        "cache_hits":    hits,
        "cache_misses":  misses,
        "total":         total,
        "hit_rate":      round(hits / total, 4) if total else 0.0,
        "sampled_lines": sampled,
    })


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


# ---------------------------------------------------------------------------
# Conversation Replay (v0.6)
# ---------------------------------------------------------------------------

@app.get("/api/v1/conversation/{conv_id}/replay")
async def api_conversation_replay(conv_id: str):
    """Reconstruct a conversation with full routing context."""
    cfg = get_config()
    rt = get_runtime_config()
    # Check runtime config first, fall back to static config
    if "conversation_replay_enabled" in rt:
        replay_enabled = rt.get("conversation_replay_enabled")
    else:
        replay_enabled = cfg.get("conversation_replay", {}).get("enabled", False)
    if not replay_enabled:
        return JSONResponse({
            "enabled": False,
            "message": "Conversation replay is disabled. Enable it in Config tab or set conversation_replay.enabled: true in config.yaml.",
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
    Harness master orchestrator — goal-directed multi-agent coordinator with run persistence.

    Body:
        query    str           — the goal/task to accomplish
        targets  list[str]     — available targets e.g. ["operator","model:llama3.2:3b"]
        model    str (opt)     — override the orchestrator's own model
        max_rounds int (opt)   — override max iteration rounds (default 8)

    Returns: text/event-stream of JSON events:
        {type:"start",    run_id, goal, model, targets}
        {type:"plan",     round, reasoning, tasks:[{target,prompt,rationale}]}
        {type:"dispatch", round, task_count}
        {type:"result",   round, target, prompt, content, latency_ms, status, error_type?, attempts}
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
    from datetime import datetime, timezone

    orch = HarnessOrchestrator(
        available_targets=targets,
        model=model_override,
        max_rounds=max_rounds,
        task_stagger_seconds=task_stagger,
    )

    async def _event_stream():
        events = []
        error_count = 0
        start_ts = time.time()
        run_id = None
        
        try:
            async for event in orch.run(goal):
                events.append(event)
                if event.get("type") == "error":
                    error_count += 1
                if event.get("type") == "start":
                    run_id = event.get("run_id")
                yield f"data: {_json.dumps(event)}\n\n"
        except Exception as e:
            error_event = {'type':'error','message':str(e)}
            events.append(error_event)
            error_count += 1
            yield f"data: {_json.dumps(error_event)}\n\n"
        finally:
            # Store run after completion
            if orch.store_runs:
                try:
                    from beigebox.storage.sqlite_store import SQLiteStore
                    cfg = get_config()
                    store = SQLiteStore(cfg["storage"]["db_path"])
                    
                    total_latency = round((time.time() - start_ts) * 1000)
                    final_answer = ""
                    total_rounds = 0
                    was_capped = False
                    
                    # Extract final answer and metadata from finish event
                    for event in reversed(events):
                        if event.get("type") == "finish":
                            final_answer = event.get("answer", "")
                            total_rounds = event.get("rounds", 0)
                            was_capped = event.get("capped", False)
                            break
                    
                    run_record = {
                        "id": run_id or "unknown",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "goal": goal,
                        "targets": targets,
                        "model": orch.model,
                        "max_rounds": orch.max_rounds,
                        "final_answer": final_answer,
                        "total_rounds": total_rounds,
                        "was_capped": was_capped,
                        "total_latency_ms": total_latency,
                        "error_count": error_count,
                        "events_jsonl": "\n".join(_json.dumps(e) for e in events),
                    }
                    store.store_harness_run(run_record)
                    logger.info(f"Stored harness run {run_id} (goal={goal[:50]}, rounds={total_rounds}, errors={error_count})")
                except Exception as e:
                    logger.error(f"Failed to store harness run: {e}")
        
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


@app.get("/api/v1/harness/{run_id}")
def get_harness_run(run_id: str):
    """
    Retrieve a stored harness orchestration run by ID.
    
    Returns the full run record including all events for replay/analysis.
    """
    try:
        from beigebox.storage.sqlite_store import SQLiteStore
        cfg = get_config()
        store = SQLiteStore(cfg["storage"]["db_path"])
        
        run = store.get_harness_run(run_id)
        if not run:
            return JSONResponse(
                {"error": f"Harness run '{run_id}' not found"},
                status_code=404
            )
        
        return {
            "id": run["id"],
            "created_at": run["created_at"],
            "goal": run["goal"],
            "targets": json.loads(run["targets"]) if isinstance(run["targets"], str) else run["targets"],
            "model": run["model"],
            "max_rounds": run["max_rounds"],
            "final_answer": run["final_answer"],
            "total_rounds": run["total_rounds"],
            "was_capped": run["was_capped"],
            "total_latency_ms": run["total_latency_ms"],
            "error_count": run["error_count"],
            "events": run.get("events", []),
        }
    except Exception as e:
        logger.error(f"Failed to retrieve harness run: {e}")
        return JSONResponse(
            {"error": f"Failed to retrieve run: {str(e)}"},
            status_code=500
        )


@app.get("/api/v1/harness")
def list_harness_runs(limit: int = 10):
    """
    List recent harness orchestration runs.
    
    Query parameters:
        limit: int (default 10, max 100) — number of runs to return
    
    Returns a list of recent runs with metadata (without full event logs).
    """
    try:
        # Clamp limit
        limit = min(max(limit, 1), 100)
        
        from beigebox.storage.sqlite_store import SQLiteStore
        cfg = get_config()
        store = SQLiteStore(cfg["storage"]["db_path"])
        
        runs = store.list_harness_runs(limit=limit)
        
        return {
            "count": len(runs),
            "limit": limit,
            "runs": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "goal": r["goal"][:100] if r["goal"] else "",  # Truncate for list view
                    "total_rounds": r["total_rounds"],
                    "total_latency_ms": r["total_latency_ms"],
                    "error_count": r["error_count"],
                    "was_capped": r["was_capped"],
                }
                for r in runs
            ]
        }
    except Exception as e:
        logger.error(f"Failed to list harness runs: {e}")
        return JSONResponse(
            {"error": f"Failed to list runs: {str(e)}"},
            status_code=500
        )


@app.post("/api/v1/operator")
async def api_operator(request: Request):
    """
    Run the operator agent.
    Body: {"query": "your question"}
    """
    cfg = get_config()
    if not cfg.get("operator", {}).get("enabled", False):
        return JSONResponse(
            {"error": "Operator is disabled. Set operator.enabled: true in config.yaml to enable LLM-driven tool execution."},
            status_code=403,
        )
    try:
        body = await request.json()
        question = body.get("query", "").strip()
        if not question:
            return JSONResponse({"error": "query required"}, status_code=400)
        
        from beigebox.storage.vector_store import VectorStore
        from beigebox.storage.backends import make_backend as _mk
        from beigebox.agents.operator import Operator

        cfg = get_config()
        try:
            _sc = cfg["storage"]
            _ec = cfg["embedding"]
            vs = VectorStore(
                embedding_model=_ec["model"],
                embedding_url=_ec.get("backend_url") or cfg["backend"]["url"],
                backend=_mk(
                    _sc.get("vector_backend", "chromadb"),
                    path=_sc.get("chroma_path") or _sc.get("vector_store_path", "./data/chroma"),
                ),
            )
        except Exception:
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
# Ensemble Voting (v1.0+)
# ---------------------------------------------------------------------------

@app.post("/api/v1/ensemble")
async def api_ensemble(request: Request):
    """
    Vote on responses from multiple models using an LLM judge.

    Request body:
    {
      "prompt": "What is X?",
      "models": ["llama3.2:3b", "mistral:7b"],
      "judge_model": "llama3.2:3b"  (optional; defaults to operator model)
    }

    Returns: SSE stream of JSON events
    {type:"dispatch", model_count:2}
    {type:"result", model:"...", response:"...", latency_ms:123}
    ...
    {type:"evaluate", winner:"...", reasoning:"...", all_responses:[...]}
    {type:"finish", winner:"...", best_response:"...", verdict:"..."}
    """
    try:
        body = await request.json()
    except:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    prompt = body.get("prompt", "").strip()
    models = body.get("models", [])
    judge_model = body.get("judge_model")

    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    if not models:
        return JSONResponse({"error": "models list is required"}, status_code=400)

    from beigebox.agents.ensemble_voter import EnsembleVoter

    voter = EnsembleVoter(models=models, judge_model=judge_model)

    async def event_generator():
        try:
            async for event in voter.vote(prompt):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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

@app.post("/api/v1/web-ui/toggle-vi-mode")


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

def _get_voice_url(kind: str) -> str | None:
    """
    Return the configured STT or TTS base URL from runtime config, falling back
    to config.yaml voice section, then None (which means use backend.url).
    kind: 'stt' or 'tts'
    """
    rt = get_runtime_config()
    cfg = get_config()
    voice_cfg = cfg.get("voice", {})
    key = f"{kind}_url"
    return rt.get(key) or voice_cfg.get(key) or None


async def _wire_and_forward(request: Request, route_label: str, override_base_url: str | None = None) -> StreamingResponse:
    """
    Generic forward: log to wiretap, stream response from backend verbatim.
    Used for all known-but-not-specially-handled OpenAI/Ollama endpoints.
    override_base_url: if provided, forward to this base instead of config backend.url
    """
    cfg = get_config()
    backend_url = (override_base_url or cfg["backend"]["url"]).rstrip("/")
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
    return await _wire_and_forward(request, "audio/transcriptions", _get_voice_url("stt"))

@app.post("/v1/audio/speech")
async def audio_speech(request: Request):
    """TTS — forward to configured TTS service or backend."""
    return await _wire_and_forward(request, "audio/speech", _get_voice_url("tts"))

@app.post("/v1/audio/translations")
async def audio_translations(request: Request):
    """Audio translation — forward to backend."""
    return await _wire_and_forward(request, "audio/translations", _get_voice_url("stt"))

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
