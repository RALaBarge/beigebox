"""Lifespan startup/shutdown orchestrator.

``main.lifespan()`` is a five-line passthrough now: read config once,
hand it to ``startup(app)`` to get a fully composed ``AppState``, then
``set_state(...)`` it. Each subsystem lives in its own module under
``beigebox.bootstrap`` so adding a new subsystem doesn't widen
``main.py``.

Dependency / order constraints (preserved exactly from the pre-extract
lifespan):

  1. logging configured + payload-log path bound first
  2. storage built — vector_store must exist before tools
  3. tools built — needs vector_store
  4. auth built — needs ``users`` repo from storage
  5. mcp built — needs tool_registry; loads skills internally
  6. security built — independent of the above
  7. proxy built — needs conversations, vector_store, hook_manager,
     tool_registry, blob_store, wire_events, extraction_detector. Builds
     egress hooks, attaches PostgresWireSink, late-binds AAD, etc.
  8. preload tasks scheduled (fire-and-forget)

Shutdown reverses the side-effects ``proxy`` set up:
  - log "shutting down"
  - stop egress hooks
  - close proxy.wire (flushes jsonl + sqlite + postgres sinks)
  - close payload log
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from beigebox.app_state import AppState
from beigebox.config import get_config, get_primary_backend_url
from beigebox.observability.egress import stop_egress_hooks
from beigebox.payload_log import close as _pl_close

from beigebox.bootstrap.auth import build_auth
from beigebox.bootstrap.logging_setup import configure_logging_and_payload_log
from beigebox.bootstrap.mcp import build_mcp
from beigebox.bootstrap.preload import schedule_preloads
from beigebox.bootstrap.proxy import build_proxy
from beigebox.bootstrap.security import build_security
from beigebox.bootstrap.storage import build_storage
from beigebox.bootstrap.tools import build_tools

logger = logging.getLogger(__name__)


async def startup(app: FastAPI) -> AppState:  # noqa: ARG001 — app reserved for future use
    """Run every bootstrap submodule in dependency order; return AppState."""
    cfg = get_config()
    configure_logging_and_payload_log(cfg)

    storage = build_storage(cfg)
    tools = build_tools(cfg, vector_store=storage.vector_store)
    auth = build_auth(cfg, users=storage.users)
    mcp = build_mcp(cfg, tool_registry=tools.tool_registry)
    security = build_security(cfg)

    proxy_bundle = await build_proxy(
        cfg,
        conversations=storage.conversations,
        vector_store=storage.vector_store,
        hook_manager=tools.hook_manager,
        tool_registry=tools.tool_registry,
        blob_store=storage.blob_store,
        wire_events=storage.wire_events,
        extraction_detector=security.extraction_detector,
    )

    state = AppState(
        proxy=proxy_bundle.proxy,
        tool_registry=tools.tool_registry,
        db=storage.db,
        api_keys=storage.api_keys,
        conversations=storage.conversations,
        quarantine=storage.quarantine,
        users=storage.users,
        wire_events=storage.wire_events,
        vector_store=storage.vector_store,
        blob_store=storage.blob_store,
        hook_manager=tools.hook_manager,
        backend_router=proxy_bundle.backend_router,
        cost_tracker=storage.cost_tracker,
        auth_registry=auth.auth_registry,
        web_auth=auth.web_auth,
        mcp_server=mcp.mcp_server,
        security_mcp_server=mcp.security_mcp_server,
        poisoning_detector=storage.poisoning_detector,
        extraction_detector=security.extraction_detector,
        # audit_logger / honeypot_manager intentionally omitted — defaulted
        # to None on AppState until their dedicated revival commit lands.
        injection_guard=security.injection_guard,
        rag_scanner=security.rag_scanner,
        egress_hooks=proxy_bundle.egress_hooks,
    )

    logger.info(
        "BeigeBox started — listening on %s:%s, backend %s",
        cfg["server"]["host"],
        cfg["server"]["port"],
        get_primary_backend_url(cfg),
    )
    logger.info(
        "Storage: SQLite=%s, Vector=%s",
        storage.sqlite_path,
        storage.vector_store_path,
    )
    logger.info("Tools: %s", tools.tool_registry.list_tools())
    logger.info("Hooks: %s", tools.hook_manager.list_hooks())

    # Preload models — run concurrently in the background so startup is
    # not blocked. Both use retry-with-backoff; Ollama may still be
    # loading models from disk. Tasks are NOT awaited.
    schedule_preloads(cfg)

    return state


async def shutdown(state: AppState) -> None:
    """Reverse ``startup`` side-effects in safe order."""
    logger.info("BeigeBox shutting down")
    if state and state.egress_hooks:
        await stop_egress_hooks(state.egress_hooks)
    if state and state.proxy and state.proxy.wire:
        state.proxy.wire.close()
    _pl_close()
    logger.info("Wiretap and payload log flushed and closed")


__all__ = ["startup", "shutdown"]
