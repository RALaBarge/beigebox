"""
Multi-key API auth registry for BeigeBox.

Resolves named API keys from agentauth (OS keychain / BB_<NAME>_TOKEN env var).
Falls back to the legacy single-key auth.api_key mode for backwards compatibility.

Config (config.yaml):
  auth:
    api_key: ""          # legacy single key — still works
    keys:
      - name: openwebui
        allowed_models: ["*"]
        allowed_endpoints: ["*"]
      - name: readonly-client
        allowed_models: ["llama3.2", "qwen3:*"]
        allowed_endpoints: ["/v1/chat/completions", "/v1/models"]
        rate_limit_rpm: 60

Token storage (per named key):
  agentauth add <name>           # stores in OS keychain
  BB_<NAME>_TOKEN=...            # env var fallback for headless/Docker

Tier enforcement for MCP / operator calls is handled separately by
the ConnectionTool / agentauth registry — this module only governs
inbound requests to BeigeBox's own API.
"""
from __future__ import annotations

import fnmatch
import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class KeyMeta:
    name: str
    allowed_models: list[str] = field(default_factory=lambda: ["*"])
    allowed_endpoints: list[str] = field(default_factory=lambda: ["*"])
    rate_limit_rpm: int = 0  # 0 = unlimited
    # Admin keys can hit endpoints that mutate code, reload WASM, or otherwise
    # affect the running process. Non-admin keys (default) get 403 from those
    # endpoints even if endpoint ACL would otherwise allow them.
    admin: bool = False


class MultiKeyAuthRegistry:
    """
    Resolves named API keys from agentauth keychain/env.
    Enforces per-key model ACLs, endpoint ACLs, and rate limits.

    Empty token map = auth disabled (all requests pass through).
    """

    def __init__(self, auth_cfg: dict):
        self._token_map: dict[str, KeyMeta] = {}   # token → meta
        self._rate_windows: dict[str, deque] = {}   # key name → timestamps

        # Legacy single key (backwards compat — always a wildcard key, and
        # admin since it's the operator's own key in single-key deployments).
        legacy_key = auth_cfg.get("api_key", "").strip()
        if legacy_key:
            self._token_map[legacy_key] = KeyMeta(name="default", admin=True)
            logger.info("Auth: legacy api_key loaded (admin)")

        # Named keys via agentauth
        for key_cfg in auth_cfg.get("keys", []):
            name = key_cfg.get("name", "").strip()
            if not name:
                continue
            token = _resolve_token(name)
            if not token:
                logger.warning(
                    "Auth: no token found for key '%s' — "
                    "run: agentauth add %s  or  set BB_%s_TOKEN",
                    name, name, name.upper(),
                )
                continue
            meta = KeyMeta(
                name=name,
                allowed_models=key_cfg.get("allowed_models", ["*"]),
                allowed_endpoints=key_cfg.get("allowed_endpoints", ["*"]),
                rate_limit_rpm=int(key_cfg.get("rate_limit_rpm", 0)),
                admin=bool(key_cfg.get("admin", False)),
            )
            self._token_map[token] = meta
            logger.info(
                "Auth: key '%s' loaded (models=%s, endpoints=%s, rpm=%s, admin=%s)",
                name, meta.allowed_models, meta.allowed_endpoints, meta.rate_limit_rpm,
                meta.admin,
            )

        if self._token_map:
            logger.info("Auth: %d key(s) active", len(self._token_map))
        else:
            logger.info("Auth: disabled (no keys configured)")

    def is_enabled(self) -> bool:
        return bool(self._token_map)

    def validate(self, token: str) -> KeyMeta | None:
        """Return KeyMeta for a valid token, or None if invalid."""
        return self._token_map.get(token)

    def check_rate_limit(self, meta: KeyMeta) -> bool:
        """
        Rolling 60-second window rate limiter.
        Returns True if request is within limit, False if exceeded.
        Records the request on True.

        Uses a deque of monotonic timestamps. On each call we evict
        timestamps older than 60s from the front (sliding window), then
        check if the remaining count is still under the RPM limit.
        """
        if meta.rate_limit_rpm <= 0:
            return True
        now = time.monotonic()
        window = self._rate_windows.setdefault(meta.name, deque())
        # Evict timestamps that have fallen outside the 60-second window
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= meta.rate_limit_rpm:
            return False
        window.append(now)
        return True

    def check_endpoint(self, meta: KeyMeta, path: str) -> bool:
        """True if path matches any pattern in allowed_endpoints."""
        return any(fnmatch.fnmatch(path, pat) for pat in meta.allowed_endpoints)

    def check_model(self, meta: KeyMeta, model: str) -> bool:
        """True if model matches any pattern in allowed_models."""
        return any(fnmatch.fnmatch(model, pat) for pat in meta.allowed_models)


def _resolve_token(name: str) -> str | None:
    """Resolve token via agentauth keychain, then BB_<NAME>_TOKEN env var.

    Resolution order:
      1. agentauth OS keychain (works on Linux/macOS with a keyring backend)
      2. BB_<NAME>_TOKEN environment variable (Docker/headless fallback)
    If neither has a value, returns None and the key is skipped at startup.
    """
    try:
        from agentauth.registry import get_token
        token = get_token(name)
        if token:
            return token
    except Exception as e:
        logger.debug("agentauth unavailable for key '%s': %s", name, e)

    import os
    return os.environ.get(f"BB_{name.upper()}_TOKEN")
