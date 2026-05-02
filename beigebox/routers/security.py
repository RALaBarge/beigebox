"""Security control plane + storage stats endpoints.

Extracted from beigebox/main.py (B-4). Mostly read-only telemetry:
audit logs, honeypot status, injection-guard / RAG-scanner /
extraction-detector quarantine queues, plus the /beigebox/* legacy
status routes (stats, search, health) and /metrics/quarantine for
Prometheus scraping.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from beigebox import __version__ as _BB_VERSION
from beigebox.config import get_config
from beigebox.state import get_state


router = APIRouter()


# ── Storage / vector / tool stats ─────────────────────────────────────────

@router.get("/beigebox/stats")
async def stats():
    """Return storage and usage statistics."""
    _st = get_state()
    sqlite_stats = _st.conversations.get_stats() if _st.conversations else {}
    vector_stats = _st.vector_store.get_stats() if _st.vector_store else {}
    tools = _st.tool_registry.list_tools() if _st.tool_registry else []
    hooks = _st.hook_manager.list_hooks() if _st.hook_manager else []

    return JSONResponse({
        "sqlite": sqlite_stats,
        "vector": vector_stats,
        "tools": tools,
        "hooks": hooks,
    })


@router.get("/metrics/quarantine")
async def metrics_quarantine(format: str = "json"):
    """Get RAG poisoning quarantine metrics."""
    from beigebox.observability.poisoning_metrics import PoisoningMetrics
    _st = get_state()

    if not _st.quarantine:
        return JSONResponse({"error": "Quarantine repo not initialized"}, status_code=503)

    metrics = PoisoningMetrics(_st.quarantine)

    if format == "prometheus":
        return Response(
            content=metrics.get_prometheus_format(),
            media_type="text/plain; version=0.0.4",
        )
    else:
        return JSONResponse(metrics.get_json_metrics())


# ── Security control plane (P1 audit + detection + forensics) ───────────

@router.get("/api/v1/security/status")
async def security_status():
    """Aggregate status of all security subsystems (6 modules)."""
    _st = get_state()
    return JSONResponse({
        "audit_logger": {
            "enabled": _st.audit_logger is not None,
            "stats": _st.audit_logger.get_stats() if _st.audit_logger else {},
        },
        "honeypots": {
            "enabled": _st.honeypot_manager is not None,
            "trap_count": len(_st.honeypot_manager.traps) if _st.honeypot_manager else 0,
        },
        "injection_guard": {
            "enabled": _st.injection_guard is not None,
            "quarantined": len(_st.injection_guard._quarantine) if _st.injection_guard else 0,
        },
        "rag_scanner": {
            "enabled": _st.rag_scanner is not None,
            "quarantined": len(_st.rag_scanner._quarantine) if _st.rag_scanner else 0,
        },
        "extraction_detector": {
            "enabled": _st.extraction_detector is not None,
            "active_sessions": len(_st.extraction_detector._sessions) if _st.extraction_detector else 0,
        },
        "anomaly_detector": {
            "enabled": _st.proxy.anomaly_detector is not None if _st.proxy else False,
        },
    })


@router.get("/api/v1/security/audit")
async def security_audit(hours: int = 24, severity: str = "", tool: str = "", limit: int = 100):
    """Queryable audit log with filters: severity, tool, hours."""
    _st = get_state()
    if not _st.audit_logger:
        return JSONResponse({"error": "Audit logger not initialized"}, status_code=503)
    stats = _st.audit_logger.get_stats(hours=hours)
    entries = _st.audit_logger.search_denials(
        severity=severity or None,
        tool=tool or None,
        limit=limit,
        hours=hours,
    )
    return JSONResponse({"stats": stats, "entries": entries})


@router.get("/api/v1/security/audit/patterns")
async def security_patterns(hours: int = 24):
    """Detect suspicious patterns (many denials, rapid calls, etc.)."""
    _st = get_state()
    if not _st.audit_logger:
        return JSONResponse({"error": "Audit logger not initialized"}, status_code=503)
    patterns = _st.audit_logger.search_suspicious_patterns(hours=hours, threshold=1)
    return JSONResponse({"patterns": patterns})


@router.get("/api/v1/security/injection/stats")
async def injection_stats():
    """Injection guard quarantine statistics."""
    _st = get_state()
    if not _st.injection_guard:
        return JSONResponse({"enabled": False, "message": "Injection guard not initialized"}, status_code=503)
    stats = _st.injection_guard.get_quarantine_stats()
    return JSONResponse(stats)


@router.get("/api/v1/security/rag/quarantine")
async def rag_quarantine():
    """RAG scanner quarantine queue with confidence breakdown."""
    _st = get_state()
    if not _st.rag_scanner:
        return JSONResponse({"enabled": False, "message": "RAG scanner not initialized"}, status_code=503)
    stats = _st.rag_scanner.get_quarantine_stats()
    contents = _st.rag_scanner.get_quarantine_contents()
    return JSONResponse({"stats": stats, "entries": contents})


@router.get("/api/v1/security/extraction/sessions")
async def extraction_sessions():
    """All active extraction detector sessions with risk levels."""
    _st = get_state()
    if not _st.extraction_detector:
        return JSONResponse({"enabled": False, "message": "Extraction detector not initialized"}, status_code=503)
    sessions = []
    for session_id, metrics in _st.extraction_detector._sessions.items():
        analysis = _st.extraction_detector.analyze_pattern(session_id)
        sessions.append(analysis)
    return JSONResponse({"sessions": sessions})


@router.get("/api/v1/security/honeypots")
async def honeypots_list():
    """Honeypot trap definitions and status."""
    _st = get_state()
    if not _st.honeypot_manager:
        return JSONResponse({"enabled": False, "message": "Honeypot manager not initialized"}, status_code=503)
    traps = _st.honeypot_manager.get_honeypot_definitions()
    recent_triggers = []
    if _st.audit_logger:
        recent_triggers = _st.audit_logger.search_bypass_attempts(limit=20)
    return JSONResponse({"traps": traps, "recent_triggers": recent_triggers})


# ── Search (vector-store backed) ──────────────────────────────────────────

@router.get("/beigebox/search")
async def search_conversations(q: str, n: int = 5, role: str | None = None):
    """Semantic search over stored conversations (raw message hits)."""
    _st = get_state()
    if not _st.vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = _st.vector_store.search(q, n_results=n, role_filter=role)
    return JSONResponse({"query": q, "results": results})


@router.get("/api/v1/search")
async def api_search_conversations(q: str, n: int = 5, role: str | None = None):
    """Semantic search grouped by conversation. Returns conversations ranked
    by best message match, with excerpt.
    """
    _st = get_state()
    if not _st.vector_store:
        return JSONResponse({"error": "Vector store not initialized"}, status_code=503)
    results = _st.vector_store.search_grouped(q, n_conversations=n, role_filter=role)
    return JSONResponse({"query": q, "results": results, "count": len(results)})


# ── Health ────────────────────────────────────────────────────────────────

@router.get("/beigebox/health")
async def health():
    """Health check."""
    _st = get_state()
    cfg = get_config()
    return JSONResponse({
        "status": "ok",
        "version": _BB_VERSION,
        "backend_url": cfg.get("backend", {}).get("url", "http://localhost:11434").rstrip("/"),
        "rag_poisoning_detection": "enabled" if _st.poisoning_detector else "disabled",
    })
