"""
BeigeBox — LLM Middleware Control Plane

LICENSING: Dual-licensed under AGPL-3.0 (free) and Commercial License (proprietary).
See LICENSE.md and COMMERCIAL_LICENSE.md for details.

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

import asyncio
import logging
import os
import time
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from beigebox import __version__ as _BB_VERSION
from beigebox.config import (
    get_config,
    get_runtime_config,
    update_runtime_config,
    get_effective_backends_config,
    get_storage_paths,
)
from beigebox.proxy import Proxy
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.tools.registry import ToolRegistry
from beigebox.agents.decision import DecisionAgent
from beigebox.agents.embedding_classifier import get_embedding_classifier
from beigebox.hooks import HookManager
from beigebox.backends.router import MultiBackendRouter
from beigebox.costs import CostTracker
from beigebox.auth import MultiKeyAuthRegistry
from beigebox.mcp_server import McpServer
from beigebox.amf_mesh import AmfMeshAdvertiser
from beigebox.app_state import AppState
from beigebox.observability.egress import build_egress_hooks, start_egress_hooks, stop_egress_hooks
from beigebox.metrics import collect_system_metrics_async


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state — initialized during lifespan startup
# ---------------------------------------------------------------------------
_app_state: AppState | None = None


def get_state() -> AppState:
    """Return the initialized AppState. Raises if called before startup."""
    if _app_state is None:
        raise RuntimeError("AppState not initialized — server not started yet")
    return _app_state


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


async def _preload_model(url: str, model: str, label: str,
                         retries: int = 5, base_delay: float = 5.0):
    """
    Pin a model in Ollama's memory at startup.
    Retries with exponential backoff — Ollama may still be loading the model
    from disk when beigebox first starts, so one attempt is never enough.
    """
    _log = logging.getLogger(__name__)
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": -1},
                )
                resp.raise_for_status()
            _log.info("%s model '%s' preloaded and pinned", label, model)
            return
        except Exception as e:
            delay = base_delay * (2 ** attempt)
            if attempt < retries - 1:
                _log.warning(
                    "%s preload attempt %d/%d failed (%s) — retrying in %.0fs",
                    label, attempt + 1, retries, e, delay,
                )
                await asyncio.sleep(delay)
            else:
                _log.warning("%s preload failed after %d attempts: %s", label, retries, e)


async def _preload_embedding_model(cfg: dict):
    """Pin the embedding model in Ollama's memory at startup."""
    embed_cfg = cfg.get("embedding", {})
    model = embed_cfg.get("model", "")
    url = embed_cfg.get("backend_url", cfg["backend"]["url"]).rstrip("/")
    if model:
        await _preload_model(url, model, "Embedding")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global _app_state

    cfg = get_config()
    _setup_logging(cfg)
    logger = logging.getLogger(__name__)

    # Storage
    sqlite_path, vector_store_path = get_storage_paths(cfg)
    sqlite_store = SQLiteStore(sqlite_path)
    _storage_cfg  = cfg["storage"]
    _embed_cfg    = cfg["embedding"]
    _backend_type = _storage_cfg.get("vector_backend", "chromadb")
    _backend_path = vector_store_path
    from beigebox.storage.backends import make_backend as _make_backend
    from beigebox.storage.blob_store import BlobStore
    vector_store = VectorStore(
        embedding_model=_embed_cfg["model"],
        embedding_url=_embed_cfg.get("backend_url") or cfg["backend"]["url"],
        backend=_make_backend(_backend_type, path=_backend_path),
    )
    blob_store = BlobStore(Path(vector_store_path) / "blobs")

    # Tools (pass vector_store for the memory tool)
    tool_registry = ToolRegistry(vector_store=vector_store)

    # Decision Agent
    decision_agent = DecisionAgent.from_config(
        available_tools=tool_registry.list_tools()
    )

    # Hooks
    hooks_cfg = cfg.get("hooks", {})
    _hooks_enabled = hooks_cfg.get("enabled", True) if isinstance(hooks_cfg, dict) else True
    _hook_list = hooks_cfg.get("hooks", []) if isinstance(hooks_cfg, dict) else []
    hook_manager = HookManager(
        hooks_dir=hooks_cfg.get("directory", "./hooks") if _hooks_enabled else None,
        hook_configs=_hook_list if isinstance(_hook_list, list) else [],
    )

    # Embedding classifier (fast path for routing)
    embedding_classifier = get_embedding_classifier()
    ec_status = "ready" if embedding_classifier.ready else "no centroids — will auto-build at startup"

    # Multi-backend router — reads effective config (runtime_config.yaml overrides config.yaml)
    backend_router = None
    backends_enabled, backends_cfg = get_effective_backends_config()
    if backends_enabled:
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

    # Auth registry (multi-key, agentauth-backed)
    auth_registry = MultiKeyAuthRegistry(cfg.get("auth", {}))

    # MCP server — expose operator/run if operator is enabled
    _op_enabled_for_mcp = cfg.get("operator", {}).get("enabled", False)
    _op_mcp_factory = None
    if _op_enabled_for_mcp:
        async def _op_mcp_factory(question: str) -> str:
            from beigebox.storage.vector_store import VectorStore as _VS
            from beigebox.storage.backends import make_backend as _mk_b
            from beigebox.agents.operator import Operator as _Op
            _cfg2 = get_config()
            try:
                _sc = _cfg2["storage"]
                _ec = _cfg2["embedding"]
                _vs = _VS(
                    embedding_model=_ec["model"],
                    embedding_url=_ec.get("backend_url") or _cfg2["backend"]["url"],
                    backend=_mk_b(_sc.get("vector_backend", "chromadb"), path=get_storage_paths(_cfg2)[1]),
                )
            except Exception:
                _vs = None
            _op = _Op(vector_store=_vs, blob_store=blob_store)
            _loop = asyncio.get_event_loop()
            return await _loop.run_in_executor(None, _op.run, question, None)

    # Load skills for MCP resources/list + resources/read
    from beigebox.agents.skill_loader import load_skills as _load_skills
    _skills_path = cfg.get("skills", {}).get("path") or str(
        Path(__file__).parent.parent / "2600" / "skills"
    )
    _mcp_skills = _load_skills(_skills_path)

    mcp_server = McpServer(tool_registry, operator_factory=_op_mcp_factory, skills=_mcp_skills)
    if _op_enabled_for_mcp:
        logger.info("MCP server: enabled (POST /mcp) — operator/run tool exposed")
    else:
        logger.info("MCP server: enabled (POST /mcp)")

    # AMF mesh advertisement (mDNS + NATS heartbeat)
    amf_advertiser = AmfMeshAdvertiser(cfg, tool_names=tool_registry.list_tools())
    await amf_advertiser.start()

    # Observability egress hooks (webhook batching, fire-and-forget)
    egress_hooks = build_egress_hooks(cfg)
    await start_egress_hooks(egress_hooks)
    if egress_hooks:
        logger.info("Observability egress: %d hook(s) active", len(egress_hooks))
    else:
        logger.debug("Observability egress: no webhooks configured")

    # Proxy (with decision agent, hooks, embedding classifier, tools, and router)
    proxy = Proxy(
        sqlite=sqlite_store,
        vector=vector_store,
        decision_agent=decision_agent,
        hook_manager=hook_manager,
        embedding_classifier=embedding_classifier,
        tool_registry=tool_registry,
        backend_router=backend_router,
        blob_store=blob_store,
        egress_hooks=egress_hooks,
    )

    _app_state = AppState(
        proxy=proxy,
        tool_registry=tool_registry,
        sqlite_store=sqlite_store,
        vector_store=vector_store,
        blob_store=blob_store,
        decision_agent=decision_agent,
        hook_manager=hook_manager,
        backend_router=backend_router,
        cost_tracker=cost_tracker,
        embedding_classifier=embedding_classifier,
        auth_registry=auth_registry,
        mcp_server=mcp_server,
        amf_advertiser=amf_advertiser,
        egress_hooks=egress_hooks,
    )

    logger.info(
        "BeigeBox started — listening on %s:%s, backend %s",
        cfg["server"]["host"],
        cfg["server"]["port"],
        cfg["backend"]["url"],
    )
    logger.info("Storage: SQLite=%s, Vector=%s", sqlite_path, vector_store_path)
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

    # Preload models — run concurrently in the background so startup is not blocked.
    # Both use retry-with-backoff; Ollama may still be loading models from disk.
    _preload_tasks = [asyncio.create_task(_preload_embedding_model(cfg))]
    if decision_agent:
        # Delay decision LLM preload by 30s — gives Ollama time to finish loading
        # the primary chat model first so the two don't race for VRAM bandwidth.
        async def _delayed_decision_preload():
            await asyncio.sleep(30)
            await decision_agent.preload()
        _preload_tasks.append(asyncio.create_task(_delayed_decision_preload()))
    # Fire-and-forget: server starts accepting requests immediately while
    # models warm up. Tasks are not awaited here.

    # Auto-build centroids if they don't exist yet. Uses create_task (not
    # await) so the startup finishes immediately and the server begins
    # accepting requests while the embedding model warms up in the background.
    if not embedding_classifier.ready:
        import asyncio as _asyncio

        async def _auto_build_centroids():
            logger.info("Embedding centroids not found — auto-building in background…")
            try:
                import asyncio as _asyncio2
                loop = _asyncio2.get_running_loop()
                success = await loop.run_in_executor(None, embedding_classifier.build_centroids)
                if success:
                    logger.info("Embedding centroids auto-built successfully")
                else:
                    logger.warning("Auto-build centroids returned False — check Ollama is running with nomic-embed-text")
            except Exception as _e:
                logger.warning("Auto-build centroids failed: %s", _e)

        _asyncio.create_task(_auto_build_centroids())

    # Auto-ingest staged documents on startup
    def _sync_ingest_staging():
        """Synchronous worker: index staged docs. Runs in a thread via run_in_executor."""
        import hashlib
        import shutil
        from beigebox.storage.chunker import chunk_text

        staging_path = Path(__file__).parent.parent / "2600" / "2600-staging"
        archive_path = Path(__file__).parent.parent / "2600" / "2599"
        manifest_path = Path(__file__).parent.parent / "2600" / ".upload-manifest.json"

        if not staging_path.exists():
            return

        staged_files = list(staging_path.glob("*.md"))
        if not staged_files:
            return

        logger.info("Found %d staged document(s) in 2600-staging — indexing…", len(staged_files))

        manifest = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())

        if "files" not in manifest:
            manifest["files"] = {}

        for file_path in staged_files:
            try:
                content = file_path.read_text(encoding="utf-8")
                md5 = hashlib.md5(content.encode()).hexdigest()

                chunks = chunk_text(content, source_file=file_path.name)

                for chunk in chunks:
                    vector_store.store_document_chunk(
                        source_file=file_path.name,
                        chunk_index=chunk["chunk_index"],
                        char_offset=chunk["char_offset"],
                        blob_hash=md5,
                        text=chunk["text"],
                    )

                manifest["files"][file_path.name] = {
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "md5": md5,
                    "status": "uploaded"
                }

                archive_path.mkdir(parents=True, exist_ok=True)
                dest = archive_path / file_path.name
                shutil.move(str(file_path), str(dest))
                logger.info("Indexed and archived: %s", file_path.name)

            except Exception as e:
                logger.error("Failed to ingest %s: %s", file_path.name, e)
                manifest["files"][file_path.name] = {
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "md5": "",
                    "status": "failed",
                    "error": str(e)
                }

        manifest["last_sync"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2))
        logger.info("Staging ingest complete — manifest updated")

    async def _auto_ingest_staging():
        """Fire-and-forget wrapper: runs sync ingest in a thread executor."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _sync_ingest_staging)
        except Exception as e:
            logger.warning("Staging ingest failed: %s", e)

    asyncio.create_task(_auto_ingest_staging())

    yield

    logger.info("BeigeBox shutting down")
    if _app_state and _app_state.amf_advertiser:
        await _app_state.amf_advertiser.stop()
    if _app_state and _app_state.egress_hooks:
        await stop_egress_hooks(_app_state.egress_hooks)
    if _app_state and _app_state.proxy and _app_state.proxy.wire:
        _app_state.proxy.wire.close()
    from beigebox.payload_log import get_payload_log as _get_pl
    _get_pl().close()
    logger.info("Wiretap and payload log flushed and closed")


# ---------------------------------------------------------------------------
# Auth middleware — single API key, disabled when key is empty
# ---------------------------------------------------------------------------

# Paths that never require auth (web UI + basic health checks)
_AUTH_EXEMPT = frozenset(["/", "/ui", "/beigebox/health", "/api/v1/status"])


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Multi-key API guard backed by agentauth keychain storage.

    Reads from the global auth_registry (built at startup from config auth.keys).
    Falls back to the legacy single auth.api_key for backwards compatibility.
    Auth disabled when no keys are configured.

    Per-key enforcement:
      - Endpoint ACL (allowed_endpoints glob patterns)
      - Model ACL  (allowed_models glob patterns — checked in chat endpoint)
      - Rate limit (allowed_models rate_limit_rpm rolling 60-second window)

    Accepts the token via:
      Authorization: Bearer <token>
      api-key: <token>          (OpenAI-style header)
      ?api_key=<token>          (query param fallback)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if _app_state is None or _app_state.auth_registry is None or not _app_state.auth_registry.is_enabled():
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT or path.startswith("/web/"):
            return await call_next(request)

        # Extract token
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = (
                request.headers.get("api-key", "")
                or request.query_params.get("api_key", "")
            )

        meta = _app_state.auth_registry.validate(token)
        if meta is None:
            return JSONResponse(
                {
                    "error": {
                        "message": (
                            "Invalid API key. Provide it via "
                            "Authorization: Bearer <key>, api-key header, or ?api_key= query param."
                        ),
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
                status_code=401,
            )

        # Rate limit
        if not _app_state.auth_registry.check_rate_limit(meta):
            return JSONResponse(
                {
                    "error": {
                        "message": f"Rate limit exceeded for key '{meta.name}' ({meta.rate_limit_rpm} rpm).",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                },
                status_code=429,
            )

        # Endpoint ACL
        if not _app_state.auth_registry.check_endpoint(meta, path):
            return JSONResponse(
                {
                    "error": {
                        "message": f"Endpoint '{path}' not permitted for key '{meta.name}'.",
                        "type": "invalid_request_error",
                        "code": "endpoint_not_allowed",
                    }
                },
                status_code=403,
            )

        # Store key metadata in request state so downstream endpoints can check model ACL
        request.state.auth_key = meta
        return await call_next(request)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="BeigeBox",
    description="Tap the line. Control the carrier.",
    version=_BB_VERSION,
    lifespan=lifespan,
)

app.add_middleware(ApiKeyMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # CSP: self + blob: (audio/image preview) + no inline scripts except index.html
        # eval is blocked; data: URIs restricted to images only.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)


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

    # Model ACL — check here where the body is already parsed
    model = body.get("model", "")
    _auth_key = getattr(request.state, "auth_key", None)
    _st = get_state()
    if _auth_key is not None and model and _st.auth_registry and not _st.auth_registry.check_model(_auth_key, model):
        return JSONResponse(
            {
                "error": {
                    "message": f"Model '{model}' not permitted for key '{_auth_key.name}'.",
                    "type": "invalid_request_error",
                    "code": "model_not_allowed",
                }
            },
            status_code=403,
        )

    # Inject the key name as a special body field so the routing rules engine
    # can match on BB_AUTH_KEY conditions. The proxy strips it before forwarding.
    if _auth_key:
        body["_bb_auth_key"] = _auth_key.name

    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            _st.proxy.forward_chat_completion_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        data = await _st.proxy.forward_chat_completion(body)
        from beigebox.proxy import _request_route as _rr
        _route_val = _rr.get("")
        extra = {"X-BeigeBox-Route": _route_val} if _route_val else {}
        return JSONResponse(data, headers=extra)


@app.get("/v1/models")
async def list_models():
    """Forward model listing to backend."""
    data = await get_state().proxy.list_models()
    return JSONResponse(data)


@app.post("/api/v1/route-check")
async def api_route_check(request: Request):
    """
    Return the routing decision for a prompt without running inference.
    Used by eval suites to verify classifier accuracy without token cost.

    Body: {"input": "plain text"} or {"messages": [...]}
    Returns: {"route": "simple|complex|code|creative|...", "model": "...", "confidence": 0.0}
    """
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Accept "input" shorthand used by eval suites
    if "input" in body and "messages" not in body:
        body["messages"] = [{"role": "user", "content": body["input"]}]

    _st = get_state()
    if not _st.proxy:
        return JSONResponse({"error": "proxy not available"}, status_code=503)

    from beigebox.agents.zcommand import parse_z_command
    from beigebox.proxy import _request_route as _rr

    user_msg = body.get("messages", [{}])[-1].get("content", "") if body.get("messages") else ""
    zcmd = parse_z_command(user_msg)

    # Run only the routing stage — no backend call
    body_copy = dict(body)
    body_copy, decision = await _st.proxy._hybrid_route(body_copy, zcmd, "route-check")

    route = _rr.get("default")
    model = body_copy.get("model", "")
    result = {"route": route, "model": model}
    if decision:
        result["decision_llm"] = True
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# BeigeBox-specific endpoints
# ---------------------------------------------------------------------------

@app.get("/beigebox/stats")
async def stats():
    """Return storage and usage statistics."""
    _st = get_state()
    sqlite_stats = _st.sqlite_store.get_stats() if _st.sqlite_store else {}
    vector_stats = _st.vector_store.get_stats() if _st.vector_store else {}
    tools = _st.tool_registry.list_tools() if _st.tool_registry else []
    hooks = _st.hook_manager.list_hooks() if _st.hook_manager else []

    return JSONResponse({
        "sqlite": sqlite_stats,
        "vector": vector_stats,
        "tools": tools,
        "hooks": hooks,
        "decision_llm": {
            "enabled": _st.decision_agent.enabled if _st.decision_agent else False,
            "model": _st.decision_agent.model if _st.decision_agent else "",
            **(_st.decision_agent.fallback_stats() if _st.decision_agent else {}),
        },
    })


@app.get("/beigebox/search")
async def search_conversations(q: str, n: int = 5, role: str | None = None):
    """Semantic search over stored conversations (raw message hits)."""
    _st = get_state()
    if not _st.vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = _st.vector_store.search(q, n_results=n, role_filter=role)
    return JSONResponse({"query": q, "results": results})


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """
    MCP (Model Context Protocol) server — Streamable HTTP transport.
    Accepts JSON-RPC 2.0 requests and dispatches to BeigeBox's tool registry.

    Supported methods: initialize, tools/list, tools/call
    Auth: governed by the same ApiKeyMiddleware as all other endpoints.
    """
    _st = get_state()
    if _st.mcp_server is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "MCP server not initialised"}},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    result = await _st.mcp_server.handle(body)
    if result is None:
        # Notification — no response body
        from starlette.responses import Response as _Response
        return _Response(status_code=202)
    return JSONResponse(result)


@app.get("/api/v1/zcommands")
async def api_zcommands():
    """Return all available z-commands — hardcoded + any custom ones from config."""
    from beigebox.agents.zcommand import ROUTE_ALIASES, TOOL_DIRECTIVES
    cfg = get_config()
    zcfg = cfg.get("zcommands", {})

    # Collapse aliases into display groups: target → [aliases]
    route_groups: dict[str, list[str]] = {}
    for alias, target in ROUTE_ALIASES.items():
        route_groups.setdefault(target, []).append(alias)

    tool_groups: dict[str, list[str]] = {}
    for alias, target in TOOL_DIRECTIVES.items():
        tool_groups.setdefault(target, []).append(alias)

    # Custom commands from config zcommands.commands (if any)
    custom = [
        {"name": c["name"], "description": c.get("description", ""), "route_to": c.get("route_to", "")}
        for c in zcfg.get("commands", [])
        if c.get("name") not in {**ROUTE_ALIASES, **TOOL_DIRECTIVES, "help": 1, "fork": 1}
    ]

    return JSONResponse({
        "prefix": zcfg.get("prefix", "z:"),
        "enabled": zcfg.get("enabled", True),
        "routing": [{"aliases": aliases, "target": target} for target, aliases in route_groups.items()],
        "tools":   [{"aliases": aliases, "target": target} for target, aliases in tool_groups.items()],
        "special": [
            {"name": "help",  "description": "list available z-commands"},
            {"name": "fork",  "description": "fork conversation into a new branch"},
        ],
        "custom": custom,
    })


@app.get("/api/v1/search")
async def api_search_conversations(q: str, n: int = 5, role: str | None = None):
    """
    Semantic search grouped by conversation.
    Returns conversations ranked by best message match, with excerpt.
    """
    _st = get_state()
    if not _st.vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = _st.vector_store.search_grouped(q, n_conversations=n, role_filter=role)
    return JSONResponse({"query": q, "results": results, "count": len(results)})


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
        "x-amf": {
            "agent_id": _st.amf_advertiser._agent_id if _st.amf_advertiser else "spiffe://local/beigebox/unknown",
            "trust_domain": cfg.get("amf_mesh", {}).get("trust_domain", "local"),
            "protocols": ["MCP/2024-11-05"],
            "mcp_endpoint": f"{endpoint}/mcp",
        },
    })


@app.get("/beigebox/health")
async def health():
    """Health check."""
    _st = get_state()
    cfg = get_config()
    return JSONResponse({
        "status": "ok",
        "version": _BB_VERSION,
        "decision_llm": _st.decision_agent.enabled if _st.decision_agent else False,
        "backend_url": cfg.get("backend", {}).get("url", "http://localhost:11434").rstrip("/"),
    })


# ---------------------------------------------------------------------------
# API v1 endpoints (for web UI and other clients)
# ---------------------------------------------------------------------------

@app.get("/api/v1/info")
async def api_info():
    """System info — what features are available."""
    cfg = get_config()
    _st = get_state()
    return JSONResponse({
        "version": _BB_VERSION,
        "name": "BeigeBox",
        "description": "Transparent Pythonic LLM Proxy",
        "server": {
            "host": cfg["server"].get("host", "0.0.0.0"),
            "port": cfg["server"].get("port", 8000),
        },
        "backend": {
            "url": cfg["backend"].get("url", ""),
            "default_model": get_runtime_config().get("default_model") or cfg["backend"].get("default_model", ""),
        },
        "features": {
            "routing": True,
            "decision_llm": _st.decision_agent.enabled if _st.decision_agent else False,
            "embedding_classifier": _st.embedding_classifier.ready if _st.embedding_classifier else False,
            "storage": _st.sqlite_store is not None and _st.vector_store is not None,
            "tools": _st.tool_registry is not None and cfg.get("tools", {}).get("enabled", False),
            "hooks": _st.hook_manager is not None,
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

    # Merge runtime overrides onto config values
    return JSONResponse({
        # ── Features (Phase 1 refactoring) ────────────────────────────
        "features": {
            "backends":              rt.get("features_backends", cfg.get("features", {}).get("backends", cfg.get("backends_enabled", False))),
            "decision_llm":          rt.get("features_decision_llm", cfg.get("features", {}).get("decision_llm", cfg.get("decision_llm", {}).get("enabled", True))),
            "classifier":            rt.get("features_classifier", cfg.get("features", {}).get("classifier", cfg.get("classifier", {}).get("enabled", True))),
            "semantic_cache":        rt.get("features_semantic_cache", cfg.get("features", {}).get("semantic_cache", cfg.get("semantic_cache", {}).get("enabled", False))),
            "operator":              rt.get("features_operator", cfg.get("features", {}).get("operator", cfg.get("operator", {}).get("enabled", True))),
            "harness":               rt.get("features_harness", cfg.get("features", {}).get("harness", cfg.get("harness", {}).get("enabled", True))),
            "tools":                 rt.get("features_tools", cfg.get("features", {}).get("tools", cfg.get("tools", {}).get("enabled", False))),
            "cost_tracking":         rt.get("features_cost_tracking", cfg.get("features", {}).get("cost_tracking", cfg.get("cost_tracking", {}).get("enabled", False))),
            "conversation_replay":   rt.get("features_conversation_replay", cfg.get("features", {}).get("conversation_replay", cfg.get("conversation_replay", {}).get("enabled", False))),
            "auto_summarization":    rt.get("features_auto_summarization", cfg.get("features", {}).get("auto_summarization", cfg.get("auto_summarization", {}).get("enabled", False))),
            "system_context":        rt.get("features_system_context", cfg.get("features", {}).get("system_context", cfg.get("system_context", {}).get("enabled", False))),
            "wiretap":               rt.get("features_wiretap", cfg.get("features", {}).get("wiretap", cfg.get("wiretap", {}).get("enabled", True))),
            "payload_log":           rt.get("features_payload_log", cfg.get("features", {}).get("payload_log", False)),
            "wasm":                  rt.get("features_wasm", cfg.get("features", {}).get("wasm", cfg.get("wasm", {}).get("enabled", False))),
            "guardrails":            rt.get("features_guardrails", cfg.get("features", {}).get("guardrails", cfg.get("guardrails", {}).get("enabled", False))),
            "amf_mesh":              rt.get("features_amf_mesh", cfg.get("features", {}).get("amf_mesh", cfg.get("amf_mesh", {}).get("enabled", False))),
            "voice":                 rt.get("features_voice", cfg.get("features", {}).get("voice", cfg.get("voice", {}).get("enabled", False))),
            "hooks":                 rt.get("features_hooks", cfg.get("features", {}).get("hooks", cfg.get("hooks", {}).get("enabled", False))),
        },
        # ── Backend ──────────────────────────────────────────────────
        "backend": {
            "url":           cfg.get("backend", {}).get("url", ""),
            "default_model": rt.get("default_model") or cfg.get("backend", {}).get("default_model", ""),
            "timeout":       cfg.get("backend", {}).get("timeout", 120),
        },
        # ── Models Registry (Phase 2 refactoring) ────────────────────
        "models": {
            "default":       rt.get("models_default") or cfg.get("models", {}).get("default", "qwen3:4b"),
            "routing":       rt.get("models_routing") or cfg.get("models", {}).get("profiles", {}).get("routing", "llama3.2:3b"),
            "agentic":       rt.get("models_agentic") or cfg.get("models", {}).get("profiles", {}).get("agentic", "qwen3:4b"),
            "summary":       rt.get("models_summary") or cfg.get("models", {}).get("profiles", {}).get("summary", "llama3.2:3b"),
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
            "path":               get_storage_paths(cfg)[0],
            "vector_store_path":  get_storage_paths(cfg)[1],
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
            "browserbox":   cfg.get("tools", {}).get("browserbox", {}),
        },
        # ── Decision LLM ─────────────────────────────────────────────
        "decision_llm": {
            "enabled":     rt.get("decision_llm_enabled", get_state().decision_agent.enabled if get_state().decision_agent else False),
            "model":       get_state().decision_agent.model if get_state().decision_agent else cfg.get("decision_llm", {}).get("model", ""),
            "timeout":     rt.get("decision_llm_timeout") or cfg.get("decision_llm", {}).get("timeout", 5),
            "max_tokens":  cfg.get("decision_llm", {}).get("max_tokens", 256),
        },
        # ── Operator ─────────────────────────────────────────────────
        "operator": {
            "enabled":        rt.get("operator_enabled", cfg.get("operator", {}).get("enabled", False)),
            "model":          rt.get("operator_model") or cfg.get("operator", {}).get("model", ""),
            "max_iterations": cfg.get("operator", {}).get("max_iterations", 10),
            "run_timeout":    rt.get("operator_run_timeout") or cfg.get("operator", {}).get("run_timeout", 600),
            "shell": {
                "enabled":            cfg.get("operator", {}).get("shell", {}).get("enabled", False),
                "allowed_commands":   cfg.get("operator", {}).get("shell", {}).get("allowed_commands", []),
                "blocked_patterns":   cfg.get("operator", {}).get("shell", {}).get("blocked_patterns", []),
            },
        },
        # ── Local Models Filter ────────────────────────────────────────
        "local_models": {
            "filter_enabled": cfg.get("local_models", {}).get("filter_enabled", False),
            "allowed_models": cfg.get("local_models", {}).get("allowed_models", []),
        },
        # ── Model advertising ─────────────────────────────────────────
        "model_advertising": cfg.get("model_advertising", {}),
        # ── Multi-backend ─────────────────────────────────────────────
        # Effective config: runtime_config.yaml overrides config.yaml
        "backends_enabled": get_effective_backends_config()[0],
        "backends": [
            {k: ("***" if "key" in k.lower() and v else v)
             for k, v in b.items()}
            for b in get_effective_backends_config()[1]
        ],
        # ── Feature flags ─────────────────────────────────────────────
        "cost_tracking": {
            **cfg.get("cost_tracking", {}),
            "enabled": rt.get("cost_tracking_enabled", cfg.get("cost_tracking", {}).get("enabled", False)),
            "track_openrouter": rt.get("cost_track_openrouter", cfg.get("cost_tracking", {}).get("track_openrouter", True)),
            "track_local": rt.get("cost_track_local", cfg.get("cost_tracking", {}).get("track_local", False)),
        },
        "harness": {
            "enabled":      rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)),
            "ralph_enabled": rt.get("ralph_enabled", cfg.get("harness", {}).get("ralph_enabled", False)),
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
        # ── Routing — Tier Pipeline (Phase 3 refactoring) ────────────────
        "routing": {
            # Tier 1: Session cache
            "session_cache": {
                "ttl_seconds": cfg.get("routing", {}).get("session_cache", {}).get("ttl_seconds", 3600),
            },
            # Tier 2: Embedding classifier
            "classifier": {
                "enabled": rt.get("features_classifier", cfg.get("features", {}).get("classifier", cfg.get("classifier", {}).get("enabled", True))),
                "centroid_rebuild_interval": cfg.get("routing", {}).get("classifier", {}).get("centroid_rebuild_interval", cfg.get("classifier", {}).get("centroid_rebuild_interval", 3600)),
            },
            # Tier 3: Semantic cache
            "semantic_cache": {
                "enabled": rt.get("features_semantic_cache", cfg.get("features", {}).get("semantic_cache", cfg.get("semantic_cache", {}).get("enabled", False))),
                "similarity_threshold": cfg.get("routing", {}).get("semantic_cache", {}).get("similarity_threshold", cfg.get("semantic_cache", {}).get("similarity_threshold", 0.92)),
                "max_entries": cfg.get("routing", {}).get("semantic_cache", {}).get("max_entries", cfg.get("semantic_cache", {}).get("max_entries", 500)),
                "ttl_seconds": cfg.get("routing", {}).get("semantic_cache", {}).get("ttl_seconds", cfg.get("semantic_cache", {}).get("ttl_seconds", 3600)),
            },
            # Tier 4: Decision LLM (judge)
            "decision_llm": {
                "enabled": rt.get("features_decision_llm", cfg.get("features", {}).get("decision_llm", cfg.get("decision_llm", {}).get("enabled", True))),
                "temperature": cfg.get("routing", {}).get("decision_llm", {}).get("temperature", cfg.get("decision_llm", {}).get("temperature", 0.2)),
            },
            # Routing control
            "force_route":         rt.get("force_route", ""),
            "force_decision":      rt.get("force_decision", False),
            "border_threshold":    rt.get("border_threshold"),
            "agentic_threshold":   rt.get("agentic_threshold"),
            "allow_openrouter_for_plain_models": rt.get("allow_openrouter_for_plain_models", cfg.get("routing", {}).get("allow_openrouter_for_plain_models", False)),
        },
        # ── Logging ───────────────────────────────────────────────────
        "logging": {
            "level": rt.get("log_level") or cfg.get("logging", {}).get("level", "INFO"),
            "file":  cfg.get("logging", {}).get("file", ""),
        },
        # ── Wiretap ───────────────────────────────────────────────────
        "wiretap": cfg.get("wiretap", {}),
        # ── Payload log ───────────────────────────────────────────────
        "payload_log": {
            "enabled": rt.get("payload_log_enabled", False),
            "path":    cfg.get("payload_log", {}).get("path", "./data/payload.jsonl"),
        },
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
        # ── WASM ──────────────────────────────────────────────────────────────
        "wasm": {
            "enabled":        rt.get("wasm_enabled", get_state().proxy.wasm_runtime.enabled if get_state().proxy else cfg.get("wasm", {}).get("enabled", False)),
            "timeout_ms":     rt.get("wasm_timeout_ms", cfg.get("wasm", {}).get("timeout_ms", 500)),
            "modules":        get_state().proxy.wasm_runtime.list_modules() if get_state().proxy else [],
            "default_module": rt.get("wasm_default_module") or (get_state().proxy.wasm_runtime.default_module if get_state().proxy else cfg.get("wasm", {}).get("default_module", "")),
            "modules_cfg": {
                name: {
                    "description": mcfg.get("description", ""),
                    "enabled":     mcfg.get("enabled", True),
                    "path":        mcfg.get("path", ""),
                }
                for name, mcfg in cfg.get("wasm", {}).get("modules", {}).items()
            },
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
        cost_tracking_enabled, harness_enabled,
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
        # Features (Phase 1 refactoring)
        "features_backends":            "features_backends",
        "features_decision_llm":        "features_decision_llm",
        "features_classifier":          "features_classifier",
        "features_semantic_cache":      "features_semantic_cache",
        "features_operator":            "features_operator",
        "features_harness":             "features_harness",
        "features_tools":               "features_tools",
        "features_cost_tracking":       "features_cost_tracking",
        "features_conversation_replay": "features_conversation_replay",
        "features_auto_summarization":  "features_auto_summarization",
        "features_system_context":      "features_system_context",
        "features_wiretap":             "features_wiretap",
        "features_payload_log":         "features_payload_log",
        "features_wasm":                "features_wasm",
        "features_guardrails":          "features_guardrails",
        "features_amf_mesh":            "features_amf_mesh",
        "features_voice":               "features_voice",
        "features_hooks":               "features_hooks",
        # Models Registry (Phase 2 refactoring)
        "models_default":               "models_default",
        "models_routing":               "models_routing",
        "models_agentic":               "models_agentic",
        "models_summary":               "models_summary",
        # Routing
        "default_model":                "default_model",
        "force_route":                  "force_route",
        "border_threshold":             "border_threshold",
        "agentic_threshold":            "agentic_threshold",
        # Features (keep old keys for backwards compat)
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
        "harness_enabled":              "harness_enabled",
        "conversation_replay_enabled":  "conversation_replay_enabled",
        "auto_summarization_enabled":   "auto_summarization_enabled",
        "auto_token_budget":            "auto_token_budget",
        "auto_summary_model":           "auto_summary_model",
        "auto_keep_last":               "auto_keep_last",
        # System context
        "system_context_enabled":       "system_context_enabled",
        # Operator
        "operator_enabled":             "operator_enabled",
        "operator_model":               "operator_model",
        # Operator shell security
        "shell_allowed_commands":       "shell_allowed_commands",
        "shell_blocked_patterns":       "shell_blocked_patterns",
        # Local models filter
        "local_models_filter_enabled":  "local_models_filter_enabled",
        "local_models_allowed_models":  "local_models_allowed_models",
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
        # WASM
        "wasm_default_module":          "wasm_default_module",
        "wasm_enabled":                 "wasm_enabled",
        "wasm_timeout_ms":              "wasm_timeout_ms",
        # Decision LLM tuning
        "decision_llm_timeout":         "decision_llm_timeout",
        # Routing
        "force_decision":               "force_decision",
        "allow_openrouter_for_plain_models": "allow_openrouter_for_plain_models",
        # Multi-backend
        "backends_enabled":             "backends_enabled",
        # BrowserBox
        "browserbox_enabled":           "browserbox_enabled",
        "browserbox_ws_url":            "browserbox_ws_url",
        "browserbox_timeout":           "browserbox_timeout",
        # Payload logger
        "payload_log_enabled":          "payload_log_enabled",
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
    _st = get_state()

    if "decision_llm_enabled" in updated and _st.decision_agent:
        _st.decision_agent.enabled = rt.get("decision_llm_enabled", _st.decision_agent.enabled)

    if "default_model" in updated and _st.proxy:
        _st.proxy.default_model = rt.get("default_model", _st.proxy.default_model)

    if "log_conversations" in updated and _st.proxy:
        _st.proxy.log_enabled = rt.get("log_conversations", _st.proxy.log_enabled)

    if "wasm_default_module" in updated and _st.proxy:
        _st.proxy.wasm_runtime.default_module = rt.get("wasm_default_module", "")

    if "wasm_enabled" in updated and _st.proxy:
        new_wasm_enabled = rt.get("wasm_enabled")
        if new_wasm_enabled is True:
            _st.proxy.wasm_runtime.enable(get_config())
        elif new_wasm_enabled is False:
            _st.proxy.wasm_runtime.disable()

    if errors:
        return JSONResponse({"saved": updated, "errors": errors}, status_code=207)
    return JSONResponse({"saved": updated, "ok": True})


@app.post("/api/v1/wasm/reload")
async def api_wasm_reload():
    """
    Reload WASM modules from disk without restarting BeigeBox.
    Re-reads config.yaml for updated paths and enabled flags.
    Returns the list of successfully loaded module names.
    """
    _st = get_state()
    if not _st.proxy:
        return JSONResponse({"error": "proxy not initialized"}, status_code=503)
    loaded = _st.proxy.wasm_runtime.reload()
    return JSONResponse({"ok": True, "modules": loaded})


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



@app.get("/api/v1/tools")
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


@app.get("/api/v1/status")
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
            "sqlite": _st.sqlite_store is not None,
            "vector": _st.vector_store is not None,
            "stats": _st.sqlite_store.get_stats() if _st.sqlite_store else {},
        },
        "routing": {
            "decision_llm": {
                "enabled": _st.decision_agent.enabled if _st.decision_agent else False,
                "model": _st.decision_agent.model if _st.decision_agent else "",
            },
            "embedding_classifier": {
                "ready": _st.embedding_classifier.ready if _st.embedding_classifier else False,
            },
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


@app.get("/api/v1/stats")
async def api_stats():
    """Statistics about conversations and usage."""
    _st = get_state()
    sqlite_stats = _st.sqlite_store.get_stats() if _st.sqlite_store else {}
    vector_stats = _st.vector_store.get_stats() if _st.vector_store else {}
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
    _st = get_state()
    if not _st.cost_tracker:
        return JSONResponse({
            "enabled": False,
            "message": "Cost tracking is disabled. Set cost_tracking.enabled: true in config.",
        })
    stats = _st.cost_tracker.get_stats(days=days)
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
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    fmt = format.lower().strip()
    if fmt not in ("jsonl", "alpaca", "sharegpt"):
        return JSONResponse(
            {"error": f"Unknown format '{fmt}'. Use: jsonl, alpaca, sharegpt"},
            status_code=400,
        )

    model_filter = model or None

    if fmt == "jsonl":
        data = _st.sqlite_store.export_jsonl(model_filter)
        filename = "conversations.jsonl"
        # True JSONL: one JSON object per line
        content = "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n"
        media_type = "application/x-ndjson"
    elif fmt == "alpaca":
        data = _st.sqlite_store.export_alpaca(model_filter)
        filename = "conversations_alpaca.json"
        content = json.dumps(data, ensure_ascii=False, indent=2)
        media_type = "application/json"
    else:  # sharegpt
        data = _st.sqlite_store.export_sharegpt(model_filter)
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
    """Per-model latency and throughput stats."""
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)
    rt = get_runtime_config()
    since = rt.get("perf_stats_since") or None
    data = _st.sqlite_store.get_model_performance(days=days, since=since)
    data["enabled"] = True
    data["since"] = since
    return JSONResponse(data)


@app.post("/api/v1/model-performance/reset")
async def api_model_performance_reset():
    """Set perf_stats_since to now, effectively zeroing the visible stats window."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    update_runtime_config("perf_stats_since", now)
    return JSONResponse({"ok": True, "since": now})


@app.get("/api/v1/model-options")
async def api_get_model_options():
    """Return current runtime model_options (num_gpu per model)."""
    rt = get_runtime_config()
    return JSONResponse({"model_options": rt.get("model_options", {})})


@app.post("/api/v1/model-options")
async def api_set_model_option(request: Request):
    """
    Set or clear num_gpu for a specific model.
    Body: {"model": "llama3.2:3b", "num_gpu": 0}  — 0=CPU, 99=GPU, null=inherit
    """
    body = await request.json()
    model_name = body.get("model", "").strip()
    if not model_name:
        return JSONResponse({"error": "model required"}, status_code=400)
    num_gpu = body.get("num_gpu")  # int or null

    rt = get_runtime_config()
    model_opts = dict(rt.get("model_options") or {})

    if num_gpu is None:
        model_opts.pop(model_name, None)
    else:
        model_opts[model_name] = int(num_gpu)

    update_runtime_config("model_options", model_opts)
    return JSONResponse({"ok": True, "model": model_name, "num_gpu": num_gpu})


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
            # Tail-seek: seek to end (SEEK_END), measure file size, then seek back
            # by `lines * ~220 bytes` to read just the relevant tail without
            # loading the entire wire.jsonl into memory.
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
    """Health and status of all configured backends, with rolling latency stats."""
    _st = get_state()
    if not _st.backend_router:
        cfg = get_config()
        return JSONResponse({
            "enabled": False,
            "message": "Multi-backend routing is disabled. Set backends_enabled: true in config.",
            "primary_backend": cfg.get("backend", {}).get("url", ""),
        })
    backends_list = await _st.backend_router.health()

    # Passive model spec discovery: upsert any loaded models observed in hw_stats
    if _st.sqlite_store:
        for b in backends_list:
            for hw in (b.get("hw_stats") or []):
                model_name = hw.get("model", "")
                vram_mb = hw.get("vram_used_mb") or None  # None, not 0
                if model_name:
                    try:
                        _st.sqlite_store.store_model_spec(
                            model_name=model_name,
                            backend=b.get("name", "unknown"),
                            vram_mb=vram_mb,
                            discovery_method="ollama_ps",
                        )
                    except Exception as _e:
                        logger.debug("model_spec upsert failed for %s: %s", model_name, _e)

    return JSONResponse({
        "enabled": True,
        "backends": backends_list,  # list, not dict
    })


@app.post("/api/v1/backends/apply")
async def api_backends_apply(request: Request):
    """
    Save backends config to runtime_config.yaml and rebuild the router without restart.

    Accepts: {"enabled": bool, "backends": [{name, provider, url, api_key, priority, ...}]}

    API keys set to "***" (the UI masked placeholder) are preserved from the existing config.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    try:
        enabled = bool(body.get("enabled", False))
        new_backends = body.get("backends", [])
        if not isinstance(new_backends, list):
            return JSONResponse({"error": "backends must be a list"}, status_code=400)

        # The web UI displays API keys as "***" (never sends the real value).
        # On save, re-inject the real key from the existing config so a
        # round-trip through the UI never silently clears a configured key.
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

        # Rebuild the router in-place so existing requests using the old router
        # finish cleanly while new requests immediately see the updated config.
        # Use get_effective_backends_config() so the ollama fallback injection
        # runs — same logic as startup, prevents "No backends available" when
        # the UI saves a list that contains no ollama provider.
        new_router = None
        if enabled and resolved:
            try:
                _, effective_backends = get_effective_backends_config()
                new_router = MultiBackendRouter(effective_backends)
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


@app.get("/api/v1/backends/{backend_name}/models")
async def api_backend_models(backend_name: str):
    """
    List available models from a specific backend.

    Used by the web UI to populate model picker when configuring backend restrictions.
    Returns {"models": ["model1", "model2", ...]} or falls back to empty list on error.
    """
    _st = get_state()
    if not _st.backend_router:
        return JSONResponse({"models": []})

    backend = _st.backend_router.get_backend(backend_name)
    if not backend:
        return JSONResponse({"models": []})

    try:
        # Call the backend's list_models() method (async)
        # Unwrap from RetryableBackendWrapper if needed
        inner = getattr(backend, "backend", backend)
        models = await inner.list_models()
        return JSONResponse({"models": models or []})
    except Exception as e:
        logger.warning("Failed to list models from backend '%s': %s", backend_name, e)
        return JSONResponse({"models": []})


@app.get("/api/v1/system-metrics")
async def api_system_metrics():
    """
    Real-time CPU, RAM, and GPU utilization with temperature probes.
    Collected with psutil + pynvml; gracefully degrades if libraries absent.
    Cached 1s server-side; safe to poll at 2s intervals from the frontend.
    """
    data = await collect_system_metrics_async()
    return JSONResponse(data)


@app.get("/api/v1/model-specs")
async def api_model_specs():
    """Return all discovered model resource specs from SQLite."""
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "storage not initialized"}, status_code=503)
    specs = _st.sqlite_store.get_model_specs()
    return JSONResponse({"specs": specs, "count": len(specs)})


@app.get("/api/v1/model-specs/export")
async def api_model_specs_export():
    """Download all model specs as CSV."""
    import io
    import csv
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "storage not initialized"}, status_code=503)
    specs = _st.sqlite_store.get_model_specs()
    buf = io.StringIO()
    if specs:
        writer = csv.DictWriter(buf, fieldnames=specs[0].keys())
        writer.writeheader()
        writer.writerows(specs)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=model_specs.csv"},
    )


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
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    from beigebox.replay import ConversationReplayer
    wire_path = cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")
    replayer = ConversationReplayer(_st.sqlite_store, wiretap_path=wire_path)
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
    _st = get_state()
    if not _st.sqlite_store:
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
        copied = _st.sqlite_store.fork_conversation(
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
    source: str | None = None,
    event_type: str | None = None,
    conv_id: str | None = None,
    run_id: str | None = None,
):
    """
    Return recent wire events with optional filters.

    Query params:
        n           int — max entries (default 50, max 500)
        role        str — user|assistant|system|tool|decision
        dir         str — inbound|outbound|internal  (JSONL compat, ignored for SQLite)
        source      str — proxy|operator|harness|router|cache|classifier
        event_type  str — message|tool_call|routing_decision|op_thought|…
        conv_id     str — filter to one conversation
        run_id      str — filter to one operator/harness run
    """
    import json as _json
    from pathlib import Path as _P

    n = min(max(1, n), 500)
    st = get_state().sqlite_store

    # Primary path: query SQLite wire_events table
    if st is not None:
        try:
            rows = st.get_wire_events(
                n=n,
                event_type=event_type or None,
                source=source or None,
                conv_id=conv_id or None,
                run_id=run_id or None,
                role=role or None,
            )
            return JSONResponse({"entries": rows, "total": len(rows), "filtered": len(rows)})
        except Exception as e:
            logger.warning("api_tap SQLite query failed, falling back to JSONL: %s", e)

    # Fallback: parse JSONL (old behaviour, cold-start before any events written)
    cfg = get_config()
    wire_path = _P(cfg.get("wiretap", {}).get("path", "./data/wire.jsonl"))
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
    cfg = get_config()
    rt  = get_runtime_config()
    if not rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)):
        return JSONResponse({"error": "Harness is disabled."}, status_code=403)

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

    inj_queue: asyncio.Queue = asyncio.Queue()
    _st = get_state()
    orch = HarnessOrchestrator(
        available_targets=targets,
        model=model_override,
        max_rounds=max_rounds,
        task_stagger_seconds=task_stagger,
        backend_router=_st.backend_router,
        injection_queue=inj_queue,
        sqlite_store=_st.sqlite_store,
        wire_log=_st.proxy.wire if _st.proxy else None,
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
                    get_state().harness_injection_queues[run_id] = inj_queue
                yield f"data: {_json.dumps(event)}\n\n"
        except Exception as e:
            error_event = {'type':'error','message':str(e)}
            events.append(error_event)
            error_count += 1
            yield f"data: {_json.dumps(error_event)}\n\n"
        finally:
            if run_id and run_id in get_state().harness_injection_queues:
                del get_state().harness_injection_queues[run_id]
            # Persist the completed run in the finally block so it's always stored
        # even if the SSE stream was interrupted mid-run by the client.
        if orch.store_runs:
                try:
                    from beigebox.storage.sqlite_store import SQLiteStore
                    cfg = get_config()
                    _sq_path, _ = get_storage_paths(cfg)
                    store = SQLiteStore(_sq_path)

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


@app.post("/api/v1/harness/wiggam")
async def api_harness_wiggam(request: Request):
    """
    Wiggam planning phase — multi-agent consensus decomposition.

    Body:
        goal            str        — high-level goal to decompose
        wiggam_model    str (opt)  — model for the planner
        officer_models  list[str]  — critic models (default: [wiggam_model])
        max_rounds      int (opt)  — max debate rounds (default: 5)

    Returns: text/event-stream of JSON events.
    """
    cfg = get_config()
    rt  = get_runtime_config()
    if not rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)):
        return JSONResponse({"error": "Harness is disabled."}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    goal            = (body.get("goal") or "").strip()
    wiggam_model    = body.get("wiggam_model") or None
    officer_models  = body.get("officer_models") or None
    max_rounds      = int(body.get("max_rounds", 5))

    if not goal:
        return JSONResponse({"error": "goal required"}, status_code=400)

    from beigebox.agents.wiggam_planner import WiggamPlanner
    import json as _json

    planner = WiggamPlanner(
        goal=goal,
        wiggam_model=wiggam_model,
        officer_models=officer_models,
        max_rounds=max_rounds,
        backend_router=get_state().backend_router,
    )

    async def _event_stream():
        try:
            async for event in planner.run():
                yield f"data: {_json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/harness/ralph")
async def api_harness_ralph(request: Request):
    """
    Ralph mode — autonomous spec-driven development loop.

    Body:
        spec_path      str (opt)  — path to PROMPT.md (reloaded each iteration)
        spec_inline    str (opt)  — inline spec text (used if spec_path not set)
        test_cmd       str        — shell command to run after each iteration (exit 0 = pass)
        working_dir    str (opt)  — working directory for test_cmd (default: cwd)
        max_iterations int (opt)  — max loop iterations (default: 20)
        model          str (opt)  — model override

    Returns: text/event-stream of JSON events.
    """
    cfg = get_config()
    rt  = get_runtime_config()
    if not rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)):
        return JSONResponse({"error": "Harness is disabled."}, status_code=403)
    if not rt.get("ralph_enabled", cfg.get("harness", {}).get("ralph_enabled", False)):
        return JSONResponse({"error": "Ralph mode is disabled. Set harness.ralph_enabled: true in config.yaml to enable."}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    spec_path      = body.get("spec_path") or None
    spec_inline    = body.get("spec_inline") or None
    test_cmd       = body.get("test_cmd", "").strip()
    working_dir    = body.get("working_dir") or None
    max_iterations = int(body.get("max_iterations", 20))
    model_override = body.get("model") or None

    if not spec_path and not spec_inline:
        return JSONResponse({"error": "spec_path or spec_inline required"}, status_code=400)

    from beigebox.agents.ralph_orchestrator import RalphOrchestrator
    import json as _json

    inj_queue: asyncio.Queue = asyncio.Queue()
    ralph = RalphOrchestrator(
        spec_path=spec_path,
        spec_inline=spec_inline,
        test_cmd=test_cmd,
        working_dir=working_dir,
        max_iterations=max_iterations,
        model=model_override,
        backend_router=get_state().backend_router,
        injection_queue=inj_queue,
    )

    async def _event_stream():
        run_id = ralph.run_id
        get_state().harness_injection_queues[run_id] = inj_queue
        try:
            async for event in ralph.run():
                yield f"data: {_json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
        finally:
            get_state().harness_injection_queues.pop(run_id, None)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/harness/{run_id}/inject")
async def api_harness_inject(run_id: str, request: Request):
    """Inject a steering message into an active orchestration run."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    # Queue lookup is O(1) and safe under concurrent async tasks because
    # Python's GIL serialises dict mutations. The put() is non-blocking since
    # the injection queue is unbounded — the run loop drains it at each round.
    queue = get_state().harness_injection_queues.get(run_id)
    if queue is None:
        return JSONResponse({"error": "run not found or already completed"}, status_code=404)
    await queue.put(message)
    return JSONResponse({"ok": True})


@app.get("/api/v1/harness/{run_id}")
def get_harness_run(run_id: str):
    """
    Retrieve a stored harness orchestration run by ID.

    Returns the full run record including all events for replay/analysis.
    """
    cfg = get_config()
    rt  = get_runtime_config()
    if not rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)):
        return JSONResponse({"error": "Harness is disabled."}, status_code=403)
    try:
        from beigebox.storage.sqlite_store import SQLiteStore
        cfg = get_config()
        _sq_path, _ = get_storage_paths(cfg)
        store = SQLiteStore(_sq_path)

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
    cfg = get_config()
    rt  = get_runtime_config()
    if not rt.get("harness_enabled", cfg.get("harness", {}).get("enabled", True)):
        return JSONResponse({"error": "Harness is disabled."}, status_code=403)
    try:
        # Clamp limit
        limit = min(max(limit, 1), 100)
        
        from beigebox.storage.sqlite_store import SQLiteStore
        cfg = get_config()
        _sq_path, _ = get_storage_paths(cfg)
        store = SQLiteStore(_sq_path)

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
    rt = get_runtime_config()
    op_enabled = rt.get("operator_enabled", cfg.get("operator", {}).get("enabled", False))
    if not op_enabled:
        return JSONResponse(
            {"error": "Operator is disabled. Set operator.enabled: true in config.yaml to enable LLM-driven tool execution."},
            status_code=403,
        )
    try:
        body = await request.json()
        question = body.get("query", "").strip()
        if not question:
            return JSONResponse({"error": "query required"}, status_code=400)
        model_override = body.get("model", "").strip() or None
        history = body.get("history") or None

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
                    path=get_storage_paths(cfg)[1],
                ),
            )
        except Exception:
            vs = None

        try:
            import uuid as _uuid
            _conv_id = _uuid.uuid4().hex[:8]
            _st = get_state()
            _wire = _st.proxy.wire if _st.proxy else None
            op = Operator(vector_store=vs, blob_store=_st.blob_store, model_override=model_override)
            _op_model = op._model
            if _wire:
                _wire.log("inbound", "user", question,
                          model=_op_model, conversation_id=_conv_id)
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, op.run, question, history)
            if _wire:
                _wire.log("outbound", "assistant", answer,
                          model=_op_model, conversation_id=_conv_id)
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


def _reduce_plan_state(workspace_out: Path) -> dict:
    """
    Read plan.md from the operator workspace and extract structured run state.

    Returns a dict:
      found        – bool: plan.md exists and is parseable
      objective    – str: first descriptive line of the plan
      steps        – list[{num, name}]: numbered steps in order
      completed    – set[int]: step numbers marked done in ## Progress
      progress_lines – list[str]: raw lines from ## Progress section
      next_step    – {num, name} | None: lowest-numbered incomplete step
      all_done     – bool: every step is marked done
    """
    import re as _re

    plan_path = workspace_out / "plan.md"
    if not plan_path.exists():
        return {"found": False}

    try:
        text = plan_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"found": False}

    # Split at ## Progress
    progress_idx = text.find("## Progress")
    plan_body = text[:progress_idx] if progress_idx >= 0 else text
    progress_body = text[progress_idx:] if progress_idx >= 0 else ""

    # Objective: first non-blank, non-heading line
    objective = ""
    for line in plan_body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            objective = stripped
            break

    # Numbered steps: "1. Step name" or "1) Step name"
    steps = []
    for m in _re.finditer(r'^(\d+)[.)]\s+(.+)$', plan_body, _re.MULTILINE):
        steps.append({"num": int(m.group(1)), "name": m.group(2).strip()})
    steps.sort(key=lambda s: s["num"])

    # Progress lines (bullet items under ## Progress)
    progress_lines = []
    for line in progress_body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "+")):
            progress_lines.append(stripped.lstrip("-*+ ").strip())

    # Words that indicate a step is NOT yet done — skip these lines
    _NOT_DONE = {"not started", "pending", "todo", "to do", "in progress",
                 "tbd", "upcoming", "incomplete", "not done", "not complete"}

    # Match completed progress lines to steps.
    # A line only counts as "done" if it does NOT contain a "not done" marker
    # and DOES mention the step by name or number.
    completed: set[int] = set()
    for step in steps:
        for pline in progress_lines:
            plow = pline.lower()
            # Skip lines that explicitly say the step isn't done
            if any(nd in plow for nd in _NOT_DONE):
                continue
            if (step["name"].lower() in plow
                    or f"step {step['num']}" in plow
                    or plow.startswith(f"{step['num']}.")):
                completed.add(step["num"])
                break

    next_step = next((s for s in steps if s["num"] not in completed), None)
    all_done = bool(steps) and next_step is None

    return {
        "found": True,
        "objective": objective,
        "steps": steps,
        "completed": completed,
        "progress_lines": progress_lines,
        "next_step": next_step,
        "all_done": all_done,
    }


@app.post("/api/v1/harness/autonomous")
async def api_harness_autonomous(request: Request):
    """
    Autonomous multi-turn operator loop — used by the Harness Agentic panel.

    Each turn the operator executes with a clean, structured context injected
    by the harness state reducer (reads plan.md). The operator tab uses
    /api/v1/operator/stream (single-turn only).

    Body: {"query": "...", "history": [{role, content}, ...], "model": "...", "max_turns": 5}

    SSE events:
      {"type": "start",      "run_id": str}
      {"type": "turn_start", "turn": int, "total": int}
      {"type": "tool_call",  "tool": str, "input": str, "thought": str}
      {"type": "tool_result","tool": str, "result": str}
      {"type": "answer",     "content": str}
      {"type": "error",      "message": str}
    """
    cfg = get_config()
    rt = get_runtime_config()
    op_enabled = rt.get("operator_enabled", cfg.get("operator", {}).get("enabled", False))
    if not op_enabled:
        return JSONResponse(
            {"error": "Operator is disabled. Set operator.enabled: true in config.yaml."},
            status_code=403,
        )

    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    question = body.get("query", "").strip()
    if not question:
        return JSONResponse({"error": "query required"}, status_code=400)

    history = body.get("history") or None
    model_override = body.get("model", "").strip() or None
    max_turns = max(1, int(body.get("max_turns", 5)))

    async def event_stream():
        import json as _json
        import uuid as _uuid
        import time as _time
        _run_id = _uuid.uuid4().hex[:8]
        _conv_id = _uuid.uuid4().hex[:8]
        _st = _app_state
        _wire = _st.proxy.wire if (_st and _st.proxy) else None
        _start_time = _time.time()
        _op_model = model_override or "unknown"
        try:
            from beigebox.storage.vector_store import VectorStore
            from beigebox.storage.backends import make_backend as _mk2
            from beigebox.agents.operator import Operator

            cfg2 = get_config()
            try:
                _sc = cfg2["storage"]
                _ec = cfg2["embedding"]
                vs = VectorStore(
                    embedding_model=_ec["model"],
                    embedding_url=_ec.get("backend_url") or cfg2["backend"]["url"],
                    backend=_mk2(
                        _sc.get("vector_backend", "chromadb"),
                        path=get_storage_paths(cfg2)[1],
                    ),
                )
            except Exception:
                vs = None

            yield f"data: {_json.dumps({'type': 'start', 'run_id': _run_id})}\n\n"

            # Cap history for the run record only — turns always get clean context.
            _raw_history = list(history or [])
            initial_history = _raw_history[-8:] if len(_raw_history) > 8 else _raw_history
            final_answer = None

            from beigebox.agents.pruner import ContextPruner as _ContextPruner
            _pruner = _ContextPruner.from_config()
            from beigebox.agents.reflector import Reflector as _Reflector
            _reflector = _Reflector.from_config()
            from beigebox.agents.shadow import ShadowAgent as _ShadowAgent
            _shadow = _ShadowAgent.from_config()
            _shadow_task = None

            _ws_path_cfg = cfg2.get("workspace", {}).get("path", "./workspace")
            _workspace_out = (Path(__file__).parent.parent / _ws_path_cfg / "out").resolve()

            # Turn 0: self-contained question — no conversation history passed.
            # Subsequent turns receive structured state from the state reducer instead.
            # Each operator subagent gets: system prompt + cur_question only (~9K tokens
            # vs ~15K with accumulated history — see 2600/multi-turn-research.md).
            cur_question = (
                question + "\n\n"
                "[Autonomous run] Start by writing a plan.md file to /workspace/out/ "
                "using the workspace_file tool. Include: the task summary, your numbered "
                "step-by-step plan (e.g. '1. Step name'), and a '## Progress' section "
                "you will update each turn. Then complete step 1."
            )

            for turn_n in range(max_turns):
                if turn_n > 0:
                    remaining = max_turns - turn_n
                    yield f"data: {_json.dumps({'type': 'turn_start', 'turn': turn_n + 1, 'total': max_turns})}\n\n"

                    # Inject reflector insight if ready from previous turn
                    _insight = _reflector.consume_insight()
                    if _insight and final_answer:
                        final_answer += f"\n\n[Reflection] {_insight}"

                    # State reducer: harness reads plan.md so the model doesn't have to replay history
                    state = _reduce_plan_state(_workspace_out)

                    if state.get("found") and state.get("steps"):
                        steps = state["steps"]
                        completed = state["completed"]
                        done_count = len(completed)
                        total_count = len(steps)
                        next_step = state.get("next_step")

                        step_lines = []
                        for s in steps:
                            if s["num"] in completed:
                                tag = "[DONE]"
                            elif next_step and s["num"] == next_step["num"]:
                                tag = "[NEXT]"
                            else:
                                tag = "[    ]"
                            step_lines.append(f"  {s['num']}. {tag} {s['name']}")
                        steps_display = "\n".join(step_lines)

                        if state.get("all_done"):
                            cur_question = (
                                f"Turn {turn_n + 1} of {max_turns}.\n\n"
                                f"All {total_count} steps are complete:\n{steps_display}\n\n"
                                f"Write a concise final summary of what was built and where "
                                f"the files are, then end with ##DONE##."
                            )
                        elif next_step:
                            ns = next_step
                            is_last = done_count + 1 >= total_count
                            cur_question = (
                                f"Turn {turn_n + 1} of {max_turns} ({remaining} turns left).\n\n"
                                f"## Project state (read from plan.md by harness)\n\n"
                                f"**Objective:** {state['objective']}\n\n"
                                f"**Steps:**\n{steps_display}\n\n"
                                f"**Progress:** {done_count} of {total_count} steps complete.\n\n"
                                f"## Your task this turn\n\n"
                                f"Implement step {ns['num']}: \"{ns['name']}\" completely.\n"
                                f"- Write all code/files to /workspace/out/ using workspace_file\n"
                                f"- Do NOT work on any other step\n"
                                f"- When done, APPEND to plan.md under ## Progress: "
                                f"`- Turn {turn_n + 1}: {ns['name']} done`\n"
                                + (
                                    "- This is the last step — end your answer with ##DONE## after completing it."
                                    if is_last else ""
                                )
                            )
                        else:
                            cur_question = (
                                f"Turn {turn_n + 1} of {max_turns} ({remaining} turns left).\n\n"
                                f"Continue with the next incomplete step. "
                                f"When all steps are done, end with ##DONE##."
                            )
                    else:
                        cur_question = (
                            f"Turn {turn_n + 1} of {max_turns} ({remaining} turns left).\n\n"
                            f"READ plan.md with workspace_file to review the plan.\n"
                            f"Continue with the next incomplete step. "
                            f"When all work is done, end your answer with ##DONE##."
                        )

                # Fire shadow agent on turn 0 (fire-and-forget, collected after stream)
                if turn_n == 0 and _shadow.enabled:
                    _shadow_task = asyncio.ensure_future(_shadow.run_shadow(question, vs))

                op = Operator(vector_store=vs, model_override=model_override, autonomous=True,
                              sqlite_store=_st.sqlite_store if _st else None)
                _op_model = op._model

                if _wire and turn_n == 0:
                    _wire.log("inbound", "user", question, model=_op_model, conversation_id=_conv_id)
                elif _wire:
                    _wire.log("inbound", "user", cur_question, model=_op_model, conversation_id=_conv_id)

                turn_answer = None
                # Context isolation: every turn is a clean subagent call — no history.
                async for event in op.run_stream(cur_question, []):
                    if event.get("type") == "answer":
                        turn_answer = event.get("content", "")
                    if _wire:
                        etype = event.get("type")
                        if etype == "tool_call":
                            _wire.log("internal", "tool",
                                      f"{event.get('thought','')} → {event.get('input','')}",
                                      tool_name=event.get("tool", ""), model=_op_model, conversation_id=_conv_id)
                        elif etype == "tool_result":
                            _wire.log("internal", "tool", event.get("result", ""),
                                      tool_name=event.get("tool", ""), model=_op_model, conversation_id=_conv_id)
                        elif etype == "answer":
                            _wire.log("outbound", "assistant", event.get("content", ""),
                                      model=_op_model, conversation_id=_conv_id)
                        elif etype == "error":
                            _wire.log("outbound", "system", f"operator error: {event.get('message','')}",
                                      model=_op_model, conversation_id=_conv_id)
                    yield f"data: {_json.dumps(event)}\n\n"

                final_answer = turn_answer

                # Collect shadow result on turn 0 and emit if it diverges
                if turn_n == 0 and _shadow.enabled and _shadow_task is not None:
                    _shadow_answer = await _shadow.collect(_shadow_task, wait=2.0)
                    if _shadow_answer and _ShadowAgent.diverges(
                        final_answer or "", _shadow_answer, _shadow._divergence_threshold
                    ):
                        yield f"data: {_json.dumps({'type': 'alternative_plan', 'content': _shadow_answer})}\n\n"

                # Fire-and-forget reflection on completed turn
                if _reflector.enabled and turn_answer:
                    await _reflector.reflect_async(turn_answer, cur_question, f"turn {turn_n + 1}")

                # Context pruner: compress turn answer to reduce inter-turn token bloat
                if _pruner.enabled and turn_answer and turn_n + 1 < max_turns:
                    _next_step_name = f"turn {turn_n + 2}"
                    try:
                        _loop = asyncio.get_event_loop()
                        _pruned = await asyncio.wait_for(
                            _loop.run_in_executor(None, _pruner.prune, turn_answer, _next_step_name),
                            timeout=_pruner._timeout + 1,
                        )
                        final_answer = _pruned
                    except Exception:
                        pass  # keep original

                if turn_answer and "##DONE##" in turn_answer:
                    break

            _latency_ms = int((_time.time() - _start_time) * 1000)
            try:
                if _st and _st.sqlite_store:
                    _st.sqlite_store.store_operator_run(
                        run_id=_run_id, query=question, history=initial_history,
                        model=_op_model, status="completed",
                        result=final_answer or "", latency_ms=_latency_ms,
                    )
            except Exception as store_err:
                logger.warning("Failed to store autonomous run: %s", store_err)

        except Exception as e:
            if _wire:
                _wire.log("outbound", "system", f"autonomous exception: {e}", conversation_id=_conv_id)
            import json as _json2
            _latency_ms = int((_time.time() - _start_time) * 1000)
            try:
                if _st and _st.sqlite_store:
                    _st.sqlite_store.store_operator_run(
                        run_id=_run_id, query=question, history=history or [],
                        model=_op_model, status="error",
                        result=str(e), latency_ms=_latency_ms,
                    )
            except Exception as store_err:
                logger.warning("Failed to store autonomous error run: %s", store_err)
            yield f"data: {_json2.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/operator/stream")
async def api_operator_stream(request: Request):
    """
    Run the operator agent (single-turn) with streaming progress events (SSE).

    For multi-turn autonomous execution use POST /api/v1/harness/autonomous.

    Body: {"query": "...", "history": [{role, content}, ...], "model": "..."}

    SSE events:
      {"type": "tool_call",   "tool": str, "input": str, "thought": str}
      {"type": "tool_result", "tool": str, "result": str}
      {"type": "answer",      "content": str}
      {"type": "error",       "message": str}
    """
    cfg = get_config()
    rt = get_runtime_config()
    op_enabled = rt.get("operator_enabled", cfg.get("operator", {}).get("enabled", False))
    if not op_enabled:
        return JSONResponse(
            {"error": "Operator is disabled. Set operator.enabled: true in config.yaml to enable LLM-driven tool execution."},
            status_code=403,
        )

    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    question = body.get("query", "").strip()
    if not question:
        return JSONResponse({"error": "query required"}, status_code=400)

    history = body.get("history") or None
    model_override = body.get("model", "").strip() or None

    async def event_stream():
        import json as _json
        import uuid as _uuid
        import time as _time
        _run_id = _uuid.uuid4().hex[:8]
        _conv_id = _uuid.uuid4().hex[:8]
        _st = _app_state
        _wire = _st.proxy.wire if (_st and _st.proxy) else None
        _start_time = _time.time()
        _op_model = model_override or "unknown"
        try:
            from beigebox.storage.vector_store import VectorStore
            from beigebox.storage.backends import make_backend as _mk2
            from beigebox.agents.operator import Operator

            cfg2 = get_config()
            try:
                _sc = cfg2["storage"]
                _ec = cfg2["embedding"]
                vs = VectorStore(
                    embedding_model=_ec["model"],
                    embedding_url=_ec.get("backend_url") or cfg2["backend"]["url"],
                    backend=_mk2(
                        _sc.get("vector_backend", "chromadb"),
                        path=get_storage_paths(cfg2)[1],
                    ),
                )
            except Exception:
                vs = None

            yield f"data: {_json.dumps({'type': 'start', 'run_id': _run_id})}\n\n"

            _raw_history = list(history or [])
            initial_history = _raw_history[-8:] if len(_raw_history) > 8 else _raw_history

            op = Operator(vector_store=vs, model_override=model_override,
                          sqlite_store=_st.sqlite_store if _st else None)
            _op_model = op._model

            if _wire:
                _wire.log("inbound", "user", question, model=_op_model, conversation_id=_conv_id)

            had_tool_call = False
            final_answer = None
            _traj_events: list[dict] = []

            async for event in op.run_stream(question, initial_history):
                if event.get("type") == "tool_call":
                    had_tool_call = True
                elif event.get("type") == "answer":
                    final_answer = event.get("content", "")
                _traj_events.append(event)

                if _wire:
                    etype = event.get("type")
                    if etype == "tool_call":
                        _wire.log("internal", "tool",
                                  f"{event.get('thought','')} → {event.get('input','')}",
                                  tool_name=event.get("tool", ""),
                                  model=_op_model, conversation_id=_conv_id)
                    elif etype == "tool_result":
                        _wire.log("internal", "tool",
                                  event.get("result", ""),
                                  tool_name=event.get("tool", ""),
                                  model=_op_model, conversation_id=_conv_id)
                    elif etype == "answer":
                        _wire.log("outbound", "assistant",
                                  event.get("content", ""),
                                  model=_op_model, conversation_id=_conv_id)
                    elif etype == "error":
                        _wire.log("outbound", "system",
                                  f"operator error: {event.get('message','')}",
                                  model=_op_model, conversation_id=_conv_id)
                yield f"data: {_json.dumps(event)}\n\n"

            if not had_tool_call:
                yield f"data: {_json.dumps({'type': 'info', 'message': 'No tools used — operator answered directly'})}\n\n"

            # Trajectory evaluation — score the run and emit as SSE
            try:
                from beigebox.trajectory import score_run as _score_run
                _score = _score_run(question, _traj_events, 1, final_answer or "")
                yield f"data: {_json.dumps({'type': 'run_score', **_score})}\n\n"
            except Exception as score_err:
                logger.debug("Trajectory scoring failed: %s", score_err)
                _score = None

            # Store successful run to database
            _latency_ms = int((_time.time() - _start_time) * 1000)
            try:
                if _st and _st.sqlite_store:
                    _st.sqlite_store.store_operator_run(
                        run_id=_run_id,
                        query=question,
                        history=initial_history,
                        model=_op_model,
                        status="completed",
                        result=final_answer or "",
                        latency_ms=_latency_ms,
                    )
                    if _score:
                        _st.sqlite_store.store_run_score(_run_id, _score)
            except Exception as store_err:
                logger.warning("Failed to store operator run: %s", store_err)

        except Exception as e:
            if _wire:
                _wire.log("outbound", "system", f"operator exception: {e}",
                          conversation_id=_conv_id)
            import json as _json2

            _latency_ms = int((_time.time() - _start_time) * 1000)
            try:
                if _st and _st.sqlite_store:
                    _st.sqlite_store.store_operator_run(
                        run_id=_run_id,
                        query=question,
                        history=history or [],
                        model=_op_model,
                        status="error",
                        result=str(e),
                        latency_ms=_latency_ms,
                    )
            except Exception as store_err:
                logger.warning("Failed to store operator error run: %s", store_err)

            yield f"data: {_json2.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Operator notes — cross-session persistent context
# ---------------------------------------------------------------------------

def _notes_path() -> Path:
    # Resolve against the project root (two levels up from this file) so the
    # path is correct whether BeigeBox is run as a package or from the repo root.
    cfg = get_config()
    ws_raw = cfg.get("workspace", {}).get("path", "./workspace")
    return (Path(__file__).parent.parent / ws_raw / "out" / "operator_notes.md").resolve()


@app.get("/api/v1/operator/notes")
async def api_operator_notes_get():
    """Read the operator's persistent notes file (workspace/out/operator_notes.md)."""
    p = _notes_path()
    if not p.exists():
        return JSONResponse({"content": "", "exists": False})
    try:
        return JSONResponse({"content": p.read_text(encoding="utf-8"), "exists": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/v1/operator/notes")
async def api_operator_notes_set(request: Request):
    """Write the operator's persistent notes file."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "")
    p = _notes_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return JSONResponse({"ok": True, "bytes": len(content.encode())})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Operator Run Retrieval & History
# ---------------------------------------------------------------------------

@app.get("/api/v1/operator/runs")
async def api_operator_list_runs():
    """List recent operator runs."""
    if not sqlite_store:
        return JSONResponse({"error": "storage not available"}, status_code=503)

    runs = sqlite_store.list_operator_runs(limit=50)
    return JSONResponse({"runs": runs})


@app.get("/api/v1/operator/{run_id}")
async def api_operator_get_run(run_id: str):
    """Retrieve a completed operator run by ID."""
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "storage not available"}, status_code=503)

    run = _st.sqlite_store.get_operator_run(run_id)
    if not run:
        return JSONResponse({"error": f"Run '{run_id}' not found"}, status_code=404)

    return JSONResponse(run)


@app.get("/api/v1/operator/runs")
async def api_operator_list_runs():
    """List recent operator runs."""
    _st = get_state()
    if not _st.sqlite_store:
        return JSONResponse({"error": "storage not available"}, status_code=503)

    runs = _st.sqlite_store.list_operator_runs(limit=50)
    return JSONResponse({"runs": runs})



# ---------------------------------------------------------------------------
# Council — "council then commander" pattern
# ---------------------------------------------------------------------------

# In-memory council session store: run_id → {query, operator_model, backend_url}.
# Cleared on restart. Sessions are only needed for the duration of a single
# propose → engage cycle; no persistence required.
_council_sessions: dict[str, dict] = {}


@app.post("/api/v1/council/propose")
async def api_council_propose(request: Request):
    """
    Phase 1: operator proposes a specialist council for the query.

    Body: {"query": "...", "model": "optional override"}
    Returns: {"run_id": "...", "council": [{name, model, task}, ...]}
    """
    import uuid as _uuid
    from beigebox.agents.council import propose as _council_propose

    cfg = get_config()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    query = body.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)

    _rt = get_runtime_config()
    operator_model = (
        body.get("model", "").strip()
        or _rt.get("operator_model")
        or cfg.get("operator", {}).get("model")
        or cfg.get("backend", {}).get("default_model", "")
    )
    backend_url = cfg.get("backend", {}).get("url", "http://localhost:11434")
    allowed_models = body.get("allowed_models") or None
    if allowed_models and not isinstance(allowed_models, list):
        allowed_models = None

    try:
        council = await _council_propose(query, backend_url, operator_model, allowed_models=allowed_models)
    except Exception as e:
        logger.error("council propose error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=503)

    run_id = str(_uuid.uuid4())[:8]
    _council_sessions[run_id] = {
        "query":          query,
        "operator_model": operator_model,
        "backend_url":    backend_url,
        "run_id":         run_id,
    }

    # Wiretap: log the incoming query and the proposed council lineup
    _st = get_state()
    if _st.proxy and _st.proxy.wire:
        _st.proxy.wire.log("inbound", "user", query,
                       model=operator_model, conversation_id=run_id)
        member_summary = ", ".join(
            f"{m['name']}({m['model']})" for m in council
        )
        _st.proxy.wire.log("internal", "decision",
                       f"council proposed: {member_summary}",
                       model=operator_model, conversation_id=run_id)

    return JSONResponse({"run_id": run_id, "council": council})


@app.post("/api/v1/council/{run_id}/engage")
async def api_council_engage(run_id: str, request: Request):
    """
    Phase 2: dispatch the (possibly user-edited) council and stream results.

    Body: {"council": [{name, model, task}, ...]}
    Returns: SSE stream of events:
      {type:"dispatch", count:N}
      {type:"member_start", name, model}
      {type:"member_done", name, model, result}
      {type:"member_error", name, error}
      {type:"synthesizing"}
      {type:"synthesis", result}
      {type:"error", message}
      {type:"done"}
    """
    import json as _json
    from beigebox.agents.council import execute as _council_execute
    from starlette.responses import StreamingResponse as _SR

    session = _council_sessions.get(run_id)
    if not session:
        return JSONResponse({"error": "unknown run_id — propose first"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    council = body.get("council", [])
    if not council:
        return JSONResponse({"error": "council required"}, status_code=400)

    query          = session["query"]
    operator_model = session["operator_model"]
    backend_url    = session["backend_url"]

    _st = get_state()
    _wire = _st.proxy.wire if _st.proxy else None

    async def _stream():
        from pathlib import Path as _Path
        _ws_out = _Path(__file__).parent.parent / "workspace" / "out"

        yield f"data: {_json.dumps({'type': 'dispatch', 'count': len(council)})}\n\n"
        try:
            async for event in _council_execute(query, council, backend_url, operator_model):
                # Wiretap each council event
                if _wire:
                    etype = event.get("type")
                    if etype == "member_done":
                        _wire.log("internal", "assistant",
                                  event.get("result", ""),
                                  model=event.get("model", ""),
                                  conversation_id=run_id,
                                  tool_name=event.get("name", ""))
                    elif etype == "member_error":
                        _wire.log("internal", "system",
                                  f"member error [{event.get('name','')}]: {event.get('error','')}",
                                  conversation_id=run_id)
                    elif etype == "synthesis":
                        _wire.log("outbound", "assistant",
                                  event.get("result", ""),
                                  model=operator_model, conversation_id=run_id)
                yield f"data: {_json.dumps(event)}\n\n"
                if event.get("type") == "synthesis":
                    try:
                        _ws_out.mkdir(parents=True, exist_ok=True)
                        out_file = _ws_out / f"council_{run_id}.md"
                        parts = [f"# Council Synthesis\n\n**Query:** {query}\n\n## Specialists\n\n"]
                        for m in council:
                            parts.append(f"- **{m['name']}** ({m['model']}): {m['task']}\n")
                        parts.append(f"\n---\n\n{event['result']}")
                        out_file.write_text("".join(parts), encoding="utf-8")
                        yield f"data: {_json.dumps({'type': 'saved', 'path': f'workspace/out/council_{run_id}.md'})}\n\n"
                    except Exception:
                        pass
        except Exception as e:
            if _wire:
                _wire.log("outbound", "system",
                          f"council error: {e}", conversation_id=run_id)
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            # Always remove the session even if the stream was interrupted —
            # prevents stale run_ids from leaking memory and blocking re-use.
            _council_sessions.pop(run_id, None)
        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return _SR(_stream(), media_type="text/event-stream")


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
    except (ValueError, UnicodeDecodeError) as e:
        return JSONResponse({"error": f"Invalid JSON: {e}"}, status_code=400)

    prompt = body.get("prompt", "").strip()
    models = body.get("models", [])
    judge_model = body.get("judge_model")

    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    if not models:
        return JSONResponse({"error": "models list is required"}, status_code=400)

    from beigebox.agents.ensemble_voter import EnsembleVoter

    voter = EnsembleVoter(models=models, judge_model=judge_model, backend_router=get_state().backend_router)

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


# ---------------------------------------------------------------------------

@app.post("/api/v1/build-centroids")
async def api_build_centroids():
    """
    Rebuild embedding classifier centroids from seed prompts.
    Equivalent to `beigebox build-centroids` CLI command.
    Runs synchronously — may take 10-30s depending on embedding model speed.
    """
    _st = get_state()
    if not _st.embedding_classifier:
        return JSONResponse({"success": False, "error": "Embedding classifier not initialized"}, status_code=503)
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        success = await loop.run_in_executor(None, _st.embedding_classifier.build_centroids)
        if success:
            return JSONResponse({"success": True, "message": "Centroids built successfully"})
        else:
            return JSONResponse({"success": False, "error": "build_centroids() returned False — check Ollama is running"}, status_code=500)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.get("/api/v1/workspace")
async def api_workspace():
    """List files in workspace/in and workspace/out with sizes and timestamps."""
    cfg = get_config()
    ws_cfg = cfg.get("workspace", {})
    ws_path_raw = ws_cfg.get("path", "./workspace")
    max_mb = ws_cfg.get("max_mb", 0)

    # Resolve relative paths from the app root (parent of the beigebox package dir)
    app_root = Path(__file__).parent.parent
    ws_path = (app_root / ws_path_raw).resolve()

    def scan_dir(dirpath: Path) -> tuple[list[dict], int]:
        entries = []
        total = 0
        if not dirpath.exists():
            return entries, total
        for entry in os.scandir(dirpath):
            if entry.name == ".gitkeep":
                continue
            try:
                stat = entry.stat()
            except (FileNotFoundError, OSError):
                # Broken symlink or inaccessible entry — skip it
                continue
            entries.append({
                "name": entry.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "is_link": entry.is_symlink(),
            })
            total += stat.st_size
        entries.sort(key=lambda e: e["name"])
        return entries, total

    in_files, in_bytes = scan_dir(ws_path / "in")
    out_files, out_bytes = scan_dir(ws_path / "out")

    return JSONResponse({
        "in": in_files,
        "out": out_files,
        "in_bytes": in_bytes,
        "out_bytes": out_bytes,
        "max_mb": max_mb,
    })


@app.delete("/api/v1/workspace/out/{filename}")
async def api_workspace_delete(filename: str):
    """Delete a file from workspace/out/. Guards against path traversal."""
    if "/" in filename or ".." in filename:
        return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent
    target = (app_root / ws_path_raw / "out" / filename).resolve()

    # Path traversal guard: resolve() canonicalises symlinks and ".." so the
    # startswith check is a reliable confinement test. os.sep suffix ensures
    # "workspace/out2" cannot match the "workspace/out" prefix.
    out_dir = (app_root / ws_path_raw / "out").resolve()
    if not str(target).startswith(str(out_dir) + os.sep) and target != out_dir:
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)

    if not target.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)

    target.unlink()
    return JSONResponse({"ok": True})


@app.get("/api/v1/openrouter/models")
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


@app.get("/api/v1/openrouter/pinned")
async def openrouter_pinned_get():
    """Return current pinned model IDs."""
    pinned = get_runtime_config().get("openrouter_pinned_models", [])
    return JSONResponse({"pinned": pinned})


@app.post("/api/v1/openrouter/pinned")
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


@app.get("/api/v1/openrouter/balance")
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


# ── Artificial Analysis rankings cache ───────────────────────────────────────

_aa_cache: dict = {"data": None, "fetched_at": 0.0}
_AA_TTL = 3600  # 1 hour cache

async def _fetch_aa_rankings() -> dict:
    """Scrape Artificial Analysis agentic/coding rankings from the public page."""
    now = time.time()
    if _aa_cache["data"] and (now - _aa_cache["fetched_at"]) < _AA_TTL:
        return _aa_cache["data"]

    log = logging.getLogger(__name__)
    url = "https://artificialanalysis.ai/models/capabilities/agentic"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.warning("AA fetch failed: %s", e)
        if _aa_cache["data"]:
            return _aa_cache["data"]
        return {"agentic": [], "coding": []}

    try:
        models = _parse_aa_models(html)
    except Exception as e:
        log.warning("AA parse failed: %s", e)
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
    log.info("AA rankings refreshed: %d agentic, %d coding", len(result["agentic"]), len(result["coding"]))
    return result


def _parse_aa_models(html: str) -> list[dict]:
    """Extract model list from Artificial Analysis Next.js RSC payload."""
    marker = '\\"defaultData\\":'
    idx = html.find(marker)
    if idx < 0:
        raise ValueError("defaultData marker not found")

    # Find the enclosing self.__next_f.push([1,"..."]) call
    push_prefix = 'self.__next_f.push([1,"'
    push_start = html.rfind(push_prefix, 0, idx)
    if push_start < 0:
        raise ValueError("push() wrapper not found")
    content_start = push_start + len(push_prefix)

    # Find end of the JS string literal (unescaped " followed by ])
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

    # JSON-aware bracket tracking to find array end
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


@app.get("/api/v1/artificial-analysis/rankings")
async def artificial_analysis_rankings():
    """Return top-15 agentic and coding model rankings from Artificial Analysis."""
    data = await _fetch_aa_rankings()
    return JSONResponse(data)


def _index_document(file_path: Path, vs, bs) -> None:
    """Parse, chunk, embed, and store a workspace document.  Runs in a thread."""
    from beigebox.storage.chunker import chunk_text

    _log = logging.getLogger(__name__)
    source = file_path.name
    suffix = file_path.suffix.lower()

    try:
        if suffix == ".pdf":
            try:
                import pdf_oxide as pox
                doc = pox.PdfDocument(str(file_path))
                parts = []
                for i in range(doc.page_count()):
                    md = doc.to_markdown(i, detect_headings=True)
                    if md.strip():
                        parts.append(md)
                text = "\n\n".join(parts)
            except Exception as e:
                _log.warning("_index_document: pdf_oxide failed for %s: %s", source, e)
                return
        else:
            # Plain text, markdown, code files, etc.
            text = file_path.read_text(encoding="utf-8", errors="replace")

        if not text.strip():
            _log.info("_index_document: %s is empty, skipping", source)
            return

        chunks = chunk_text(text, source_file=source)
        for chunk in chunks:
            blob_hash = bs.write(chunk["text"])
            vs.store_document_chunk(
                source_file=source,
                chunk_index=chunk["chunk_index"],
                char_offset=chunk["char_offset"],
                blob_hash=blob_hash,
                text=chunk["text"],
            )

        _log.info("_index_document: indexed %d chunks from %s", len(chunks), source)

    except Exception as e:
        _log.error("_index_document failed for %s: %s", source, e)


@app.post("/api/v1/workspace/upload")
async def api_workspace_upload(file: UploadFile):
    """Upload a file to workspace/in/. Guards against path traversal."""
    filename = Path(file.filename or "upload").name
    if not filename or ".." in filename:
        return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent
    in_dir = (app_root / ws_path_raw / "in").resolve()
    in_dir.mkdir(parents=True, exist_ok=True)

    target = (in_dir / filename).resolve()
    if not str(target).startswith(str(in_dir) + os.sep):
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)

    try:
        content = await file.read()
        target.write_bytes(content)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # Chunk and embed the file in a background thread — keeps the upload
    # response fast even for large PDFs. The file is already saved to disk
    # so if indexing fails the file is still accessible via workspace/in.
    _st = get_state()
    if _st.vector_store and _st.blob_store:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _index_document, target, _st.vector_store, _st.blob_store)

    return JSONResponse({"ok": True, "name": filename, "size": len(content)})


@app.post("/api/v1/transform/pdf")
async def api_transform_pdf(file: UploadFile):
    """
    Accept a PDF upload and return its text/markdown content via the pdf_oxide
    WASM module.  Falls back to a plain error if WASM is unavailable.

    Response: {"ok": true, "text": "...", "chars": N, "filename": "..."}
    """
    _st = get_state()
    if not _st.proxy:
        return JSONResponse({"ok": False, "error": "proxy not initialized"}, status_code=503)

    filename = Path(file.filename or "upload.pdf").name
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)

    text = await _st.proxy.wasm_runtime.transform_input("pdf_oxide", raw)
    if not text:
        return JSONResponse(
            {"ok": False, "error": "pdf_oxide WASM module not loaded or returned empty"},
            status_code=422,
        )

    return JSONResponse({"ok": True, "text": text, "chars": len(text), "filename": filename})


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
    # Strip hop-by-hop headers that must not be forwarded — host would misdirect
    # the request; content-length is re-computed by httpx from the body bytes.
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    # Wire log entry via proxy.wire (WireLog)
    _st = get_state()
    if _st.proxy and _st.proxy.wire:
        try:
            body_preview = body[:400].decode("utf-8", errors="replace") if body else ""
        except Exception:
            body_preview = ""
        _st.proxy.wire.log(
            direction="internal",
            role="proxy",
            content=f"[{request.method}] {route_label} → {target}\n{body_preview}",
            model="",
            conversation_id="",
        )

    async def _stream():
        resp_status = None
        total_bytes = 0
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    request.method,
                    target,
                    headers=headers,
                    content=body,
                ) as resp:
                    resp_status = resp.status_code
                    async for chunk in resp.aiter_bytes():
                        total_bytes += len(chunk)
                        yield chunk
        finally:
            if _st.proxy and _st.proxy.wire:
                _st.proxy.wire.log(
                    direction="outbound",
                    role="proxy",
                    content=f"[{request.method}] {route_label} ← HTTP {resp_status} ({total_bytes} bytes)",
                )

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

# ---------------------------------------------------------------------------
# Context Optimization Discovery Framework
# ---------------------------------------------------------------------------

@app.get("/api/v1/discovery/opportunities")
async def discovery_list_opportunities():
    """List all registered discovery opportunities with metadata."""
    from beigebox.discovery import list_opportunities
    return JSONResponse({"opportunities": list_opportunities(), "count": len(list_opportunities())})


@app.post("/api/v1/discovery/run")
async def discovery_run(request: Request):
    """
    Run a discovery experiment for context optimization.

    POST /api/v1/discovery/run
    {
        "opportunity_id": "position_sensitivity",   // use a registered ID to auto-load
        "weight_profile": "general" | "code" | "reasoning" | "safety",

        // Optional overrides (omit to use the opportunity's built-in variants/test_cases):
        "variants": [...],
        "test_cases": [...]
    }

    Returns:
    {
        "run_id": str,
        "pareto_front": [...],
        "champion": {...},
        "scorecards": [...],
        "statistics": {...}   // Welch's t-test vs. baseline per challenger
    }
    """
    import uuid
    from beigebox.discovery import get_opportunity
    from beigebox.discovery.runner import DiscoveryRunner

    state = get_state()
    body = await request.json()

    opportunity_id = body.get("opportunity_id", "unknown")
    weight_profile = body.get("weight_profile", "general")
    candidate_model = body.get("candidate_model") or None  # None → runner uses default
    judge_model = body.get("judge_model") or None

    # Try to resolve a registered opportunity
    opportunity = get_opportunity(opportunity_id)

    # Custom variants/test_cases override the opportunity's defaults
    variants_override = body.get("variants")
    test_cases_override = body.get("test_cases")

    if opportunity is None and not variants_override:
        return JSONResponse(
            {"error": f"Unknown opportunity_id '{opportunity_id}' and no variants provided. "
                      f"GET /api/v1/discovery/opportunities for valid IDs."},
            status_code=400,
        )

    try:
        runner = DiscoveryRunner(
            sqlite_store=state.sqlite_store,
            candidate_model=candidate_model,
            judge_model=judge_model,
        )

        if opportunity and not variants_override:
            # Fully typed path — uses opportunity's transform() and test_cases()
            if weight_profile != "general":
                opportunity.WEIGHT_PROFILE = weight_profile
            result = await runner.run(opportunity)
        else:
            # Generic / override path
            variants = variants_override or (opportunity.VARIANTS if opportunity else [])
            if not variants:
                return JSONResponse({"error": "variants list required"}, status_code=400)
            result = await runner.run_dict(
                body={
                    "opportunity_id": opportunity_id,
                    "opportunity_name": body.get("opportunity_name", opportunity_id),
                    "variants": variants,
                    "test_cases": test_cases_override or [],
                    "weight_profile": weight_profile,
                },
                opportunity=opportunity,
            )

        return JSONResponse(result)

    except Exception as e:
        logger.exception("Discovery experiment failed: %s", e)
        return JSONResponse(
            {"error": f"Discovery failed: {str(e)}", "run_id": str(uuid.uuid4())[:8]},
            status_code=500,
        )


@app.get("/api/v1/discovery/results")
async def discovery_results(
    request: Request,
    opportunity_id: str | None = None,
    run_id: str | None = None,
    n: int = 100,
):
    """
    Fetch discovery scorecard results.

    GET /api/v1/discovery/results?opportunity_id=X&run_id=Y&n=50
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    results = state.sqlite_store.get_discovery_scorecards(
        opportunity_id=opportunity_id,
        run_id=run_id,
        n=n,
    )

    return JSONResponse({
        "results": results,
        "count": len(results),
    })


# ---------------------------------------------------------------------------
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
# Orchestration Profiles Management
# ---------------------------------------------------------------------------

@app.get("/api/v1/orchestrations")
async def list_orchestrations(enabled_only: bool = False):
    """
    List all orchestration profiles.

    GET /api/v1/orchestrations?enabled_only=false
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    profiles = state.sqlite_store.list_orchestration_profiles(enabled_only=enabled_only)
    return JSONResponse({"profiles": profiles, "count": len(profiles)})


@app.post("/api/v1/orchestrations")
async def create_orchestration(request: Request):
    """
    Create a new orchestration profile.

    POST /api/v1/orchestrations
    {
        "name": "aggressive_research",
        "description": "Fast research with many tool calls",
        "worker_type": "RESEARCH",
        "config": {...},
        "max_rounds": 12,
        "max_iterations": 15
    }
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    body = await request.json()
    name = body.get("name", "").strip()

    if not name:
        return JSONResponse(
            {"error": "name required"},
            status_code=400,
        )

    try:
        profile = state.sqlite_store.create_orchestration_profile(
            name=name,
            description=body.get("description"),
            config=body.get("config", {}),
            worker_type=body.get("worker_type"),
            max_rounds=body.get("max_rounds", 8),
            max_iterations=body.get("max_iterations", 10),
        )
        return JSONResponse(profile, status_code=201)
    except Exception as e:
        logger.exception(f"Failed to create orchestration profile: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )


@app.get("/api/v1/orchestrations/{name}")
async def get_orchestration(name: str):
    """
    Get a specific orchestration profile.

    GET /api/v1/orchestrations/aggressive_research
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    profile = state.sqlite_store.get_orchestration_profile(name)

    if not profile:
        return JSONResponse(
            {"error": "not found"},
            status_code=404,
        )

    return JSONResponse(profile)


@app.put("/api/v1/orchestrations/{name}")
async def update_orchestration(name: str, request: Request):
    """
    Update an orchestration profile.

    PUT /api/v1/orchestrations/aggressive_research
    {
        "description": "Updated description",
        "max_rounds": 20
    }
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    body = await request.json()

    try:
        profile = state.sqlite_store.update_orchestration_profile(name, **body)

        if not profile:
            return JSONResponse(
                {"error": "not found"},
                status_code=404,
            )

        return JSONResponse(profile)
    except Exception as e:
        logger.exception(f"Failed to update orchestration profile: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )


@app.delete("/api/v1/orchestrations/{name}")
async def delete_orchestration(name: str):
    """
    Delete an orchestration profile.

    DELETE /api/v1/orchestrations/aggressive_research
    """
    state = get_state()

    if not state.sqlite_store:
        return JSONResponse(
            {"error": "SQLite store not configured"},
            status_code=503,
        )

    deleted = state.sqlite_store.delete_orchestration_profile(name)

    if not deleted:
        return JSONResponse(
            {"error": "not found"},
            status_code=404,
        )

    return JSONResponse({"status": "deleted"})


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
