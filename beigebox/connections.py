"""
BeigeBox Connections — encrypted credential store + HTTP dispatch.

Tokens are encrypted at rest with AES-256-GCM. The agent never sees a raw
token — it calls a connection by name, the registry injects Authorization
headers internally, and only the response body is returned.

Key storage (in priority order):
  1. BB_MASTER_KEY env var — 64 hex chars (32 bytes)
  2. ~/.bb/.key file       — auto-generated on first use, chmod 600

Credentials file: ~/.bb/connections.enc (AES-256-GCM encrypted JSON)

CLI usage:
  python -m beigebox.connections list
  python -m beigebox.connections add <name> --token <value>
  python -m beigebox.connections add <name> --token-env <ENV_VAR>
  python -m beigebox.connections remove <name>
  python -m beigebox.connections test <name>
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BB_DIR   = Path.home() / ".bb"
_KEY_FILE = _BB_DIR / ".key"
_CRED_FILE = _BB_DIR / "connections.enc"


# ─────────────────────────────────────────────────────────────────────────────
# Key management
# ─────────────────────────────────────────────────────────────────────────────

def _load_or_create_key() -> bytes:
    """Return 32-byte AES key. Never raises — creates key file on first use."""
    # 1. Env var
    env_key = os.environ.get("BB_MASTER_KEY", "")
    if env_key:
        try:
            key = bytes.fromhex(env_key)
            if len(key) == 32:
                return key
        except ValueError:
            pass
        raise ValueError("BB_MASTER_KEY must be 64 hex characters (32 bytes)")

    # 2. Key file
    _BB_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _KEY_FILE.exists():
        key = bytes.fromhex(_KEY_FILE.read_text().strip())
        if len(key) != 32:
            raise ValueError(f"Key file {_KEY_FILE} is corrupt (expected 32 bytes)")
        return key

    # 3. Generate new key
    from cryptography.hazmat.primitives import hashes  # noqa: F401 — just to check import
    import secrets
    key = secrets.token_bytes(32)
    _KEY_FILE.write_text(key.hex())
    _KEY_FILE.chmod(0o600)
    logger.info("Generated new master key at %s", _KEY_FILE)
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Encrypted store
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionStore:
    """
    Loads and saves the encrypted credentials file.
    Format on disk: 12-byte nonce || 16-byte tag || ciphertext (AES-256-GCM).
    In memory: plain dict {"github": {"token": "..."}, ...}
    """

    def __init__(self):
        self._key = _load_or_create_key()

    def load(self) -> dict[str, dict]:
        if not _CRED_FILE.exists():
            return {}
        raw = _CRED_FILE.read_bytes()
        try:
            return json.loads(self._decrypt(raw))
        except Exception as e:
            raise RuntimeError(f"Failed to decrypt {_CRED_FILE}: {e}") from e

    def save(self, data: dict[str, dict]) -> None:
        _BB_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        encrypted = self._encrypt(json.dumps(data).encode())
        _CRED_FILE.write_bytes(encrypted)
        _CRED_FILE.chmod(0o600)

    def _encrypt(self, plaintext: bytes) -> bytes:
        import os as _os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = _os.urandom(12)
        ct    = AESGCM(self._key).encrypt(nonce, plaintext, None)
        return nonce + ct   # tag is appended by AESGCM (last 16 bytes of ct)

    def _decrypt(self, data: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, ct = data[:12], data[12:]
        return AESGCM(self._key).decrypt(nonce, ct, None)


# ─────────────────────────────────────────────────────────────────────────────
# Connection registry
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionRegistry:
    """
    Resolves named connections from config + encrypted store, makes credentialed
    HTTP calls. The agent calls connection.call — it never receives a token.

    Config structure (config.yaml, no secrets):
      connections:
        github:
          type: bearer
          base_url: https://api.github.com
          allowed_paths:
            - /user/**
            - /repos/**
        my_service:
          type: bearer
          base_url: https://api.myservice.com
          allowed_paths:
            - /v1/**
    """

    def __init__(self, cfg: dict):
        self._cfg   = cfg         # connection metadata from config.yaml
        self._store = ConnectionStore()

    def list_connections(self) -> list[str]:
        creds = self._store.load()
        return sorted(set(list(self._cfg.keys()) + list(creds.keys())))

    def add_token(self, name: str, token: str) -> None:
        """Add or update a token for a named connection."""
        creds = self._store.load()
        creds[name] = {"token": token}
        self._store.save(creds)
        logger.info("Stored token for connection: %s", name)

    def remove(self, name: str) -> None:
        creds = self._store.load()
        if name not in creds:
            raise KeyError(f"No credentials stored for: {name}")
        del creds[name]
        self._store.save(creds)

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
        Raises on unknown connection, disallowed path, or missing credentials.
        """
        conn_cfg = self._cfg.get(name)
        if conn_cfg is None:
            raise ValueError(
                f"Unknown connection '{name}'. "
                f"Add it to config.yaml connections: section first. "
                f"Known: {list(self._cfg.keys())}"
            )

        # Enforce path allowlist
        allowed = conn_cfg.get("allowed_paths", ["/**"])
        if not any(fnmatch.fnmatch(path, pat) for pat in allowed):
            raise PermissionError(
                f"Path '{path}' is not in the allowlist for connection '{name}'. "
                f"Allowed: {allowed}"
            )

        # Resolve token
        creds = self._store.load()
        conn_creds = creds.get(name)
        if not conn_creds or not conn_creds.get("token"):
            raise RuntimeError(
                f"No token stored for connection '{name}'. "
                f"Run: python -m beigebox.connections add {name} --token <value>"
            )
        token = conn_creds["token"]

        base_url = conn_cfg.get("base_url", "").rstrip("/")
        url      = base_url + path
        method   = method.upper()

        req_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            req_headers.update(headers)

        logger.info(
            "connection.call: %s %s %s (path=%s)",
            name, method, url.split("?")[0], path
        )

        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, headers=req_headers, json=body)

        # Log without token
        logger.info(
            "connection.call response: %s %s → %d (%d bytes)",
            name, path, resp.status_code, len(resp.content)
        )

        return {"status": resp.status_code, "body": resp.text}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton (lazy)
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
# CLI — python -m beigebox.connections
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    import argparse, getpass, sys

    parser = argparse.ArgumentParser(
        prog="python -m beigebox.connections",
        description="Manage BeigeBox encrypted connection credentials",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all connections")

    p_add = sub.add_parser("add", help="Add or update a connection token")
    p_add.add_argument("name", help="Connection name (must match config.yaml)")
    grp = p_add.add_mutually_exclusive_group()
    grp.add_argument("--token", help="Token value (use --prompt to avoid shell history)")
    grp.add_argument("--token-env", metavar="ENV", help="Read token from environment variable")
    grp.add_argument("--prompt", action="store_true", help="Prompt for token securely")

    p_rm = sub.add_parser("remove", help="Remove a stored token")
    p_rm.add_argument("name")

    p_test = sub.add_parser("test", help="Test a connection with a GET request")
    p_test.add_argument("name")
    p_test.add_argument("--path", default="/", help="Path to test (default: /)")

    args = parser.parse_args()

    from beigebox.config import get_config
    cfg = get_config().get("connections", {})
    reg = ConnectionRegistry(cfg)

    if args.cmd == "list":
        names = reg.list_connections()
        if not names:
            print("No connections configured.")
        else:
            store = ConnectionStore()
            creds = store.load()
            print(f"{'NAME':<20} {'CONFIG':<8} {'TOKEN'}")
            for n in names:
                has_cfg   = "yes" if n in cfg   else "no"
                has_token = "stored" if n in creds and creds[n].get("token") else "MISSING"
                print(f"{n:<20} {has_cfg:<8} {has_token}")

    elif args.cmd == "add":
        if args.token:
            token = args.token
        elif args.token_env:
            token = os.environ.get(args.token_env)
            if not token:
                print(f"Error: env var {args.token_env} is not set", file=sys.stderr)
                sys.exit(1)
        elif args.prompt:
            token = getpass.getpass(f"Token for '{args.name}': ")
        else:
            token = getpass.getpass(f"Token for '{args.name}': ")
        reg.add_token(args.name, token)
        print(f"Token stored for: {args.name}")

    elif args.cmd == "remove":
        try:
            reg.remove(args.name)
            print(f"Removed: {args.name}")
        except KeyError as e:
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


if __name__ == "__main__":
    _cli()
