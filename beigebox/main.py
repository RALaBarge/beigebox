"""
FastAPI application — the BeigeBox entry point.
Implements OpenAI-compatible endpoints that proxy to Ollama.

Now with:
  - Decision LLM initialization and preloading
  - Hook manager setup
  - Embedding model preloading
  - Enhanced stats with token tracking
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from beigebox.config import get_config
from beigebox.proxy import Proxy
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.tools.registry import ToolRegistry
from beigebox.agents.decision import DecisionAgent
from beigebox.agents.embedding_classifier import get_embedding_classifier
from beigebox.hooks import HookManager


# ---------------------------------------------------------------------------
# Globals — initialized at startup
# ---------------------------------------------------------------------------
proxy: Proxy | None = None
tool_registry: ToolRegistry | None = None
sqlite_store: SQLiteStore | None = None
vector_store: VectorStore | None = None
decision_agent: DecisionAgent | None = None
hook_manager: HookManager | None = None


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
    global decision_agent, hook_manager

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

    # Proxy (with decision agent, hooks, embedding classifier, and tools)
    proxy = Proxy(
        sqlite=sqlite_store,
        vector=vector_store,
        decision_agent=decision_agent,
        hook_manager=hook_manager,
        embedding_classifier=embedding_classifier,
        tool_registry=tool_registry,
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
    version="0.2.0",
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
        "version": "0.2.0",
        "decision_llm": decision_agent.enabled if decision_agent else False,
    })


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
