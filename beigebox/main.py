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

from beigebox.config import get_config
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
    ec_status = "ready" if embedding_classifier.ready else "no centroids (run beigebox build-centroids)"

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

    yield

    logger.info("BeigeBox shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="BeigeBox",
    description="Tap the line. Own the conversation.",
    version="0.6.0-dev",
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
    """Semantic search over stored conversations."""
    if not vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = vector_store.search(q, n_results=n, role_filter=role)
    return JSONResponse({"query": q, "results": results})


@app.get("/beigebox/health")
async def health():
    """Health check."""
    return JSONResponse({
        "status": "ok",
        "version": "0.6.0-dev",
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
        "version": "0.6.0-dev",
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
    """Current configuration (safe to expose)."""
    cfg = get_config()
    return JSONResponse({
        "backend": cfg.get("backend", {}),
        "server": cfg.get("server", {}),
        "embedding": {"model": cfg.get("embedding", {}).get("model", "")},
        "storage": {
            "log_conversations": cfg.get("storage", {}).get("log_conversations", True),
        },
        "tools": {
            "enabled": cfg.get("tools", {}).get("enabled", False),
        },
        "decision_llm": {
            "enabled": decision_agent.enabled if decision_agent else False,
            "model": decision_agent.model if decision_agent else "",
        },
        "model_advertising": cfg.get("model_advertising", {}),
    })


@app.get("/api/v1/tools")
async def api_tools():
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
