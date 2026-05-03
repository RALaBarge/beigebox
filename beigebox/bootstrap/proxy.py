"""Proxy + observability bootstrap.

This module owns the entire proxy + capture + wire-sink + egress lifecycle
because they are tightly coupled:

  - MultiBackendRouter is constructed first (Proxy needs it as a constructor arg)
  - Egress hooks are built and *started* here (Proxy receives them, then
    runs them as fire-and-forget on every request)
  - Proxy() is constructed (it builds its own WireLog internally)
  - CaptureFanout is assigned to ``proxy.capture`` AFTER Proxy() since it
    references ``proxy.wire``
  - PostgresWireSink is attached as a third sink AFTER capture is wired
  - ``set_wire_log(proxy.wire)`` runs LAST so the typed-event helpers reach
    every sink (jsonl + sqlite + postgres)
  - APIAnomalyDetectorTool late-binding requires both proxy and tool_registry

The shutdown half (``stop_egress_hooks``, ``proxy.wire.close()``) lives in
``bootstrap.__init__.shutdown(state)`` because it operates on the AppState.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from beigebox.backends.router import MultiBackendRouter
from beigebox.config import get_effective_backends_config
from beigebox.observability.egress import build_egress_hooks, start_egress_hooks
from beigebox.proxy import Proxy
from beigebox.storage.repos import make_wire_event_repo

logger = logging.getLogger(__name__)


@dataclass
class ProxyBundle:
    proxy: Proxy
    backend_router: MultiBackendRouter | None
    egress_hooks: list[Any]


async def build_proxy(
    cfg: dict,
    *,
    conversations,
    vector_store,
    hook_manager,
    tool_registry,
    blob_store,
    wire_events,
    extraction_detector,
) -> ProxyBundle:
    """Construct the proxy stack. Async because egress hooks must start
    before the Proxy fan-outs run.

    Side effects:
      - ``proxy.capture`` is assigned a CaptureFanout
      - PostgresWireSink is attached when ``storage.postgres.connection_string``
        is set (failure is non-fatal — falls back to jsonl + sqlite)
      - ``set_wire_log(proxy.wire)`` is called so typed-event dispatch works
      - APIAnomalyDetectorTool is late-bound to ``proxy.anomaly_detector``
    """

    # Multi-backend router — reads effective config (runtime_config.yaml
    # overrides config.yaml).
    backend_router: MultiBackendRouter | None = None
    backends_enabled, backends_cfg = get_effective_backends_config()
    if backends_enabled:
        if backends_cfg:
            model_routes = cfg.get("routing", {}).get("model_routes", [])
            backend_router = MultiBackendRouter(backends_cfg, model_routes=model_routes)
            logger.info(
                "Multi-backend router: enabled (%d backends)",
                len(backend_router.backends),
            )
        else:
            logger.warning("backends_enabled=true but no backends configured")
    else:
        logger.info("Multi-backend router: disabled")

    # Observability egress hooks (webhook batching, fire-and-forget). Built +
    # started before Proxy() so the proxy's ``egress_hooks`` argument is
    # non-empty when present.
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
    # if postgres is unavailable. Errors per-write are already isolated by
    # WireLog (commit 7aba40c).
    pg_conn = (
        cfg.get("storage", {}).get("postgres", {}).get("connection_string")
    )
    if pg_conn:
        try:
            from beigebox.storage.db import make_db
            from beigebox.storage.wire_sink import make_sink
            pg_db = make_db("postgres", connection_string=pg_conn)
            pg_wire_events = make_wire_event_repo(pg_db)
            pg_wire_events.create_tables()
            proxy.wire.add_sink(make_sink("postgres", repo=pg_wire_events))
            logger.info(
                "PostgresWireSink attached: wire_events fan-out → "
                "jsonl + sqlite + postgres",
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
    aad_tool = tool_registry.get("api_anomaly_detector")
    if aad_tool and proxy.anomaly_detector:
        aad_tool.set_detector(proxy.anomaly_detector)
        logger.info("APIAnomalyDetectorTool bound to proxy anomaly detector")

    return ProxyBundle(
        proxy=proxy,
        backend_router=backend_router,
        egress_hooks=egress_hooks,
    )


__all__ = ["ProxyBundle", "build_proxy"]
