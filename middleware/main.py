"""
FastAPI application — the middleware entry point.
Implements OpenAI-compatible endpoints that proxy to Ollama.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from middleware.config import get_config
from middleware.proxy import Proxy
from middleware.storage.sqlite_store import SQLiteStore
from middleware.storage.vector_store import VectorStore
from middleware.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Globals — initialized at startup
# ---------------------------------------------------------------------------
proxy: Proxy | None = None
tool_registry: ToolRegistry | None = None
sqlite_store: SQLiteStore | None = None
vector_store: VectorStore | None = None


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global proxy, tool_registry, sqlite_store, vector_store

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

    # Proxy
    proxy = Proxy(sqlite=sqlite_store, vector=vector_store)

    # Tools
    tool_registry = ToolRegistry()

    logger.info(
        "AI Middleware started — listening on %s:%s, backend %s",
        cfg["server"]["host"],
        cfg["server"]["port"],
        cfg["backend"]["url"],
    )
    logger.info("Storage: SQLite=%s, Chroma=%s", cfg["storage"]["sqlite_path"], cfg["storage"]["chroma_path"])
    logger.info("Tools enabled: %s", tool_registry.list_tools())

    yield

    logger.info("AI Middleware shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="AI Middleware",
    description="Transparent proxy between LLM frontend and backend",
    version="0.1.0",
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
# Middleware-specific endpoints (not part of OpenAI spec)
# ---------------------------------------------------------------------------

@app.get("/middleware/stats")
async def stats():
    """Return storage statistics."""
    return JSONResponse({
        "sqlite": sqlite_store.get_stats() if sqlite_store else {},
        "vector": vector_store.get_stats() if vector_store else {},
        "tools": tool_registry.list_tools() if tool_registry else [],
    })


@app.get("/middleware/search")
async def search_conversations(q: str, n: int = 5):
    """Semantic search over stored conversations."""
    if not vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = vector_store.search(q, n_results=n)
    return JSONResponse({"query": q, "results": results})


@app.get("/middleware/health")
async def health():
    """Health check."""
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Run with: python -m middleware.main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    cfg = get_config()
    uvicorn.run(
        "middleware.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=False,
    )
