"""Auth subsystem bootstrap.

Builds:
  - MultiKeyAuthRegistry (always — runtime decides whether keys are required)
  - WebAuthManager        (always — runtime decides whether OAuth is enabled)
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


def build_auth(cfg: dict, users: Any) -> AuthBundle:  # noqa: ARG001 — users reserved for future use
    auth_cfg = cfg.get("auth", {})

    # Auth registry (multi-key, agentauth-backed)
    auth_registry = MultiKeyAuthRegistry(auth_cfg)

    # Web UI OAuth shim (optional — requires itsdangerous)
    web_auth = WebAuthManager(auth_cfg.get("web_ui", {}))

    return AuthBundle(
        auth_registry=auth_registry,
        web_auth=web_auth,
    )


__all__ = ["AuthBundle", "build_auth"]
