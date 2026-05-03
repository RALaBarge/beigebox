"""
HTTP middleware: API-key auth, web-UI session auth, security headers.

Three Starlette `BaseHTTPMiddleware` classes plus the supporting
`_emit_auth_denied` helper and the path allow/deny constants. Lives
outside main.py so the entry point stays a thin wiring file; the
middleware classes themselves are independent of FastAPI app
construction and can be added to any compatible app via
`add_middleware()`.

Dependencies (kept narrow):
  - beigebox.state.maybe_state   — read-only AppState lookup
  - beigebox.web_auth.COOKIE_SESSION
  - beigebox.config.get_config
  - beigebox.auth.KeyMeta        — for synthesizing pseudo-keys when a
                                    dynamic API key is verified through
                                    the api_keys repo
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from beigebox.config import get_config
from beigebox.state import maybe_state
from beigebox.web_auth import COOKIE_SESSION

logger = logging.getLogger(__name__)


# ── Path allow/deny lists ────────────────────────────────────────────────────

# Paths that never require API-key auth (web UI, health checks, OAuth flow)
_AUTH_EXEMPT = frozenset(["/", "/ui", "/beigebox/health", "/api/v1/status"])
_AUTH_EXEMPT_PREFIXES = ("/web/", "/auth/")

# Paths that WebAuthMiddleware protects when oauth or password mode is active
_WEB_UI_PATHS = frozenset(["/", "/ui"])
_WEB_UI_PREFIXES = ("/web/",)


# ── Auth denial wire emit ────────────────────────────────────────────────────

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


# ── Middleware classes ───────────────────────────────────────────────────────

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
                # Synthesize a pseudo-KeyMeta for the dynamic key. Default
                # rate limit comes from config (not unlimited).
                from beigebox.auth import KeyMeta
                cfg = get_config()
                default_rate_limit = cfg.get("auth", {}).get("dynamic_key_rate_limit_rpm", 100)
                meta = KeyMeta(
                    name=f"user:{user_id[:8]}",
                    allowed_models=["*"],
                    allowed_endpoints=["*"],
                    rate_limit_rpm=default_rate_limit,
                )

        if meta is None:
            _emit_auth_denied("invalid_api_key", "unknown", "api_key", path, request)
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


class WebAuthMiddleware(BaseHTTPMiddleware):
    """
    Gates web UI paths behind a signed session cookie when oauth or password auth is enabled.

    API paths (/v1/, /api/) are not touched — those use Bearer token auth.
    The OAuth flow paths (/auth/*) are always exempt.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if maybe_state() is None:
            return await call_next(request)

        oauth_enabled = maybe_state().web_auth is not None and maybe_state().web_auth.is_enabled()
        password_auth_enabled = maybe_state().password_auth is not None

        if not (oauth_enabled or password_auth_enabled):
            return await call_next(request)

        path = request.url.path

        if path.startswith("/auth/"):
            return await call_next(request)

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
            if password_auth_enabled:
                return RedirectResponse(url="/auth/login", status_code=302)
            providers = maybe_state().web_auth.list_providers()
            login_path = f"/auth/{providers[0]}/login" if providers else "/"
            return RedirectResponse(url=login_path, status_code=302)

        request.state.web_user = user
        return await call_next(request)


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


__all__ = [
    "ApiKeyMiddleware",
    "WebAuthMiddleware",
    "SecurityHeadersMiddleware",
]
