"""
BeigeBox Security MCP — a separate MCP endpoint exposing offensive-security
tool wrappers (port scanners, vuln scanners, web fuzzers, subdomain enum, …).

Mounted at POST /pen-mcp by main.py when enabled. Uses the same JSON-RPC
McpServer implementation as /mcp but with its own tool registry so security
tooling stays out of the default surface.

Inspired by HexStrike AI (MIT, https://github.com/0x4m4/hexstrike-ai) — we
re-implement the *nix wrappers cleanly using argv-list subprocess (no shell
string concat / no f-string injection) and gracefully handle missing binaries
so the server stays usable even when only some tools are installed.
"""
from beigebox.security_mcp.registry import SecurityToolRegistry, build_default_registry

__all__ = ["SecurityToolRegistry", "build_default_registry"]
