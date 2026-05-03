"""Auth subsystem bootstrap.

Builds:
  - MultiKeyAuthRegistry (always — runtime decides whether keys are required)
  - WebAuthManager        (always — runtime decides whether OAuth is enabled)

Top-level ``auth.enabled`` flag (default ``True``) short-circuits BOTH
the API-key middleware and the web-auth middleware when set to ``False``,
so BeigeBox runs wide open (single-user dev mode). The flag is read here
once and threaded into both subsystems.
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
    enabled = bool(auth_cfg.get("enabled", True))

    # Auth registry (multi-key, agentauth-backed) — reads auth.enabled itself
    auth_registry = MultiKeyAuthRegistry(auth_cfg)

    # Web UI OAuth shim (optional — requires itsdangerous). Pass the
    # top-level auth.enabled flag through so a single config switch
    # short-circuits both middlewares consistently.
    web_auth = WebAuthManager(auth_cfg.get("web_ui", {}), enabled=enabled)

    return AuthBundle(
        auth_registry=auth_registry,
        web_auth=web_auth,
    )


__all__ = ["AuthBundle", "build_auth"]
