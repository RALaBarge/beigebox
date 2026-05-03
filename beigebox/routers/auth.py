"""Authentication & session management endpoints.

Extracted from beigebox/main.py (B-3). Includes:
- OAuth flows: /auth/{provider}/login, /auth/{provider}/callback
- Session management: GET /auth/logout, GET /auth/me
- API key management: POST/GET /api/v1/auth/keys,
  DELETE /api/v1/auth/keys/{key_id}

Middleware coupling: ApiKeyMiddleware and WebAuthMiddleware (both in
main.py) populate ``request.state.auth_key`` / ``request.state.web_user``
before the routed handler runs. The endpoints below read those fields
the same way they did in main.py.
"""
from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
)

from beigebox.state import get_state

# Cookie constants — COOKIE_SESSION and COOKIE_STATE live in web_auth.py
# (with safe fallbacks). _COOKIE_VERIFIER is local to the OAuth flow.
try:
    from beigebox.web_auth import COOKIE_SESSION, COOKIE_STATE
except ImportError:
    COOKIE_SESSION = "bb_session"
    COOKIE_STATE = "bb_oauth_state"

_COOKIE_VERIFIER = "bb_oauth_cv"  # PKCE code_verifier (short-lived)

logger = logging.getLogger(__name__)


router = APIRouter()


# ── OAuth flows ───────────────────────────────────────────────────────────

@router.get("/auth/{provider}/login")
async def oauth_login(provider: str, request: Request):
    """Redirect browser to the OAuth provider's authorization page."""
    st = get_state()
    if st.web_auth is None or not st.web_auth.is_enabled():
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)
    prov = st.web_auth.get_provider(provider)
    if prov is None:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=404)

    state = st.web_auth.make_state()
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    auth_url, code_verifier = prov.get_authorization_url(redirect_uri=redirect_uri, state=state)

    is_secure = request.url.scheme == "https"
    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(COOKIE_STATE, state, httponly=True, samesite="lax", secure=is_secure, max_age=600)
    resp.set_cookie(_COOKIE_VERIFIER, code_verifier, httponly=True, samesite="lax", secure=is_secure, max_age=600)
    return resp


@router.get("/auth/{provider}/callback", name="oauth_callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """Exchange OAuth code for a session cookie."""
    st = get_state()
    if st.web_auth is None or not st.web_auth.is_enabled():
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    if error:
        return JSONResponse({"error": f"OAuth error: {error}"}, status_code=400)

    # CSRF state check — constant-time comparison
    expected = request.cookies.get(COOKIE_STATE, "")
    if not expected or not secrets.compare_digest(state, expected):
        return JSONResponse({"error": "Invalid OAuth state — try logging in again"}, status_code=400)

    prov = st.web_auth.get_provider(provider)
    if prov is None:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=404)

    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    code_verifier = request.cookies.get(_COOKIE_VERIFIER, "")

    try:
        user_info = await prov.exchange_code(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:
        logger.error("OAuth callback error (%s): %s", provider, exc)
        return JSONResponse({"error": "OAuth exchange failed"}, status_code=500)

    if st.users:
        user_id = st.users.upsert(
            provider=user_info.provider,
            sub=user_info.sub,
            email=user_info.email,
            name=user_info.name,
            picture=user_info.picture,
        )
    else:
        user_id = hashlib.sha256(f"{user_info.provider}:{user_info.sub}".encode()).hexdigest()[:32]

    session_token = st.web_auth.sign_session(
        user_id=user_id,
        email=user_info.email,
        name=user_info.name,
        picture=user_info.picture,
    )

    is_secure = request.url.scheme == "https"
    resp = RedirectResponse(url="/ui", status_code=302)
    resp.set_cookie(
        COOKIE_SESSION, session_token,
        httponly=True, samesite="strict", secure=is_secure,
        max_age=60 * 60 * 4,  # 4 hours
    )
    resp.delete_cookie(COOKIE_STATE)
    resp.delete_cookie(_COOKIE_VERIFIER)
    return resp


# ── Session management ────────────────────────────────────────────────────

@router.get("/auth/logout")
async def oauth_logout():
    """Clear the session cookie and redirect to the root."""
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(COOKIE_SESSION)
    return resp


@router.get("/auth/me")
async def auth_me(request: Request):
    """Return current web user info, or {authenticated: false}."""
    st = get_state()
    if st.web_auth is None or not st.web_auth.is_enabled():
        return JSONResponse({"authenticated": False, "mode": "none"})
    token = request.cookies.get(COOKIE_SESSION, "")
    user = st.web_auth.verify_session(token) if token else None
    if user is None:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, **user})


# ── API key management ────────────────────────────────────────────────────

@router.post("/api/v1/auth/keys")
async def create_api_key(request: Request):
    """Create a new API key for the authenticated user."""
    st = get_state()
    if not st.conversations:
        return JSONResponse({"error": "API key storage not configured"}, status_code=501)

    token = request.cookies.get(COOKIE_SESSION, "")
    if st.web_auth and st.web_auth.is_enabled():
        user_info = st.web_auth.verify_session(token)
        if not user_info:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        user_id = user_info.get("user_id")
    else:
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    try:
        body = await request.json()
        name = body.get("name", "default")
    except Exception:
        name = "default"

    key_id, plain_key = st.api_keys.create(user_id, name)
    return JSONResponse({
        "key_id": key_id,
        "key": plain_key,
        "name": name,
        "message": "Save this key somewhere safe — you won't see it again",
    })


@router.get("/api/v1/auth/keys")
async def list_api_keys(request: Request):
    """List all API keys for the authenticated user (without plaintext)."""
    st = get_state()
    if not st.conversations:
        return JSONResponse({"error": "API key storage not configured"}, status_code=501)

    token = request.cookies.get(COOKIE_SESSION, "")
    if st.web_auth and st.web_auth.is_enabled():
        user_info = st.web_auth.verify_session(token)
        if not user_info:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        user_id = user_info.get("user_id")
    else:
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    keys = st.api_keys.list_for_user(user_id)
    return JSONResponse({"keys": keys})


@router.delete("/api/v1/auth/keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request):
    """Revoke an API key."""
    st = get_state()
    if not st.conversations:
        return JSONResponse({"error": "API key storage not configured"}, status_code=501)

    token = request.cookies.get(COOKIE_SESSION, "")
    if st.web_auth and st.web_auth.is_enabled():
        user_info = st.web_auth.verify_session(token)
        if not user_info:
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        user_id = user_info.get("user_id")
    else:
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    success = st.api_keys.revoke(key_id, user_id)
    if not success:
        return JSONResponse({"error": "Key not found or already revoked"}, status_code=404)
    return JSONResponse({"revoked": True})
