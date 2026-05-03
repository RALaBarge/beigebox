"""
BeigeBox — LLM Middleware Control Plane

LICENSING: Dual-licensed under AGPL-3.0 (free) and Commercial License (proprietary).
See LICENSE.md and COMMERCIAL_LICENSE.md for details.

FastAPI application — the BeigeBox entry point. Wires app construction,
middleware, lifespan, and router registration. Endpoint handlers live in
beigebox/routers/; storage, security, and proxy initialization runs through
the lifespan() startup path.
"""

import asyncio
import logging
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from beigebox import __version__ as _BB_VERSION
from beigebox.config import (
    get_config,
    get_effective_backends_config,
    get_storage_paths,
)
from beigebox.proxy import Proxy
from beigebox.storage.vector_store import VectorStore
from beigebox.tools.registry import ToolRegistry
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
from beigebox.hooks import HookManager
from beigebox.backends.router import MultiBackendRouter
from beigebox.costs import CostTracker
from beigebox.auth import MultiKeyAuthRegistry
from beigebox.web_auth import WebAuthManager, COOKIE_SESSION, COOKIE_STATE
from beigebox.mcp_server import McpServer
from beigebox.app_state import AppState
from beigebox.observability.egress import build_egress_hooks, start_egress_hooks, stop_egress_hooks


logger = logging.getLogger(__name__)

# Application state singleton lives in beigebox/state.py so router modules
# can reach it without an import cycle through main.py. Re-exported here so
# `from beigebox.main import get_state` keeps working.
from beigebox.state import get_state, maybe_state, set_state


def _setup_logging(cfg: dict):
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        from pathlib import Path
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure permissions are correct for non-root users
        try:
            log_path.parent.chmod(0o755)
            handlers.append(logging.FileHandler(log_file))
        except (OSError, PermissionError) as e:
            # If file logging fails, fall back to stderr only
            print(f"Warning: Could not set up file logging to {log_file}: {e}", flush=True)
            print("Logging to stderr only", flush=True)

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
    from beigebox.config import get_primary_backend_url
    embed_cfg = cfg.get("embedding", {})
    model = embed_cfg.get("model", "")
    url = embed_cfg.get("backend_url") or get_primary_backend_url(cfg)
    if not model:
        return
    _log = logging.getLogger(__name__)
    # Embedding-only models (e.g. nomic-embed-text) reject /api/generate with 400.
    # Use /api/embed to warm them up instead.
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{url}/api/embed",
                    json={"model": model, "input": "warmup", "keep_alive": -1},
                )
                resp.raise_for_status()
            _log.info("Embedding model '%s' preloaded and pinned", model)
            return
        except Exception as e:
            delay = 5.0 * (2 ** attempt)
            if attempt < 4:
                _log.warning(
                    "Embedding preload attempt %d/5 failed (%s) — retrying in %.0fs",
                    attempt + 1, e, delay,
                )
                await asyncio.sleep(delay)
            else:
                _log.warning("Embedding preload failed after 5 attempts: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    cfg = get_config()
    _setup_logging(cfg)
    logger = logging.getLogger(__name__)

    # Configure payload log path once at startup
    from beigebox.payload_log import configure as _pl_configure
    _pl_configure(cfg.get("payload_log", {}).get("path", "./data/payload.jsonl"))

    # Storage
    sqlite_path, vector_store_path = get_storage_paths(cfg)
    _integrity_cfg = cfg.get("security", {}).get("memory_integrity", {})

    # BaseDB shim — shared by every per-entity repo (api_keys, conversations,
    # quarantine, users, wire_events). The SQLiteStore god-object is gone in
    # batch B; ConversationRepo owns the conversations + messages tables and
    # all integrity-validation state previously held inside SQLiteStore.
    from beigebox.storage.db import make_db, build_db_kwargs
    from beigebox.storage.repos import (
        make_api_key_repo,
        make_conversation_repo,
        make_quarantine_repo,
        make_user_repo,
        make_wire_event_repo,
    )
    _db_type, _db_kwargs = build_db_kwargs(cfg, default_sqlite_path=sqlite_path)
    db = make_db(_db_type, **_db_kwargs)
    api_keys = make_api_key_repo(db)
    api_keys.create_tables()
    conversations = make_conversation_repo(db, integrity_config=_integrity_cfg)
    conversations.create_tables()
    quarantine = make_quarantine_repo(db)
    quarantine.create_tables()
    users = make_user_repo(db)
    users.create_tables()
    wire_events = make_wire_event_repo(db)
    wire_events.create_tables()

    _embed_cfg    = cfg["embedding"]
    from beigebox.storage.backends import (
        make_backend as _make_backend,
        build_backend_kwargs as _build_backend_kwargs,
    )
    from beigebox.storage.blob_store import BlobStore
    from beigebox.config import get_primary_backend_url

    _backend_type, _backend_kwargs = _build_backend_kwargs(cfg, vector_store_path)

    # RAG poisoning detection initialization
    poisoning_detector = None
    _poisoning_cfg = cfg.get("embedding_poisoning_detection", {})
    if _poisoning_cfg.get("enabled", True):
        poisoning_detector = RAGPoisoningDetector(
            sensitivity=_poisoning_cfg.get("sensitivity", 0.95),
            baseline_window=_poisoning_cfg.get("baseline_window", 1000),
            min_norm=_poisoning_cfg.get("min_norm", 0.1),
            max_norm=_poisoning_cfg.get("max_norm", 100.0),
        )
        logger.info("RAG poisoning detection: ENABLED (sensitivity=%.2f)",
                   _poisoning_cfg.get("sensitivity", 0.95))
    else:
        logger.warning("RAG poisoning detection: DISABLED")

    vector_store = VectorStore(
        embedding_model=_embed_cfg["model"],
        embedding_url=_embed_cfg.get("backend_url") or get_primary_backend_url(cfg),
        backend=_make_backend(_backend_type, **_backend_kwargs),
        poisoning_detector=poisoning_detector,
        quarantine=quarantine,
    )
    blob_store = BlobStore(Path(vector_store_path) / "blobs")

    # Tools (pass vector_store for the memory tool)
    tool_registry = ToolRegistry(vector_store=vector_store)

    # Hooks
    hooks_cfg = cfg.get("hooks", {})
    _hooks_enabled = hooks_cfg.get("enabled", True) if isinstance(hooks_cfg, dict) else True
    _hook_list = hooks_cfg.get("hooks", []) if isinstance(hooks_cfg, dict) else []
    hook_manager = HookManager(
        hooks_dir=hooks_cfg.get("directory", "./hooks") if _hooks_enabled else None,
        hook_configs=_hook_list if isinstance(_hook_list, list) else [],
    )

    # Multi-backend router — reads effective config (runtime_config.yaml overrides config.yaml)
    backend_router = None
    backends_enabled, backends_cfg = get_effective_backends_config()
    if backends_enabled:
        if backends_cfg:
            model_routes = cfg.get("routing", {}).get("model_routes", [])
            backend_router = MultiBackendRouter(backends_cfg, model_routes=model_routes)
            logger.info("Multi-backend router: enabled (%d backends)", len(backend_router.backends))
        else:
            logger.warning("backends_enabled=true but no backends configured")
    else:
        logger.info("Multi-backend router: disabled")

    # Cost tracker (v0.6)
    cost_tracker = None
    if cfg.get("cost_tracking", {}).get("enabled", False):
        cost_tracker = CostTracker(db)
        logger.info("Cost tracking: enabled")
    else:
        logger.info("Cost tracking: disabled")

    # Auth registry (multi-key, agentauth-backed)
    auth_registry = MultiKeyAuthRegistry(cfg.get("auth", {}))

    # Web UI OAuth shim (optional — requires itsdangerous)
    web_auth = WebAuthManager(cfg.get("auth", {}).get("web_ui", {})) if WebAuthManager else None

    # Simple password auth (optional — for single-tenant SaaS)
    password_auth = None
    if cfg.get("auth", {}).get("mode") == "password":
        from beigebox.web_auth import SimplePasswordAuth
        password_auth = SimplePasswordAuth(users) if users else None

    # Load skills for MCP resources/list + resources/read
    from beigebox.skill_loader import load_skills as _load_skills
    _skills_path = cfg.get("skills", {}).get("path") or str(
        Path(__file__).parent / "skills"
    )
    _mcp_skills = _load_skills(_skills_path)

    mcp_server = McpServer(tool_registry, operator_factory=None, skills=_mcp_skills)
    logger.info("MCP server: enabled (POST /mcp)")

    # Pen/Sec MCP — separate endpoint exposing offensive-security tool wrappers
    # (nmap, nuclei, sqlmap, ffuf, …). Disabled by default; enable in config.yaml:
    #   security_mcp:
    #     enabled: true
    security_mcp_server = None
    _sec_mcp_cfg = cfg.get("security_mcp", {})
    if _sec_mcp_cfg.get("enabled"):
        from beigebox.security_mcp import build_default_registry as _build_sec_registry
        _sec_registry = _build_sec_registry()
        # Empty set => expose every registered tool (no progressive disclosure).
        # Right call here: small, focused surface — list them all up front.
        # server_label="pen-mcp" tags every tool_call wire event so /mcp vs
        # /pen-mcp are distinguishable in the Tap event log.
        security_mcp_server = McpServer(_sec_registry, resident_tools=set(),
                                        server_label="pen-mcp")
        logger.info(
            "Pen/Sec MCP server: enabled (POST /pen-mcp) — %d wrappers loaded",
            len(_sec_registry.list_tools()),
        )
    else:
        logger.info("Pen/Sec MCP server: disabled (set security_mcp.enabled: true to enable)")

    # Model Extraction Attack Detection (OWASP LLM10:2025)
    from beigebox.security.extraction_detector import ExtractionDetector
    extraction_cfg = cfg.get("security", {}).get("extraction_detection", {})
    extraction_detector = None
    if extraction_cfg.get("enabled", True):
        extraction_detector = ExtractionDetector(
            diversity_threshold=extraction_cfg.get("diversity_threshold", 2.5),
            instruction_frequency_threshold=extraction_cfg.get("instruction_frequency_threshold", 10),
            token_variance_threshold=extraction_cfg.get("token_variance_threshold", 0.01),
            inversion_attempt_threshold=extraction_cfg.get("inversion_attempt_threshold", 3),
            baseline_window=extraction_cfg.get("baseline_window", 20),
            analysis_window=extraction_cfg.get("analysis_window", 100),
        )
        logger.info("Model extraction detection: ENABLED")
    else:
        logger.info("Model extraction detection: disabled")

    # Security Audit & Detection Modules (P1 Security Hardening)
    from beigebox.security.enhanced_injection_guard import EnhancedInjectionGuard
    from beigebox.security.rag_content_scanner import RAGContentScanner

    sec_cfg = cfg.get("security", {})

    # AuditLogger + HoneypotManager are not currently wired (left unset to
    # be revived by a dedicated commit if/when their DB-write issues are
    # resolved). AppState carries them as None so downstream consumers
    # remain quiet rather than missing-attribute-error.
    audit_logger = None
    honeypot_manager = None

    # Enhanced Injection Guard (semantic + pattern detection)
    injection_guard = EnhancedInjectionGuard() if sec_cfg.get("injection_guard", {}).get("enabled", True) else None
    if injection_guard:
        logger.info("Enhanced Injection Guard: initialized with semantic + pattern detection")

    # RAG Content Scanner (pre-embed poisoning detection)
    rag_scanner = RAGContentScanner() if sec_cfg.get("rag_scanner", {}).get("enabled", True) else None
    if rag_scanner:
        logger.info("RAG Content Scanner: initialized for pre-embed detection")

    # Observability egress hooks (webhook batching, fire-and-forget)
    egress_hooks = build_egress_hooks(cfg)
    await start_egress_hooks(egress_hooks)
    if egress_hooks:
        logger.info("Observability egress: %d hook(s) active", len(egress_hooks))
    else:
        logger.debug("Observability egress: no webhooks configured")

    # Proxy (with hooks, tools, router, and extraction detector)
    proxy = Proxy(
        conversations=conversations,
        vector=vector_store,
        hook_manager=hook_manager,
        tool_registry=tool_registry,
        backend_router=backend_router,
        blob_store=blob_store,
        egress_hooks=egress_hooks,
        extraction_detector=extraction_detector,
        wire_events=wire_events,
    )

    # CaptureFanout — single chokepoint for request/response telemetry.
    # Wires conversations (the new ConversationRepo on BaseDB), the WireLog
    # the proxy constructed inside __init__, and the vector store. Must be
    # assigned AFTER Proxy() since it references proxy.wire.
    from beigebox.capture import CaptureFanout
    proxy.capture = CaptureFanout(
        conversations=conversations,
        wire=proxy.wire,
        vector=vector_store,
    )
    logger.info("CaptureFanout wired: messages → sqlite + wire_events + vector")

    # PostgresWireSink — third redundant sink alongside JSONL + SQLite.
    # The user wants "capture everything" with redundancy across postgres,
    # jsonl, sql; postgres replaced chroma as the primary structured-query
    # store. Failure here is non-fatal: lifespan continues with two sinks
    # if postgres is unavailable. Errors per-write are already isolated
    # by WireLog (commit 7aba40c).
    _pg_conn = (cfg.get("storage", {})
                  .get("postgres", {})
                  .get("connection_string"))
    if _pg_conn:
        try:
            from beigebox.storage.db import make_db
            from beigebox.storage.wire_sink import make_sink
            _pg_db = make_db("postgres", connection_string=_pg_conn)
            _pg_wire_events = make_wire_event_repo(_pg_db)
            _pg_wire_events.create_tables()
            proxy.wire.add_sink(make_sink("postgres", repo=_pg_wire_events))
            logger.info(
                "PostgresWireSink attached: wire_events fan-out → jsonl + sqlite + postgres",
            )
        except Exception as exc:
            logger.warning(
                "PostgresWireSink unavailable, continuing with jsonl+sqlite: %s",
                exc,
            )

    # Bind the production WireLog to the typed-event dispatch so the
    # logging.py helpers can reach a real sink.
    from beigebox.log_events import set_wire_log
    set_wire_log(proxy.wire)

    # Late-bind the anomaly detector to the tool (proxy must exist first)
    _aad_tool = tool_registry.get("api_anomaly_detector")
    if _aad_tool and proxy.anomaly_detector:
        _aad_tool.set_detector(proxy.anomaly_detector)
        logger.info("APIAnomalyDetectorTool bound to proxy anomaly detector")

    set_state(AppState(
        proxy=proxy,
        tool_registry=tool_registry,
        db=db,
        api_keys=api_keys,
        conversations=conversations,
        quarantine=quarantine,
        users=users,
        wire_events=wire_events,
        vector_store=vector_store,
        blob_store=blob_store,
        hook_manager=hook_manager,
        backend_router=backend_router,
        cost_tracker=cost_tracker,
        auth_registry=auth_registry,
        web_auth=web_auth,
        password_auth=password_auth,
        mcp_server=mcp_server,
        security_mcp_server=security_mcp_server,
        poisoning_detector=poisoning_detector,
        extraction_detector=extraction_detector,
        audit_logger=audit_logger,
        honeypot_manager=honeypot_manager,
        injection_guard=injection_guard,
        rag_scanner=rag_scanner,
        egress_hooks=egress_hooks,
    ))

    from beigebox.config import get_primary_backend_url
    logger.info(
        "BeigeBox started — listening on %s:%s, backend %s",
        cfg["server"]["host"],
        cfg["server"]["port"],
        get_primary_backend_url(cfg),
    )
    logger.info("Storage: SQLite=%s, Vector=%s", sqlite_path, vector_store_path)
    logger.info("Tools: %s", tool_registry.list_tools())
    logger.info("Hooks: %s", hook_manager.list_hooks())

    # Preload models — run concurrently in the background so startup is not blocked.
    # Both use retry-with-backoff; Ollama may still be loading models from disk.
    _preload_tasks = [asyncio.create_task(_preload_embedding_model(cfg))]

    # Collect all distinct special-purpose models (judge, operator, summary)
    # and pin them in Ollama at startup so cold-start latency never hits a
    # live request. Stagger by 15s each to avoid VRAM bandwidth contention.
    from beigebox.config import get_primary_backend_url
    _backend_url = get_primary_backend_url(cfg)
    _models_cfg = cfg.get("models", {})
    _profiles = _models_cfg.get("profiles", {})
    _default_model = _models_cfg.get("default", "")
    _special_models: list[tuple[str, str]] = []  # (model, label)
    _seen: set[str] = set()
    for label, key in [("routing/judge", "routing"), ("operator/agentic", "agentic"), ("summary", "summary")]:
        m = _profiles.get(key) or _default_model
        if m and m not in _seen:
            _seen.add(m)
            _special_models.append((m, label))

    async def _staggered_preloads():
        # Give Ollama 15s head-start before the first special model, then
        # stagger each additional distinct model by 15s so they don't race.
        await asyncio.sleep(15)
        for idx, (model, label) in enumerate(_special_models):
            if idx > 0:
                await asyncio.sleep(15)
            await _preload_model(_backend_url, model, label)

    _preload_tasks.append(asyncio.create_task(_staggered_preloads()))
    # Fire-and-forget: server starts accepting requests immediately while
    # models warm up. Tasks are not awaited here.

    yield

    logger.info("BeigeBox shutting down")
    _final_state = maybe_state()
    if _final_state and _final_state.egress_hooks:
        await stop_egress_hooks(_final_state.egress_hooks)
    if _final_state and _final_state.proxy and _final_state.proxy.wire:
        _final_state.proxy.wire.close()
    from beigebox.payload_log import close as _pl_close
    _pl_close()
    logger.info("Wiretap and payload log flushed and closed")


# ---------------------------------------------------------------------------
# Auth middleware — single API key, disabled when key is empty
# ---------------------------------------------------------------------------

# Paths that never require API-key auth (web UI, health checks, OAuth flow)
_AUTH_EXEMPT = frozenset(["/", "/ui", "/beigebox/health", "/api/v1/status"])
_AUTH_EXEMPT_PREFIXES = ("/web/", "/auth/")


def _emit_auth_denied(reason_code: str, principal_name: str, principal_type: str,
                      endpoint_path: str, request: Request | None = None) -> None:
    """Emit an `auth_denied` wire event before a 401/403/429 return.

    Per the observability rubric: auth denials must never be silent — they're
    load-bearing for breach forensics and rate-limit tuning. ``request``, when
    provided, is mined for ``client_ip`` and ``user_agent`` so the event has
    enough context for an analyst to triage without a separate lookup.

    Best-effort: failure to emit MUST NOT block the deny response. We log
    (not silently swallow) the failure so a broken wire dispatcher surfaces
    in the stdlib log instead of disappearing.
    """
    if not (maybe_state() and maybe_state().proxy and maybe_state().proxy.wire):
        return
    meta: dict = {
        "reason_code": reason_code,
        "principal_name": principal_name,
        "principal_type": principal_type,
        "endpoint": endpoint_path,
    }
    if request is not None:
        try:
            meta["client_ip"] = request.client.host if request.client else None
            meta["user_agent"] = request.headers.get("user-agent")
        except Exception:  # request introspection — keep meta partial on failure
            pass
    try:
        maybe_state().proxy.wire.log(
            direction="inbound",
            role="auth",
            content=f"deny {reason_code}: {principal_name or '?'} → {endpoint_path}",
            event_type="auth_denied",
            source="auth_middleware",
            meta=meta,
        )
    except Exception:
        logger.warning("auth_denied wire emit failed", exc_info=True)


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
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if maybe_state() is None or maybe_state().auth_registry is None or not maybe_state().auth_registry.is_enabled():
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)

        # Extract token. Querystring (?api_key=...) is intentionally NOT accepted
        # — it leaks via access logs, browser history, referrers, and proxy logs.
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        else:
            token = request.headers.get("api-key", "")

        meta = maybe_state().auth_registry.validate(token)

        # If static key validation fails, try dynamic API keys from database
        if meta is None and maybe_state().api_keys and token:
            user_id = maybe_state().api_keys.verify(token)
            if user_id:
                # Create a pseudo-KeyMeta for dynamic keys
                # Apply default rate limit from config (not unlimited)
                from beigebox.auth import KeyMeta
                cfg = get_config()
                default_rate_limit = cfg.get("auth", {}).get("dynamic_key_rate_limit_rpm", 100)
                meta = KeyMeta(
                    name=f"user:{user_id[:8]}",
                    allowed_models=["*"],
                    allowed_endpoints=["*"],
                    rate_limit_rpm=default_rate_limit
                )

        if meta is None:
            _emit_auth_denied("invalid_api_key", "unknown", "api_key", path, request)
            # Don't leak auth methods to unauthenticated users
            return JSONResponse(
                {
                    "error": {
                        "message": "Invalid or missing API key.",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
                status_code=401,
            )

        # Rate limit
        if not maybe_state().auth_registry.check_rate_limit(meta):
            _emit_auth_denied("rate_limit_exceeded", meta.name, "api_key", path, request)
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
        if not maybe_state().auth_registry.check_endpoint(meta, path):
            _emit_auth_denied("endpoint_not_allowed", meta.name, "api_key", path, request)
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
# Web UI auth middleware — session cookie gate for browser paths
# ---------------------------------------------------------------------------

# Paths that WebAuthMiddleware protects when oauth mode is active
_WEB_UI_PATHS    = frozenset(["/", "/ui"])
_WEB_UI_PREFIXES = ("/web/",)


class WebAuthMiddleware(BaseHTTPMiddleware):
    """
    Gates web UI paths behind a signed session cookie when oauth or password auth is enabled.

    API paths (/v1/, /api/) are not touched — those use Bearer token auth.
    The OAuth flow paths (/auth/*) are always exempt.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if maybe_state() is None:
            return await call_next(request)

        # Check if either OAuth or password auth is enabled
        oauth_enabled = maybe_state().web_auth is not None and maybe_state().web_auth.is_enabled()
        password_auth_enabled = maybe_state().password_auth is not None

        if not (oauth_enabled or password_auth_enabled):
            return await call_next(request)

        path = request.url.path

        # Auth flow is always exempt
        if path.startswith("/auth/"):
            return await call_next(request)

        # Only gate web UI paths
        is_web = path in _WEB_UI_PATHS or any(path.startswith(p) for p in _WEB_UI_PREFIXES)
        if not is_web:
            return await call_next(request)

        # Validate session cookie (check both OAuth and password auth)
        token = request.cookies.get(COOKIE_SESSION, "")
        user = None

        if oauth_enabled and maybe_state().web_auth:
            user = maybe_state().web_auth.verify_session(token) if token else None

        if user is None and password_auth_enabled and maybe_state().password_auth:
            user = maybe_state().password_auth.verify_session(token) if token else None

        if user is None:
            from starlette.responses import RedirectResponse
            # Redirect to password auth login if enabled, otherwise OAuth
            if password_auth_enabled:
                return RedirectResponse(url="/auth/login", status_code=302)
            else:
                providers = maybe_state().web_auth.list_providers()
                login_path = f"/auth/{providers[0]}/login" if providers else "/"
                return RedirectResponse(url=login_path, status_code=302)

        request.state.web_user = user
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
app.add_middleware(WebAuthMiddleware)


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
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


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
from beigebox.routers.workspace import (  # noqa: E402,F401
    router as workspace_router,
    # Re-exports so existing test imports (`from beigebox.main import
    # api_workspace`, etc.) keep working without churning ~30 test sites.
    api_workspace,
    api_workspace_delete,
    api_workspace_upload,
)
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
