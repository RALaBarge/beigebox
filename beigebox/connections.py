"""
BeigeBox Connections — OS keychain credential store + authenticated HTTP dispatch.

Tokens are stored in the OS native keychain via python-keyring:
  - Linux:   gnome-keyring / KWallet (SecretService DBus API)
  - macOS:   Keychain
  - Windows: Windows Credential Manager

The agent never sees a raw token — it calls a connection by name, the registry
injects Authorization headers internally, and only the response body is returned.

Fallback for headless/server deployments:
  Set BB_<NAME>_TOKEN env vars — keyring is tried first, env vars second.

Requires:
  pip install keyring secretstorage   # Linux
  pip install keyring                 # macOS / Windows (keyring bundled)

CLI:
  python -m beigebox.connections list
  python -m beigebox.connections add <name>          # prompts securely
  python -m beigebox.connections add <name> --env    # reads BB_<NAME>_TOKEN from env
  python -m beigebox.connections remove <name>
  python -m beigebox.connections test <name> --path /endpoint
  python -m beigebox.connections setup               # print setup instructions
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "beigebox"


# ─────────────────────────────────────────────────────────────────────────────
# Token resolution — keyring first, env var fallback
# ─────────────────────────────────────────────────────────────────────────────

def _get_token(name: str) -> str | None:
    # 1. OS keychain
    try:
        import keyring
        token = keyring.get_password(_KEYRING_SERVICE, name)
        if token:
            return token
    except Exception as e:
        logger.debug("keyring unavailable: %s", e)

    # 2. Env var fallback (headless/server deployments)
    return os.environ.get(f"BB_{name.upper()}_TOKEN")


def _set_token(name: str, token: str) -> None:
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, name, token)
        logger.info("Stored token for '%s' in OS keychain", name)
        return
    except Exception as e:
        raise RuntimeError(
            f"Could not store token in OS keychain: {e}\n"
            f"On Linux, install: pip install secretstorage\n"
            f"On headless systems, set BB_{name.upper()}_TOKEN env var instead."
        ) from e


def _delete_token(name: str) -> None:
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, name)
    except Exception as e:
        raise RuntimeError(f"Could not remove token from keychain: {e}") from e


def _token_source(name: str) -> str:
    """Return human-readable description of where the token is coming from."""
    try:
        import keyring
        if keyring.get_password(_KEYRING_SERVICE, name):
            return "keychain"
    except Exception:
        pass
    if os.environ.get(f"BB_{name.upper()}_TOKEN"):
        return "env var"
    return "MISSING"


# ─────────────────────────────────────────────────────────────────────────────
# Connection registry
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionRegistry:
    """
    Resolves named connections from config, makes credentialed HTTP calls.

    Config structure (config.yaml — no secrets, metadata only):
      connections:
        github:
          type: bearer
          base_url: https://api.github.com
          allowed_paths:
            - /user/**
            - /repos/**
        openrouter:
          type: bearer
          base_url: https://openrouter.ai/api/v1
          allowed_paths:
            - /models
            - /chat/**
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg

    def list_connections(self) -> list[tuple[str, str]]:
        return [(name, _token_source(name)) for name in sorted(self._cfg.keys())]

    def call(
        self,
        name: str,
        method: str,
        path: str,
        body: Any = None,
        headers: dict | None = None,
        timeout: float = 15.0,
    ) -> dict:
        """
        Make a credentialed HTTP request. Returns {"status": int, "body": str}.
        Raises on unknown connection, disallowed path, or missing token.
        Token is injected internally — never returned or logged.
        """
        conn_cfg = self._cfg.get(name)
        if conn_cfg is None:
            raise ValueError(
                f"Unknown connection '{name}'. "
                f"Add it to config.yaml connections: section. "
                f"Known: {sorted(self._cfg.keys())}"
            )

        # Enforce path allowlist
        allowed = conn_cfg.get("allowed_paths", ["/**"])
        if not any(fnmatch.fnmatch(path, pat) for pat in allowed):
            raise PermissionError(
                f"Path '{path}' not in the allowlist for '{name}'. "
                f"Allowed patterns: {allowed}"
            )

        # Resolve token — never logged
        token = _get_token(name)
        if not token:
            raise RuntimeError(
                f"No token found for connection '{name}'. "
                f"Run: python -m beigebox.connections add {name}"
            )

        base_url    = conn_cfg.get("base_url", "").rstrip("/")
        url         = base_url + path
        req_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            req_headers.update(headers)

        logger.info("connection.call: %s %s %s", name, method.upper(), path)

        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method.upper(), url, headers=req_headers, json=body)

        logger.info(
            "connection.call response: %s %s → %d (%d bytes)",
            name, path, resp.status_code, len(resp.content)
        )

        return {"status": resp.status_code, "body": resp.text}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_registry: ConnectionRegistry | None = None


def get_registry(cfg: dict | None = None) -> ConnectionRegistry:
    global _registry
    if _registry is None:
        if cfg is None:
            from beigebox.config import get_config
            cfg = get_config().get("connections", {})
        _registry = ConnectionRegistry(cfg)
    return _registry


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m beigebox.connections",
        description="Manage BeigeBox API connection credentials",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List connections and token status")

    p_add = sub.add_parser("add", help="Store a token in the OS keychain")
    p_add.add_argument("name", help="Connection name (must match config.yaml)")
    p_add.add_argument(
        "--env", action="store_true",
        help="Read token from BB_<NAME>_TOKEN env var instead of prompting"
    )

    p_rm = sub.add_parser("remove", help="Remove a token from the OS keychain")
    p_rm.add_argument("name")

    p_test = sub.add_parser("test", help="Test a connection with a GET request")
    p_test.add_argument("name")
    p_test.add_argument("--path", default="/", help="Path to test (default: /)")

    sub.add_parser("setup", help="Print setup instructions")

    args = parser.parse_args()

    from beigebox.config import get_config
    cfg = get_config().get("connections", {})
    reg = ConnectionRegistry(cfg)

    if args.cmd == "list":
        connections = reg.list_connections()
        if not connections:
            print("No connections configured in config.yaml.")
        else:
            print(f"{'NAME':<20} {'TOKEN SOURCE'}")
            print("─" * 35)
            for name, source in connections:
                print(f"{name:<20} {source}")

    elif args.cmd == "add":
        if args.env:
            env_var = f"BB_{args.name.upper()}_TOKEN"
            token = os.environ.get(env_var)
            if not token:
                print(f"Error: {env_var} is not set", file=sys.stderr)
                sys.exit(1)
        else:
            token = getpass.getpass(f"Token for '{args.name}' (input hidden): ")
        try:
            _set_token(args.name, token)
            print(f"Token stored in OS keychain for: {args.name}")
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "remove":
        try:
            _delete_token(args.name)
            print(f"Removed keychain token for: {args.name}")
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "test":
        try:
            result = reg.call(args.name, "GET", args.path)
            print(f"Status: {result['status']}")
            print(result["body"][:500])
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "setup":
        print("""
BeigeBox Connections Setup
──────────────────────────
Tokens are stored in your OS native keychain (gnome-keyring on Linux,
Keychain on macOS, Credential Manager on Windows). Nothing is written
to disk in plaintext.

1. Install keyring support:
   Linux:          pip install keyring secretstorage
   macOS/Windows:  pip install keyring

2. Add a connection to config.yaml (no tokens here — metadata only):
   connections:
     github:
       type: bearer
       base_url: https://api.github.com
       allowed_paths:
         - /user/**
         - /repos/**

3. Store the token securely (prompts with hidden input):
   python -m beigebox.connections add github

4. Verify:
   python -m beigebox.connections list
   python -m beigebox.connections test github --path /user

Headless/server deployments (no keychain available):
   Set BB_<NAME>_TOKEN environment variables — keyring is tried first,
   env vars are the fallback.
""")


if __name__ == "__main__":
    _cli()
