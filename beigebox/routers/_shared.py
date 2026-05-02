"""Shared helpers used across multiple router modules.

Helpers are extracted from beigebox/main.py incrementally, alongside
the first router commit that needs each one:

- ``_wire_and_forward``   → arrived with routers/openai.py (B-2)
- ``_require_admin``      → moves with routers/auth.py (B-3)
- ``_emit_auth_denied``   → moves with routers/auth.py (B-3)
- ``_index_document``     → moves with routers/workspace.py (B-4)
"""
from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

from beigebox.config import get_config, get_primary_backend_url
from beigebox.state import get_state


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
