"""Auth subsystem bootstrap.

Builds:
  - MultiKeyAuthRegistry (always — runtime decides whether keys are required)
  - WebAuthManager        (always — runtime decides whether OAuth is enabled)
  - SimplePasswordAuth    (only if ``auth.mode == "password"``)

The password-auth path needs the users repo from storage, so this module
takes ``users`` as a positional argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from beigebox.auth import MultiKeyAuthRegistry
from beigebox.web_auth import WebAuthManager


@dataclass
class AuthBundle:
    auth_registry: MultiKeyAuthRegistry
    web_auth: WebAuthManager
    password_auth: Any  # SimplePasswordAuth | None


def build_auth(cfg: dict, users: Any) -> AuthBundle:
    auth_cfg = cfg.get("auth", {})

    # Auth registry (multi-key, agentauth-backed)
    auth_registry = MultiKeyAuthRegistry(auth_cfg)

    # Web UI OAuth shim (optional — requires itsdangerous)
    web_auth = WebAuthManager(auth_cfg.get("web_ui", {}))

    # Simple password auth (optional — for single-tenant SaaS)
    password_auth = None
    if auth_cfg.get("mode") == "password":
        from beigebox.web_auth import SimplePasswordAuth
        password_auth = SimplePasswordAuth(users) if users else None

    return AuthBundle(
        auth_registry=auth_registry,
        web_auth=web_auth,
        password_auth=password_auth,
    )


__all__ = ["AuthBundle", "build_auth"]
