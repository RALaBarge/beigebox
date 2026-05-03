"""
Thin OAuth shim for BeigeBox web UI.

OAuthProvider is a protocol — drop in any OAuth2/OIDC provider.
Ships: GoogleProvider.

Config (config.yaml):
  auth:
    web_ui:
      mode: none        # none | oauth
      providers:
        - name: google
          client_id: "..."
          allowed_emails: []  # empty = any Google account

Secrets — never in config files:
  client_secret:  agentauth add google     OR  BB_GOOGLE_CLIENT_SECRET env var
  session_secret: agentauth add bb-session OR  BB_SESSION_SECRET env var

When mode=oauth, all web UI paths require a valid signed session cookie
(bb_session). The cookie is set after a successful provider callback.
API paths (/v1/, /api/) are unaffected — they use Bearer token auth as before.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from abc import ABC, abstractmethod
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

COOKIE_SESSION  = "bb_session"
COOKIE_STATE    = "bb_oauth_state"
_SESSION_MAX_AGE = 60 * 60 * 4   # 4 hours — industry standard for web sessions


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class OAuthUserInfo:
    sub: str          # provider-unique subject ID
    email: str
    name: str
    picture: str = ""
    provider: str = ""


# ---------------------------------------------------------------------------
# Provider protocol — implement this to add a new provider
# ---------------------------------------------------------------------------

@runtime_checkable
class OAuthProvider(Protocol):
    name: str

    def get_authorization_url(self, redirect_uri: str, state: str) -> tuple[str, str]: ...
    """Return (authorization_url, code_verifier). code_verifier is PKCE S256."""

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> OAuthUserInfo: ...


class AuthProvider(ABC):
    """ABC for OAuth2/OIDC providers. Extend this to add a new provider."""

    name: str

    @abstractmethod
    def get_authorization_url(self, redirect_uri: str, state: str) -> tuple[str, str]:
        """Return (authorization_url, code_verifier). code_verifier is PKCE S256."""

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> OAuthUserInfo:
        """Exchange authorization code for user identity."""


class NullAuthProvider(AuthProvider):
    """Always-allow stub — accepts any code and returns a fixed anonymous identity."""

    name = "none"

    def get_authorization_url(self, redirect_uri: str, state: str) -> tuple[str, str]:
        return ("#", "")

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> OAuthUserInfo:
        return OAuthUserInfo(sub="null", email="null@localhost", name="Null User", provider="none")


def make_auth(type: str, **kwargs) -> AuthProvider:
    """Factory: return an AuthProvider instance for the given provider type.

    Types: "github", "google", "none".
    kwargs are forwarded to the provider constructor (not used for "none").
    """
    t = type.lower()
    if t == "none":
        return NullAuthProvider()
    if t == "github":
        return GitHubProvider(**kwargs)
    if t == "google":
        return GoogleProvider(**kwargs)
    raise ValueError(f"Unknown auth provider type: {type!r}. Expected one of: github, google, none")


# ---------------------------------------------------------------------------
# GitHub (OAuth2)
# ---------------------------------------------------------------------------

class GitHubProvider(AuthProvider):
    name = "github"

    _AUTH_URL     = "https://github.com/login/oauth/authorize"
    _TOKEN_URL    = "https://github.com/login/oauth/access_token"
    _USERINFO_URL = "https://api.github.com/user"
    _EMAIL_URL    = "https://api.github.com/user/emails"

    def __init__(self, client_id: str, client_secret: str, allowed_orgs: list[str] = None):
        self.client_id       = client_id
        self._client_secret  = client_secret
        self._allowed_orgs   = set(allowed_orgs or [])  # empty = any org

    def get_authorization_url(self, redirect_uri: str, state: str) -> tuple[str, str]:
        # PKCE S256 — generate verifier, derive challenge
        code_verifier  = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {
            "client_id":             self.client_id,
            "redirect_uri":          redirect_uri,
            "response_type":         "code",
            "scope":                 "user:email read:org",
            "state":                 state,
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._AUTH_URL}?{urlencode(params)}", code_verifier

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> OAuthUserInfo:
        token_data = {
            "code":          code,
            "client_id":     self.client_id,
            "client_secret": self._client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        }
        if code_verifier:
            token_data["code_verifier"] = code_verifier

        async with httpx.AsyncClient(timeout=10) as client:
            # Exchange code for access token
            tok = await client.post(
                self._TOKEN_URL,
                data=token_data,
                headers={"Accept": "application/json"},
            )
            tok.raise_for_status()
            access_token = tok.json()["access_token"]

            # Get user info
            ui = await client.get(
                self._USERINFO_URL,
                headers={"Authorization": f"token {access_token}"},
            )
            ui.raise_for_status()
            info = ui.json()

            # Get primary email (user might not have a public email)
            email = info.get("email") or ""
            if not email:
                emails = await client.get(
                    self._EMAIL_URL,
                    headers={"Authorization": f"token {access_token}"},
                )
                emails.raise_for_status()
                # Find primary verified email
                for e in emails.json():
                    if e.get("primary") and e.get("verified"):
                        email = e.get("email", "")
                        break

        user = OAuthUserInfo(
            sub      = str(info["id"]),  # GitHub user ID
            email    = email,
            name     = info.get("name") or info.get("login", ""),
            picture  = info.get("avatar_url", ""),
            provider = "github",
        )

        # Check org membership if allowed_orgs specified
        if self._allowed_orgs:
            # Query /user/orgs to get list of orgs user belongs to
            orgs_resp = await client.get(
                "https://api.github.com/user/orgs",
                headers={"Authorization": f"token {access_token}"},
                params={"per_page": 100},  # Paginate if user is in many orgs
            )
            orgs_resp.raise_for_status()
            user_orgs = {org.get("login", "").lower() for org in orgs_resp.json()}

            # Check if user is in at least one allowed org
            allowed_orgs_lower = {org.lower() for org in self._allowed_orgs}
            if not user_orgs & allowed_orgs_lower:  # Set intersection
                raise PermissionError(
                    f"User is not a member of any allowed organizations. "
                    f"Allowed: {', '.join(self._allowed_orgs)}; "
                    f"User orgs: {', '.join(user_orgs) if user_orgs else '(none)'}"
                )

        return user


# ---------------------------------------------------------------------------
# Google (OpenID Connect)
# ---------------------------------------------------------------------------

class GoogleProvider(AuthProvider):
    name = "google"

    _AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
    _TOKEN_URL    = "https://oauth2.googleapis.com/token"
    _USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self, client_id: str, client_secret: str, allowed_emails: list[str]):
        self.client_id       = client_id
        self._client_secret  = client_secret
        self._allowed_emails: set[str] = {e.lower() for e in allowed_emails}

    def get_authorization_url(self, redirect_uri: str, state: str) -> tuple[str, str]:
        # PKCE S256 — generate verifier, derive challenge
        code_verifier  = secrets.token_urlsafe(64)
        code_challenge = (
            urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        params = {
            "client_id":             self.client_id,
            "redirect_uri":          redirect_uri,
            "response_type":         "code",
            "scope":                 "openid email profile",
            "state":                 state,
            "access_type":           "online",
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._AUTH_URL}?{urlencode(params)}", code_verifier

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> OAuthUserInfo:
        token_data = {
            "code":          code,
            "client_id":     self.client_id,
            "client_secret": self._client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        }
        if code_verifier:
            token_data["code_verifier"] = code_verifier

        async with httpx.AsyncClient(timeout=10) as client:
            tok = await client.post(self._TOKEN_URL, data=token_data)
            tok.raise_for_status()
            access_token = tok.json()["access_token"]

            ui = await client.get(
                self._USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            ui.raise_for_status()
            info = ui.json()

        # Reject unverified emails — prevents allowed_emails bypass
        if not info.get("email_verified", False):
            raise PermissionError(
                f"Email '{info.get('email', '?')}' is not verified by {self.name}"
            )

        user = OAuthUserInfo(
            sub      = info["sub"],
            email    = info.get("email", ""),
            name     = info.get("name", ""),
            picture  = info.get("picture", ""),
            provider = "google",
        )
        if self._allowed_emails and user.email.lower() not in self._allowed_emails:
            raise PermissionError(f"Email '{user.email}' not in allowed list")
        return user


# ---------------------------------------------------------------------------
# Manager — built once at startup from config
# ---------------------------------------------------------------------------

class WebAuthManager:
    """Builds providers from config, signs/verifies session cookies."""

    def __init__(self, web_ui_cfg: dict):
        self.mode = web_ui_cfg.get("mode", "none")
        self._providers: dict[str, OAuthProvider] = {}
        self._signer: URLSafeTimedSerializer | None = None

        if self.mode != "oauth":
            return

        secret, secret_source = _resolve_secret("bb-session")
        if not secret:
            secret = secrets.token_hex(32)
            logger.warning(
                "WebAuth: BB_SESSION_SECRET not set — ephemeral key in use; "
                "sessions will not survive a restart. "
                "Fix: set BB_SESSION_SECRET env var  or:  agentauth add bb-session"
            )
        else:
            logger.info("WebAuth: BB_SESSION_SECRET resolved from %s", secret_source)
        self._signer = URLSafeTimedSerializer(secret, salt="bb-web-session")

        for prov_cfg in web_ui_cfg.get("providers", []):
            pname = prov_cfg.get("name", "").lower()
            if pname == "github":
                client_id = prov_cfg.get("client_id", "").strip()
                client_secret, secret_source = _resolve_secret("github")
                if not client_id or not client_secret:
                    logger.error(
                        "WebAuth: GitHub provider missing client_id or client_secret — skipping. "
                        "Set BB_GITHUB_CLIENT_SECRET or run: agentauth add github"
                    )
                    continue
                self._providers["github"] = make_auth(
                    "github",
                    client_id=client_id,
                    client_secret=client_secret,
                    allowed_orgs=prov_cfg.get("allowed_orgs", []),
                )
                logger.info(
                    "WebAuth: GitHub provider registered (client_id=%s…, secret from %s)",
                    client_id[:8], secret_source
                )
            elif pname == "google":
                client_id = prov_cfg.get("client_id", "").strip()
                client_secret, secret_source = _resolve_secret("google")
                if not client_id or not client_secret:
                    logger.error(
                        "WebAuth: Google provider missing client_id or client_secret — skipping. "
                        "Set BB_GOOGLE_CLIENT_SECRET or run: agentauth add google"
                    )
                    continue
                self._providers["google"] = make_auth(
                    "google",
                    client_id=client_id,
                    client_secret=client_secret,
                    allowed_emails=prov_cfg.get("allowed_emails", []),
                )
                logger.info(
                    "WebAuth: Google provider registered (client_id=%s…, secret from %s)",
                    client_id[:8], secret_source
                )
            else:
                logger.warning("WebAuth: unknown provider '%s' — skipping", pname)

        if not self._providers:
            logger.warning("WebAuth: mode=oauth but no providers loaded — web UI will be open")

    def is_enabled(self) -> bool:
        return self.mode == "oauth" and bool(self._providers)

    def get_provider(self, name: str) -> OAuthProvider | None:
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def make_state(self) -> str:
        return secrets.token_urlsafe(32)

    def sign_session(self, user_id: str, email: str, name: str, picture: str) -> str:
        assert self._signer, "Signer not initialized"
        return self._signer.dumps({
            "user_id": user_id,
            "email":   email,
            "name":    name,
            "picture": picture,
        })

    def verify_session(self, token: str) -> dict | None:
        """Return session payload dict or None if invalid/expired."""
        if not self._signer:
            return None
        try:
            return self._signer.loads(token, max_age=_SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_secret(name: str) -> tuple[str | None, str]:
    """Resolve via agentauth keychain first, then env var. Return (value, source).

    Returns:
        (secret_value, source_description) where source is "keychain", "env_var", or "not_found"
    """
    try:
        from agentauth.registry import get_token
        val = get_token(name)
        if val:
            return val, "keychain"
    except Exception as e:
        logger.debug("agentauth unavailable for '%s': %s", name, e)

    env_key = f"BB_{name.upper()}_TOKEN"
    val = os.environ.get(env_key)
    if val:
        return val, f"env_var ({env_key})"

    return None, "not_found"
