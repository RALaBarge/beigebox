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
    from beigebox.storage.db.base import BaseDB
    from beigebox.storage.repos.api_keys import ApiKeyRepo
    from beigebox.storage.repos.conversations import ConversationRepo
    from beigebox.storage.repos.quarantine import QuarantineRepo
    from beigebox.storage.repos.users import UserRepo
    from beigebox.storage.repos.wire_events import WireEventRepo
    from beigebox.storage.vector_store import VectorStore
    from beigebox.tools.registry import ToolRegistry
    from beigebox.hooks import HookManager
    from beigebox.backends.router import MultiBackendRouter
    from beigebox.costs import CostTracker
    from beigebox.auth import MultiKeyAuthRegistry
    from beigebox.mcp_server import McpServer
    from beigebox.observability.egress import EgressHook
    from beigebox.web_auth import WebAuthManager, SimplePasswordAuth
    from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
    from beigebox.security.extraction_detector import ExtractionDetector
    from beigebox.security.audit_logger import AuditLogger
    from beigebox.security.honeypots import HoneypotManager
    from beigebox.security.enhanced_injection_guard import EnhancedInjectionGuard
    from beigebox.security.rag_content_scanner import RAGContentScanner


@dataclass
class AppState:
    """All server subsystems, initialized during FastAPI lifespan startup."""

    proxy: Proxy | None = None
    tool_registry: ToolRegistry | None = None
    db: BaseDB | None = None  # BaseDB shim shared by per-entity repos
    api_keys: ApiKeyRepo | None = None
    conversations: ConversationRepo | None = None
    quarantine: QuarantineRepo | None = None
    users: UserRepo | None = None
    wire_events: WireEventRepo | None = None
    vector_store: VectorStore | None = None
    blob_store: Any = None
    hook_manager: HookManager | None = None
    backend_router: MultiBackendRouter | None = None
    cost_tracker: CostTracker | None = None
    auth_registry: MultiKeyAuthRegistry | None = None
    web_auth: WebAuthManager | None = None
    password_auth: SimplePasswordAuth | None = None
    mcp_server: McpServer | None = None
    # Pen/sec MCP — separate registry of offensive-security tool wrappers,
    # mounted at POST /pen-mcp. None when disabled.
    security_mcp_server: McpServer | None = None
    poisoning_detector: RAGPoisoningDetector | None = None
    extraction_detector: ExtractionDetector | None = None
    # Security audit & detection modules
    audit_logger: AuditLogger | None = None
    honeypot_manager: HoneypotManager | None = None
    injection_guard: EnhancedInjectionGuard | None = None
    rag_scanner: RAGContentScanner | None = None
    egress_hooks: list[Any] = field(default_factory=list)  # list[EgressHook]
