"""Shared helpers used across multiple router modules.

Helpers are extracted from beigebox/main.py incrementally, alongside
the first router commit that needs each one:

- ``_wire_and_forward``   → arrived with routers/openai.py (B-2)
- ``_require_admin``      → arrived with routers/auth.py (B-3)
- ``_index_document``     → moves with routers/workspace.py (B-4)

``_emit_auth_denied`` stays in beigebox/main.py because only the
ApiKeyMiddleware and WebAuthMiddleware (which live there) use it.
"""
from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from beigebox.config import get_config, get_primary_backend_url
from beigebox.state import get_state, maybe_state


def _require_admin(request: Request) -> JSONResponse | None:
    """Gate handler: returns a 403 JSONResponse if the calling key is not admin.

    Returns None when the key is admin (handler should proceed).

    Auth-disabled mode (no keys configured) is treated as admin-allowed since
    the operator running an unauthed proxy is implicitly trusting all callers.
    """
    state = maybe_state()
    if state is None or state.auth_registry is None or not state.auth_registry.is_enabled():
        return None
    meta = getattr(request.state, "auth_key", None)
    if meta is not None and getattr(meta, "admin", False):
        return None
    return JSONResponse(
        {"error": {
            "message": "Admin key required for this endpoint.",
            "type": "permission_denied",
            "code": "admin_required",
        }},
        status_code=403,
    )


async def _wire_and_forward(
    request: Request,
    route_label: str,
    override_base_url: str | None = None,
) -> StreamingResponse:
    """Generic forward: log to wiretap, stream response from backend verbatim.

    Used for all known-but-not-specially-handled OpenAI/Ollama endpoints
    (and the catch-all). ``override_base_url`` lets a caller forward to a
    target other than the default backend.url from config.
    """
    cfg = get_config()
    backend_url = (override_base_url or get_primary_backend_url(cfg)).rstrip("/")
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
