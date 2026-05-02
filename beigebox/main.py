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
import secrets
import time
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from beigebox import __version__ as _BB_VERSION
from beigebox.constants import DEFAULT_MODEL, DEFAULT_ROUTING_MODEL, DEFAULT_AGENTIC_MODEL, DEFAULT_SUMMARY_MODEL, DEFAULT_EMBEDDING_MODEL
from beigebox.config import (
    get_config,
    get_runtime_config,
    update_runtime_config,
    get_effective_backends_config,
    get_storage_paths,
    get_primary_backend_url,
)
from beigebox.proxy import Proxy
from beigebox.storage.vector_store import VectorStore
from beigebox.tools.registry import ToolRegistry
from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
from beigebox.hooks import HookManager
from beigebox.backends.router import MultiBackendRouter
from beigebox.costs import CostTracker
from beigebox.auth import MultiKeyAuthRegistry
try:
    from beigebox.web_auth import WebAuthManager, COOKIE_SESSION, COOKIE_STATE
except ImportError:
    WebAuthManager = None  # type: ignore[assignment,misc]
    COOKIE_SESSION = "bb_session"
    COOKIE_STATE   = "bb_oauth_state"

# _COOKIE_VERIFIER moved to routers/auth.py (B-3) — only the OAuth flow uses it.
from beigebox.mcp_server import McpServer
from beigebox.app_state import AppState
from beigebox.observability.egress import build_egress_hooks, start_egress_hooks, stop_egress_hooks
from beigebox.metrics import collect_system_metrics_async


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application state — initialized during lifespan startup
# ---------------------------------------------------------------------------
# The state singleton lives in beigebox/state.py so router modules can
# reach it without an import cycle through main.py. Re-exported here so
# existing callers of ``from beigebox.main import get_state`` keep
# working. In-file middleware that referenced the legacy module-level
# ``_app_state`` global has been migrated to ``maybe_state()``.
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

    # MCP server — expose operator/run if operator is enabled
    # Operator-via-MCP factory removed in v3 — Operator class deleted.
    _op_mcp_factory = None

    # Load skills for MCP resources/list + resources/read
    from beigebox.skill_loader import load_skills as _load_skills
    _skills_path = cfg.get("skills", {}).get("path") or str(
        Path(__file__).parent / "skills"
    )
    _mcp_skills = _load_skills(_skills_path)

    mcp_server = McpServer(tool_registry, operator_factory=_op_mcp_factory, skills=_mcp_skills)
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
    from beigebox.security.audit_logger import AuditLogger
    from beigebox.security.honeypots import HoneypotManager
    from beigebox.security.enhanced_injection_guard import EnhancedInjectionGuard
    from beigebox.security.rag_content_scanner import RAGContentScanner

    sec_cfg = cfg.get("security", {})

    # Audit Logger (SQLite-backed, queryable) — disabled for now due to DB write issues
    audit_logger_path = sec_cfg.get("audit_db_path", "~/.beigebox/audit.db")
    audit_logger = None  # Temporarily disabled
    if audit_logger:
        logger.info("Audit Logger: initialized at %s", audit_logger_path)

    # Honeypot Manager (8 bypass canaries) — disabled for now
    honeypot_manager = None  # Temporarily disabled
    if honeypot_manager:
        logger.info("Honeypot Manager: 8 traps active, logging to audit_logger")

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
    # logging.py helpers (once converted in A-4) can reach a real sink.
    # Until A-4 lands, the helpers still use the legacy _get_tap_logger
    # path; this set_wire_log call is wired in advance so the swap is
    # a one-import change in A-4.
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


# _require_admin moved to routers/_shared.py (B-3) — used by auth, config,
# and toolbox routers. Imported here for the in-file admin-gated endpoints.
from beigebox.routers._shared import _require_admin  # noqa: E402


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
from beigebox.routers.workspace import (  # noqa: E402
    router as workspace_router,
    # Re-exports so existing test imports (`from beigebox.main import
    # api_workspace`, etc.) keep working without churning ~30 test sites.
    api_workspace,
    api_workspace_mounts_add,
    api_workspace_mounts_delete,
    api_workspace_delete,
    api_workspace_upload,
    api_transform_pdf,
    api_conversation_replay,
    api_conversation_fork,
    toggle_vi_mode,
)
from beigebox.routers.analytics import router as analytics_router  # noqa: E402

app.include_router(openai_router)
app.include_router(auth_router)
app.include_router(security_router)
app.include_router(workspace_router)
app.include_router(analytics_router)


# OpenAI-compat + Ollama passthrough endpoints live in routers/openai.py
# (extracted in commit B-2). Registered above via app.include_router().
# /api/v1/route-check removed in v3 — the agentic routing layer it exercised
# (z-commands, hybrid routing, decision LLM, embedding classifier) was deleted.


# ---------------------------------------------------------------------------
# BeigeBox-specific endpoints
# ---------------------------------------------------------------------------

# /beigebox/stats, /metrics/quarantine, /api/v1/security/*, /beigebox/search,
# /api/v1/search, /beigebox/health all moved to routers/security.py (B-4).


_MCP_REQUEST_BODY_LIMIT = 1_048_576  # 1 MiB — caps raw bytes BEFORE json.loads


async def _read_mcp_body(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Size-cap and parse a JSON-RPC request body.

    Returns (parsed_body, None) on success, (None, error_response) otherwise.
    The cap is enforced on raw bytes before json.loads so a deeply nested or
    oversized payload can't exhaust CPU/memory in the parser.
    """
    too_large = JSONResponse(
        {"jsonrpc": "2.0", "id": None,
         "error": {"code": -32600, "message": "Request too large"}},
        status_code=413,
    )
    # Cheap early reject if the client volunteered a Content-Length.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MCP_REQUEST_BODY_LIMIT:
                return None, too_large
        except ValueError:
            pass  # malformed header — fall through to streaming check
    # Streaming read with early abort. Bounds memory at the cap even if the
    # client lied about Content-Length or omitted it under chunked transfer.
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _MCP_REQUEST_BODY_LIMIT:
            return None, too_large
    try:
        return json.loads(bytes(raw)), None
    except Exception as e:
        logger.debug("MCP parse error: %s", str(e)[:200])
        return None, JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )


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
    body, err = await _read_mcp_body(request)
    if err is not None:
        return err
    result = await _st.mcp_server.handle(body)
    if result is None:
        # Notification — no response body
        from starlette.responses import Response as _Response
        return _Response(status_code=202)
    return JSONResponse(result)


@app.post("/pen-mcp")
async def pen_mcp_endpoint(request: Request):
    """
    Pen/Sec MCP server — separate JSON-RPC endpoint exposing offensive-security
    tool wrappers (nmap, nuclei, sqlmap, ffuf, …). Same protocol as /mcp; just
    a different registry so security tooling stays out of the default surface.

    Enable via config.yaml: ``security_mcp.enabled: true``.
    Auth: same ApiKeyMiddleware as /mcp; add /pen-mcp to a key's allowed_endpoints.
    """
    _st = get_state()
    if _st.security_mcp_server is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32603,
                       "message": "Pen/Sec MCP server disabled (set security_mcp.enabled in config)"}},
            status_code=503,
        )
    body, err = await _read_mcp_body(request)
    if err is not None:
        return err
    result = await _st.security_mcp_server.handle(body)
    if result is None:
        from starlette.responses import Response as _Response
        return _Response(status_code=202)
    return JSONResponse(result)


# /api/v1/zcommands removed in v3 — z-command parsing was deleted.


# /api/v1/search moved to routers/security.py (B-4).


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


# /beigebox/health moved to routers/security.py (B-4).




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
            "url": get_primary_backend_url(cfg),
            "default_model": get_runtime_config().get("default_model") or cfg.get("models", {}).get("default", ""),
        },
        "features": {
            "storage": _st.conversations is not None and _st.vector_store is not None,
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
            "harness":               rt.get("features_harness", cfg.get("features", {}).get("harness", cfg.get("harness", {}).get("enabled", True))),
            "tools":                 rt.get("features_tools", cfg.get("features", {}).get("tools", cfg.get("tools", {}).get("enabled", False))),
            "cost_tracking":         rt.get("features_cost_tracking", cfg.get("features", {}).get("cost_tracking", cfg.get("cost_tracking", {}).get("enabled", False))),
            "conversation_replay":   rt.get("features_conversation_replay", cfg.get("features", {}).get("conversation_replay", cfg.get("conversation_replay", {}).get("enabled", False))),
            "auto_summarization":    rt.get("features_auto_summarization", cfg.get("features", {}).get("auto_summarization", cfg.get("auto_summarization", {}).get("enabled", False))),
            "aggressive_summarization": rt.get("features_aggressive_summarization", cfg.get("features", {}).get("aggressive_summarization", cfg.get("aggressive_summarization", {}).get("enabled", False))),
            "system_context":        rt.get("features_system_context", cfg.get("features", {}).get("system_context", cfg.get("system_context", {}).get("enabled", False))),
            "wiretap":               rt.get("features_wiretap", cfg.get("features", {}).get("wiretap", cfg.get("wiretap", {}).get("enabled", True))),
            "payload_log":           rt.get("payload_log_enabled", False),
            "wasm":                  rt.get("features_wasm", cfg.get("features", {}).get("wasm", cfg.get("wasm", {}).get("enabled", False))),
            "guardrails":            rt.get("features_guardrails", cfg.get("features", {}).get("guardrails", cfg.get("guardrails", {}).get("enabled", False))),
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
            "default":       rt.get("models_default") or cfg.get("models", {}).get("default", DEFAULT_MODEL),
            "routing":       rt.get("models_routing") or cfg.get("models", {}).get("profiles", {}).get("routing", DEFAULT_ROUTING_MODEL),
            "agentic":       rt.get("models_agentic") or cfg.get("models", {}).get("profiles", {}).get("agentic", DEFAULT_AGENTIC_MODEL),
            "summary":       rt.get("models_summary") or cfg.get("models", {}).get("profiles", {}).get("summary", DEFAULT_SUMMARY_MODEL),
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
        # (Operator config block removed in v3 — Operator class deleted.)
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
        "aggressive_summarization": {
            **cfg.get("aggressive_summarization", {}),
            "enabled":   rt.get("agg_sum_enabled",   cfg.get("aggressive_summarization", {}).get("enabled", False)),
            "keep_last": rt.get("agg_sum_keep_last",  cfg.get("aggressive_summarization", {}).get("keep_last", 2)),
            "model":     rt.get("agg_sum_model",      cfg.get("aggressive_summarization", {}).get("model", "")),
        },
        # ── Routing — Tier Pipeline (Phase 3 refactoring) ────────────────
        "routing": {
            # Tier 1: Session cache — rt.get() allows runtime override via POST /api/v1/config
            "session_cache": {
                "ttl_seconds": rt.get("tier1_ttl", cfg.get("routing", {}).get("session_cache", {}).get("ttl_seconds", 3600)),
            },
            # Tier 2: Embedding classifier
            "classifier": {
                "enabled": rt.get("features_classifier", cfg.get("features", {}).get("classifier", cfg.get("classifier", {}).get("enabled", True))),
                "centroid_rebuild_interval": rt.get("classifier_rebuild_interval", cfg.get("routing", {}).get("classifier", {}).get("centroid_rebuild_interval", cfg.get("classifier", {}).get("centroid_rebuild_interval", 3600))),
            },
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

    ── Adding a new field (checklist to prevent regressions) ──────────────────
    When adding a new config field that should be editable from the UI, you
    must update THREE places or the UI will silently fail / show stale values:

    1. POST allowed dict below  →  add  "your_key": "your_key"
    2. GET /api/v1/config above →  add  rt.get("your_key", cfg.get(..., default))
                                   in the appropriate section of the response
    3. index.html saveConfig()  →  add "your_key" to bools/strings/numbers list
                                   AND read it from the correct c.section.field path
                                   in loadConfig()

    Skipping any step causes the "saved but UI still shows old value" regression.
    The GET path and POST key must reference the same runtime_config.yaml key.
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.debug("Invalid JSON body: %s", str(e)[:200])
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    updated = []
    changed = []   # subset of updated where value actually differed from current runtime
    errors = []

    rt_before = get_runtime_config()  # snapshot before writes — used to compute "changed"

    # All runtime-adjustable keys
    allowed = {
        # Web UI
        "web_ui_vi_mode":               "web_ui_vi_mode",
        "web_ui_palette":               "web_ui_palette",
        # Features (Phase 1 refactoring)
        "features_backends":            "features_backends",
        "features_harness":             "features_harness",
        "features_tools":               "features_tools",
        "features_cost_tracking":       "features_cost_tracking",
        "features_conversation_replay": "features_conversation_replay",
        "features_auto_summarization":  "features_auto_summarization",
        "features_system_context":      "features_system_context",
        "features_wiretap":             "features_wiretap",
        # "features_payload_log" removed — was a no-op; use payload_log_enabled instead
        "features_wasm":                "features_wasm",
        "features_guardrails":          "features_guardrails",
        "features_hooks":               "features_hooks",
        # Models Registry (Phase 2 refactoring)
        "models_default":               "models_default",
        "models_routing":               "models_routing",
        "models_agentic":               "models_agentic",
        "models_summary":               "models_summary",
        # Default model (no longer routed by tiers — backend.router still picks
        # which provider serves a named model)
        "default_model":                "default_model",
        # Features (keep old keys for backwards compat)
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
        # Aggressive summarization
        "features_aggressive_summarization": "features_aggressive_summarization",
        "agg_sum_enabled":              "agg_sum_enabled",
        "agg_sum_keep_last":            "agg_sum_keep_last",
        "agg_sum_model":                "agg_sum_model",
        # System context
        "system_context_enabled":       "system_context_enabled",
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
        # WASM
        "wasm_default_module":          "wasm_default_module",
        "wasm_enabled":                 "wasm_enabled",
        "wasm_timeout_ms":              "wasm_timeout_ms",
        # Backend selection — affects how backends.router picks a backend for a model
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
            new_val = body[key]
            ok = update_runtime_config(rt_key, new_val)
            if ok:
                updated.append(key)
                if new_val != rt_before.get(rt_key):
                    changed.append(key)
            else:
                errors.append(key)

    # Apply live changes that don't need restart
    rt = get_runtime_config()
    _st = get_state()

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
        return JSONResponse({"saved": updated, "changed": changed, "errors": errors}, status_code=207)
    return JSONResponse({"saved": updated, "changed": changed, "ok": True})


@app.post("/api/v1/wasm/reload")
async def api_wasm_reload(request: Request):
    """
    Reload WASM modules from disk without restarting BeigeBox.
    Re-reads config.yaml for updated paths and enabled flags.
    Returns the list of successfully loaded module names.

    Admin-only: a malicious caller with file-write access could otherwise
    swap a module file then trigger reload to execute it.
    """
    if (denied := _require_admin(request)) is not None:
        return denied
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
    except Exception as e:
        logger.debug("Invalid JSON body: %s", str(e)[:200])
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


# ─────────────────────────────────────────────────────────────────────────────
# Toolbox: MCP / Tools / Skills inspection + editing
# ─────────────────────────────────────────────────────────────────────────────

_TOOLBOX_PROTECTED_TOOLS = {"registry", "plugin_loader", "__init__"}


def _toolbox_edits_enabled() -> bool:
    return bool(get_config().get("toolbox", {}).get("edits_enabled", False))


def _toolbox_tools_dir() -> Path:
    return (Path(__file__).parent / "tools").resolve()


def _toolbox_plugins_dir() -> Path:
    cfg_path = get_config().get("tools", {}).get("plugins", {}).get("path") or "./plugins"
    p = Path(cfg_path)
    if p.is_absolute():
        return p.resolve()
    return (Path(__file__).parent.parent / p).resolve()


def _toolbox_skills_dir() -> Path:
    override = get_config().get("skills", {}).get("path")
    if override:
        return Path(override).resolve()
    return (Path(__file__).parent / "skills").resolve()


def _is_valid_tool_name(name: str) -> bool:
    """Lowercase letter-first, alnum + underscore, ≤40 chars."""
    if not name or len(name) > 40:
        return False
    if not (name[0].isalpha() and name[0].islower()):
        return False
    return all(c.islower() or c.isdigit() or c == "_" for c in name)


def _is_valid_skill_name(name: str) -> bool:
    """Lowercase letter-first, alnum + underscore + hyphen, ≤40 chars."""
    if not name or len(name) > 40:
        return False
    if not (name[0].isalpha() and name[0].islower()):
        return False
    return all(c.islower() or c.isdigit() or c in "_-" for c in name)


def _resolve_tool_path(name: str) -> Path | None:
    """Resolve a tool source file path; returns None on invalid/protected/escape.

    Checks the plugins dir first (user-added tools land there), then the
    built-in tools dir.
    """
    if not name or name.startswith("_") or name in _TOOLBOX_PROTECTED_TOOLS:
        return None
    if not all(c.isalnum() or c == "_" for c in name):
        return None
    plugins_dir = _toolbox_plugins_dir()
    plugin_target = (plugins_dir / f"{name}.py").resolve()
    try:
        plugin_target.relative_to(plugins_dir)
        if plugin_target.exists():
            return plugin_target
    except ValueError:
        pass
    tools_dir = _toolbox_tools_dir()
    target = (tools_dir / f"{name}.py").resolve()
    try:
        target.relative_to(tools_dir)
    except ValueError:
        return None
    return target


def _resolve_skill_path(raw_path: str) -> Path | None:
    """Resolve a SKILL.md path; ensures it stays under the skills dir and is .md."""
    if not raw_path:
        return None
    skills_dir = _toolbox_skills_dir()
    try:
        target = Path(raw_path).resolve()
        target.relative_to(skills_dir)
    except (ValueError, OSError):
        return None
    if target.suffix.lower() != ".md":
        return None
    return target


@app.get("/api/v1/mcp/info")
async def api_mcp_info():
    """MCP server info: endpoint, resident tools, counts."""
    _st = get_state()
    try:
        from beigebox.mcp_server import _DEFAULT_RESIDENT_TOOLS
        resident = sorted(_DEFAULT_RESIDENT_TOOLS)
    except Exception:
        resident = []
    tool_names = _st.tool_registry.list_tools() if _st.tool_registry else []
    skill_count = 0
    try:
        from beigebox.skill_loader import load_skills
        skill_count = len(load_skills(_toolbox_skills_dir()))
    except Exception:
        pass
    return JSONResponse({
        "endpoint": "/mcp",
        "transport": "HTTP POST (JSON-RPC 2.0)",
        "resident_tools": resident,
        "registered_tool_count": len(tool_names),
        "skill_count": skill_count,
        "edits_enabled": _toolbox_edits_enabled(),
    })


@app.get("/api/v1/toolbox/tools")
async def api_toolbox_tools():
    """List tools with metadata for the Toolbox UI."""
    _st = get_state()
    tools_cfg = get_config().get("tools", {}) or {}
    items = []
    if _st.tool_registry:
        for name in _st.tool_registry.list_tools():
            tool = _st.tool_registry.get(name)
            description = getattr(tool, "description", "") if tool else ""
            tags = getattr(tool, "capability_tags", None) if tool else None
            risk = getattr(tool, "capability_risk", None) if tool else None
            tool_cfg = tools_cfg.get(name)
            enabled = tool_cfg.get("enabled") if isinstance(tool_cfg, dict) else None
            path = _resolve_tool_path(name)
            items.append({
                "name": name,
                "description": description or "",
                "enabled": enabled,
                "capability_tags": list(tags) if tags else [],
                "capability_risk": risk or "",
                "source_path": str(path) if path else "",
                "editable": path is not None and path.exists(),
            })
    return JSONResponse({
        "tools": items,
        "edits_enabled": _toolbox_edits_enabled(),
    })


@app.get("/api/v1/toolbox/tools/{name}/source")
async def api_toolbox_tool_source(name: str):
    path = _resolve_tool_path(name)
    if not path:
        return JSONResponse({"error": "invalid or protected tool name"}, status_code=400)
    if not path.exists():
        return JSONResponse({"error": "tool source not found"}, status_code=404)
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)
    return JSONResponse({
        "name": name,
        "path": str(path),
        "content": content,
        "length": len(content),
        "editable": _toolbox_edits_enabled(),
    })


@app.post("/api/v1/toolbox/tools/{name}/source")
async def api_toolbox_tool_save(name: str, request: Request):
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    path = _resolve_tool_path(name)
    if not path:
        return JSONResponse({"error": "invalid or protected tool name"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    try:
        compile(content, str(path), "exec")
    except SyntaxError as e:
        return JSONResponse(
            {"error": f"SyntaxError: {e.msg} at line {e.lineno}"},
            status_code=400,
        )
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "path": str(path),
        "length": len(content),
        "requires_restart": True,
    })


@app.get("/api/v1/toolbox/skills")
async def api_toolbox_skills():
    from beigebox.skill_loader import load_skills
    skills_dir = _toolbox_skills_dir()
    try:
        skills = load_skills(skills_dir)
    except Exception as e:
        logger.warning("load_skills failed: %s", e)
        skills = []
    items = [
        {
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "path": s.get("path", ""),
            "dir": s.get("dir", ""),
        }
        for s in skills
    ]
    return JSONResponse({
        "skills": items,
        "skills_dir": str(skills_dir),
        "edits_enabled": _toolbox_edits_enabled(),
    })


@app.get("/api/v1/toolbox/skills/source")
async def api_toolbox_skill_source(path: str):
    target = _resolve_skill_path(path)
    if not target:
        return JSONResponse({"error": "invalid skill path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "skill source not found"}, status_code=404)
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)
    return JSONResponse({
        "path": str(target),
        "content": content,
        "length": len(content),
        "editable": _toolbox_edits_enabled(),
    })


@app.post("/api/v1/toolbox/validate")
async def api_toolbox_validate(request: Request):
    """Dry-run syntax check for the Toolbox editor. No side effects."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    kind = body.get("kind", "")
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"valid": False, "error": "content must be a string"})
    if kind == "tool":
        try:
            compile(content, "<toolbox-validate>", "exec")
            return JSONResponse({"valid": True})
        except SyntaxError as e:
            return JSONResponse({"valid": False, "error": e.msg or "SyntaxError", "line": e.lineno})
    if kind == "skill":
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end == -1:
                return JSONResponse({
                    "valid": False,
                    "error": "unterminated frontmatter (missing closing '---')",
                    "line": 1,
                })
            import yaml
            try:
                meta = yaml.safe_load(content[3:end].strip()) or {}
            except yaml.YAMLError as e:
                line = getattr(getattr(e, "problem_mark", None), "line", None)
                first = str(e).splitlines()[0] if str(e) else "YAML error"
                return JSONResponse({
                    "valid": False,
                    "error": first,
                    "line": (line + 1) if line is not None else None,
                })
            if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
                return JSONResponse({
                    "valid": False,
                    "error": "frontmatter missing required 'name' or 'description'",
                })
        return JSONResponse({"valid": True})
    return JSONResponse({"valid": True})


@app.post("/api/v1/toolbox/skills/source")
async def api_toolbox_skill_save(request: Request):
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    target = _resolve_skill_path(body.get("path", ""))
    if not target:
        return JSONResponse({"error": "invalid skill path"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "path": str(target),
        "length": len(content),
        "requires_restart": True,
    })


@app.post("/api/v1/toolbox/tools/new")
async def api_toolbox_tool_new(request: Request):
    """Create a new plugin tool stub at plugins/<name>.py."""
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not _is_valid_tool_name(name):
        return JSONResponse(
            {"error": "name must be lowercase letters/digits/underscores, starting with a letter (≤40 chars)"},
            status_code=400,
        )
    if name in _TOOLBOX_PROTECTED_TOOLS:
        return JSONResponse({"error": "reserved name"}, status_code=400)
    _st = get_state()
    existing = set(_st.tool_registry.list_tools()) if _st.tool_registry else set()
    if name in existing:
        return JSONResponse({"error": f"tool '{name}' already registered"}, status_code=409)
    plugins_dir = _toolbox_plugins_dir()
    plugin_target = (plugins_dir / f"{name}.py").resolve()
    try:
        plugin_target.relative_to(plugins_dir)
    except ValueError:
        return JSONResponse({"error": "invalid target path"}, status_code=400)
    if plugin_target.exists():
        return JSONResponse({"error": f"file already exists: {plugin_target.name}"}, status_code=409)
    tools_dir = _toolbox_tools_dir()
    if (tools_dir / f"{name}.py").exists():
        return JSONResponse({"error": f"built-in tool source '{name}.py' exists"}, status_code=409)
    class_name = "".join(p.capitalize() for p in name.split("_") if p) + "Tool"
    stub = (
        f'"""\n{name} — user-added plugin tool.\n\n'
        'Created from the Toolbox UI. Implement .run(input: str) -> str and\n'
        'restart BeigeBox to register this plugin.\n'
        'See plugins/README.md for the full plugin contract.\n'
        '"""\n\n'
        f'PLUGIN_NAME = "{name}"\n\n\n'
        f'class {class_name}:\n'
        f'    description = "TODO: describe what {name} does"\n\n'
        '    def __init__(self):\n'
        '        pass\n\n'
        '    def run(self, input_text: str) -> str:\n'
        f'        return f"{name} received: {{input_text}}"\n'
    )
    plugins_enabled = bool(
        get_config().get("tools", {}).get("plugins", {}).get("enabled", False)
    )
    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_target.write_text(stub, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "name": name,
        "kind": "tool",
        "path": str(plugin_target),
        "length": len(stub),
        "plugins_enabled": plugins_enabled,
        "requires_restart": True,
    })


@app.post("/api/v1/toolbox/skills/new")
async def api_toolbox_skill_new(request: Request):
    """Create a new skill at beigebox/skills/<name>/SKILL.md."""
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not _is_valid_skill_name(name):
        return JSONResponse(
            {"error": "name must be lowercase letters/digits/underscores/hyphens, starting with a letter (≤40 chars)"},
            status_code=400,
        )
    skills_dir = _toolbox_skills_dir()
    target_dir = (skills_dir / name).resolve()
    try:
        target_dir.relative_to(skills_dir)
    except ValueError:
        return JSONResponse({"error": "invalid target path"}, status_code=400)
    if target_dir.exists():
        return JSONResponse({"error": f"skill '{name}' already exists"}, status_code=409)
    target = target_dir / "SKILL.md"
    stub = (
        "---\n"
        f"name: {name}\n"
        f"description: TODO — one-line description of what this skill does\n"
        "---\n\n"
        f"# {name}\n\n"
        "TODO: replace this body with instructions the agent should follow when\n"
        "this skill is activated. Skills are read on demand via `read_skill('"
        f"{name}')`.\n"
    )
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(stub, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "name": name,
        "kind": "skill",
        "path": str(target),
        "length": len(stub),
        "requires_restart": True,
    })




# ---------------------------------------------------------------------------
# Conversation Replay (v0.6)
# ---------------------------------------------------------------------------

# /api/v1/conversation/{id}/replay and /fork moved to routers/workspace.py (B-4).


# ---------------------------------------------------------------------------
# Tap — wire log reader with filters (v0.7)
# ---------------------------------------------------------------------------



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


# /api/v1/build-centroids removed in v3 — embedding classifier was deleted.


# /api/v1/workspace, /api/v1/workspace/mounts, /api/v1/workspace/mounts/{name},
# /api/v1/workspace/out/{filename} all moved to routers/workspace.py (B-4).




# _index_document and /api/v1/workspace/upload moved to routers/_shared.py
# and routers/workspace.py respectively (B-4). _index_document is shared
# because routers/tools.py (B-6) uses it for skill indexing.


# /api/v1/transform/pdf moved to routers/workspace.py (B-4).


# /api/v1/web-ui/toggle-vi-mode moved to routers/workspace.py (B-4).


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

# _wire_and_forward extracted to beigebox/routers/_shared.py (B-2).
# Imported below for the catch-all route. The per-endpoint passthroughs
# (/v1/embeddings, /v1/completions, /v1/files/, /v1/fine_tuning/,
# /v1/assistants/, /api/* ollama-native routes) live in routers/openai.py.
from beigebox.routers._shared import _wire_and_forward


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
# OpenAI-compat + Ollama passthroughs extracted to routers/openai.py (B-2).

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
