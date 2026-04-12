"""
Key management for memory integrity validation.

Supports three key sources:
1. Environment variable (BEIGEBOX_MEMORY_KEY=base64:<key>)
2. File on disk (~/.beigebox/memory.key, perms 0600)
3. System keyring (future: HSM/KMS integration)

Also supports key versioning for rotation scenarios.
"""

import os
import base64
import secrets
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MEMORY_KEY_ENV = "BEIGEBOX_MEMORY_KEY"
MEMORY_KEY_FILE = Path.home() / ".beigebox" / "memory.key"


class KeyManager:
    """Load and manage integrity validation keys."""

    @staticmethod
    def load_key(
        key_source: str = "env",
        key_path: Optional[str] = None,
        dev_mode: bool = False
    ) -> Optional[bytes]:
        """
        Load integrity validation key from configured source.

        Args:
            key_source: One of "env" (environment), "file" (disk), or "keyring"
            key_path: Custom path for file-based keys (overrides ~/.beigebox/memory.key)
            dev_mode: If True, gracefully fall back to None if key missing (no exceptions)

        Returns:
            32-byte secret key, or None if dev_mode=True and key not found

        Raises:
            ValueError: If key is malformed or not found (when dev_mode=False)
            RuntimeError: If key source is unavailable
        """
        if key_source == "env":
            return KeyManager._load_from_env(dev_mode)
        elif key_source == "file":
            return KeyManager._load_from_file(key_path or str(MEMORY_KEY_FILE), dev_mode)
        elif key_source == "keyring":
            return KeyManager._load_from_keyring(dev_mode)
        else:
            raise ValueError(f"Unknown key source: {key_source}")

    @staticmethod
    def _load_from_env(dev_mode: bool = False) -> Optional[bytes]:
        """
        Load key from BEIGEBOX_MEMORY_KEY environment variable.

        Format: BEIGEBOX_MEMORY_KEY=base64:<base64-encoded-32-bytes>
        Example: BEIGEBOX_MEMORY_KEY=base64:abcd1234...

        Args:
            dev_mode: If True, return None if env var not set

        Returns:
            32-byte key, or None in dev mode

        Raises:
            ValueError: If env var is malformed
        """
        key_str = os.environ.get(MEMORY_KEY_ENV)
        if not key_str:
            if dev_mode:
                logger.debug("Memory key not set in environment (dev mode)")
                return None
            raise ValueError(f"{MEMORY_KEY_ENV} not set")

        if not key_str.startswith("base64:"):
            raise ValueError(f"{MEMORY_KEY_ENV} must start with 'base64:'")

        try:
            encoded = key_str[7:]  # strip "base64:" prefix
            key_bytes = base64.b64decode(encoded)
            if len(key_bytes) != 32:
                raise ValueError(f"Key must be 32 bytes, got {len(key_bytes)}")
            logger.info("Loaded integrity key from environment")
            return key_bytes
        except Exception as e:
            raise ValueError(f"Failed to decode BEIGEBOX_MEMORY_KEY: {e}")

    @staticmethod
    def _load_from_file(key_path: str, dev_mode: bool = False) -> Optional[bytes]:
        """
        Load key from file at ~/.beigebox/memory.key or custom path.

        File should contain exactly 32 bytes (binary) or 64 hex characters (text).
        File permissions should be 0600 for security.

        Args:
            key_path: Path to key file
            dev_mode: If True, return None if file not found

        Returns:
            32-byte key, or None in dev mode

        Raises:
            ValueError: If key file is malformed
        """
        path = Path(key_path)

        if not path.exists():
            if dev_mode:
                logger.debug("Memory key file not found at %s (dev mode)", key_path)
                return None
            raise ValueError(f"Key file not found: {key_path}")

        # Check permissions (warn if world-readable)
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:  # Check if group or others have any permissions
            logger.warning(
                "Key file has insecure permissions (%o). Should be 0600. "
                "Run: chmod 600 %s",
                mode, key_path
            )

        try:
            with open(path, "rb") as f:
                data = f.read()

            # Try binary (32 bytes)
            if len(data) == 32:
                logger.info("Loaded integrity key from file (binary format)")
                return data

            # Try hex-encoded (64 chars)
            if len(data) == 64:
                try:
                    key_bytes = bytes.fromhex(data.decode("ascii").strip())
                    if len(key_bytes) == 32:
                        logger.info("Loaded integrity key from file (hex format)")
                        return key_bytes
                except (ValueError, UnicodeDecodeError):
                    pass

            raise ValueError(
                f"Key file must be 32 bytes (binary) or 64 hex chars, got {len(data)}"
            )
        except Exception as e:
            raise ValueError(f"Failed to read key file {key_path}: {e}")

    @staticmethod
    def _load_from_keyring(dev_mode: bool = False) -> Optional[bytes]:
        """
        Load key from system keyring (placeholder for future integration).

        Currently not implemented. Future integration targets:
        - macOS: Keychain
        - Linux: SecretService (via python-keyring)
        - Windows: DPAPI
        - Cloud: AWS Secrets Manager, Azure Key Vault, HashiCorp Vault

        Args:
            dev_mode: If True, return None instead of raising

        Raises:
            NotImplementedError: Always, until integration is added
        """
        logger.warning("Keyring source not yet implemented")
        if dev_mode:
            return None
        raise NotImplementedError("Keyring key source not yet implemented")

    @staticmethod
    def generate_key() -> bytes:
        """
        Generate a new 32-byte cryptographically-secure random key.

        Uses secrets.token_bytes(32) for secure randomness.

        Returns:
            32-byte key suitable for HMAC-SHA256
        """
        key = secrets.token_bytes(32)
        logger.info("Generated new integrity key")
        return key

    @staticmethod
    def save_key_to_file(key: bytes, key_path: str) -> None:
        """
        Save a key to disk with secure permissions.

        Creates directory if needed. Sets permissions to 0600 (owner read/write only).

        Args:
            key: 32-byte key to save
            key_path: Where to save the key file

        Raises:
            ValueError: If key is not 32 bytes
        """
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("Key must be exactly 32 bytes")

        path = Path(key_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write as hex for human readability (easier to copy/paste in configs)
        with open(path, "w") as f:
            f.write(key.hex())

        # Restrict permissions to owner only
        path.chmod(0o600)
        logger.info("Saved integrity key to %s (permissions 0600)", key_path)

    @staticmethod
    def format_env_var(key: bytes) -> str:
        """
        Format a key for use as an environment variable.

        Returns:
            String in format "base64:<base64-encoded-key>"
        """
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("Key must be exactly 32 bytes")
        encoded = base64.b64encode(key).decode("ascii")
        return f"base64:{encoded}"


def generate_and_save_key(output_path: Optional[str] = None) -> bytes:
    """
    CLI helper: Generate a new key and save it to disk.

    Args:
        output_path: Where to save key (defaults to ~/.beigebox/memory.key)

    Returns:
        The generated key (32 bytes)
    """
    key = KeyManager.generate_key()
    path = output_path or str(MEMORY_KEY_FILE)
    KeyManager.save_key_to_file(key, path)
    env_var = KeyManager.format_env_var(key)
    print(f"Key saved to: {path}")
    print(f"Environment variable: export {MEMORY_KEY_ENV}='{env_var}'")
    return key
