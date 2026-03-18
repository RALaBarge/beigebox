"""
AppState — all subsystems initialized at server startup, held in one typed object.

Replaces 13 module-level globals in main.py with a single container.
Access via get_state() after the lifespan has run.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from beigebox.proxy import Proxy
    from beigebox.storage.sqlite_store import SQLiteStore
    from beigebox.storage.vector_store import VectorStore
    from beigebox.tools.registry import ToolRegistry
    from beigebox.agents.decision import DecisionAgent
    from beigebox.hooks import HookManager
    from beigebox.backends.router import MultiBackendRouter
    from beigebox.costs import CostTracker
    from beigebox.auth import MultiKeyAuthRegistry
    from beigebox.mcp_server import McpServer
    from beigebox.amf_mesh import AmfMeshAdvertiser
    from beigebox.observability.egress import EgressHook


@dataclass
class AppState:
    """All server subsystems, initialized during FastAPI lifespan startup."""

    proxy: Proxy | None = None
    tool_registry: ToolRegistry | None = None
    sqlite_store: SQLiteStore | None = None
    vector_store: VectorStore | None = None
    blob_store: Any = None
    decision_agent: DecisionAgent | None = None
    hook_manager: HookManager | None = None
    backend_router: MultiBackendRouter | None = None
    cost_tracker: CostTracker | None = None
    embedding_classifier: Any = None
    auth_registry: MultiKeyAuthRegistry | None = None
    mcp_server: McpServer | None = None
    amf_advertiser: AmfMeshAdvertiser | None = None
    egress_hooks: list[Any] = field(default_factory=list)  # list[EgressHook]
    # Runtime registry: run_id → asyncio.Queue for harness steering injection.
    # Registered by active harness/ralph runs, consumed by /inject endpoint.
    harness_injection_queues: dict[str, asyncio.Queue] = field(default_factory=dict)
