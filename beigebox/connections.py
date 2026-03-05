"""
Backward-compat shim — connection logic has moved to the agentauth package.

  pip install agentauth   (or pip install -e /path/to/agentauth)

This module re-exports the public API so existing imports keep working.
"""
from agentauth.registry import (  # noqa: F401
    ConnectionRegistry,
    get_registry,
    get_token,
    set_token,
    delete_token,
    token_source,
    TIER_READ,
    TIER_WRITE,
    TIER_SEND,
    TIER_NEVER,
)
