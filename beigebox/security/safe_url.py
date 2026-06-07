"""
URL validation for outbound HTTP/WebSocket calls.

Untrusted URLs from config files or request bodies must not turn into SSRF.
This module provides one general validator (`validate_url`) and a small
set of preset wrappers for common BeigeBox call sites.

Usage
-----

    from beigebox.security.safe_url import validate_url, SsrfRefusedError

    try:
        url = validate_url(
            user_url,
            allow_schemes={"http", "https"},
            allow_hosts={"api.openai.com", "openrouter.ai"},
        )
    except SsrfRefusedError:
        return 403

Preset wrappers (preferred):

    from beigebox.security.safe_url import (
        validate_backend_url,        # LLM upstream — private permitted
        validate_browser_ws_url,     # CDP WebSocket — private permitted
        validate_webhook_url,        # outbound webhook — private denied
        validate_remote_probe_url,   # api_probe / network audit — private denied
    )

The validator does NOT perform DNS resolution. A motivated attacker can
still rebind a public hostname to a private IP between validation and
fetch ("DNS rebinding"). For high-stakes call sites, additionally pin the
resolved IP at fetch time. The validators here are the cheap front line.
"""

from __future__ import annotations

import ipaddress
from typing import Callable
from urllib.parse import urlparse


class SsrfRefusedError(Exception):
    """Raised when a URL fails SSRF validation."""


# Hostnames that resolve to loopback / private space without DNS. Hardcoded
# because the validator does not perform DNS — these are the names that
# would fool a "no IP literal in private space" check on their face.
_LOOPBACK_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
})


def _is_blocked_ip_literal(host: str) -> bool:
    """True if host is an IP literal in private / loopback / link-local /
    multicast / reserved space. Returns False for hostnames (no DNS)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(
    url: str,
    *,
    allow_schemes: set[str] | frozenset[str] = frozenset({"http", "https"}),
    allow_hosts: Callable[[str], bool] | set[str] | None = None,
    deny_private: bool = True,
) -> str:
    """
    Validate that `url` is safe to fetch.

    Args:
        url: Candidate URL string.
        allow_schemes: Permitted URL schemes (e.g. {"http","https"} or
            {"ws","wss"}).
        allow_hosts: Optional host allow-list. Set of literal hostnames,
            or a callable taking a host and returning True if permitted,
            or None to skip the host-name check.
        deny_private: If True, reject private/loopback/link-local IP
            literals. Set False for backends that legitimately run on
            localhost / LAN.

    Returns:
        The original URL string, unchanged, on success.

    Raises:
        SsrfRefusedError: on any failed check.
    """
    if not isinstance(url, str) or not url:
        raise SsrfRefusedError(
            f"URL must be a non-empty string, got {type(url).__name__}"
        )

    parsed = urlparse(url)

    if parsed.scheme.lower() not in allow_schemes:
        raise SsrfRefusedError(
            f"scheme {parsed.scheme!r} not allowed; permitted: {sorted(allow_schemes)}"
        )

    if parsed.username or parsed.password:
        raise SsrfRefusedError("URLs with embedded credentials are not allowed")

    host = parsed.hostname
    if not host:
        raise SsrfRefusedError(f"URL {url!r} has no host component")

    if deny_private:
        if _is_blocked_ip_literal(host):
            raise SsrfRefusedError(f"private/loopback host {host!r} not allowed")
        if host.lower() in _LOOPBACK_HOSTNAMES:
            raise SsrfRefusedError(f"private/loopback host {host!r} not allowed")

    if allow_hosts is not None:
        if isinstance(allow_hosts, (set, frozenset)):
            ok = host in allow_hosts
        else:
            ok = bool(allow_hosts(host))
        if not ok:
            raise SsrfRefusedError(f"host {host!r} not in allow-list")

    return url


# ---------------------------------------------------------------------------
# Preset validators per call site
# ---------------------------------------------------------------------------


def validate_backend_url(url: str) -> str:
    """LLM backend URLs (Ollama, OpenRouter, MLX, custom plugins).

    Backends may legitimately point at private/loopback addresses (Ollama
    on localhost, MLX on a LAN box). The operator declares them in
    config.yaml on purpose, so private addresses are permitted here.
    """
    return validate_url(url, allow_schemes={"http", "https"}, deny_private=False)


def validate_browser_ws_url(url: str) -> str:
    """Chrome DevTools Protocol (CDP) WebSocket URLs.

    Browser is local. ws/wss only, private permitted.
    """
    return validate_url(url, allow_schemes={"ws", "wss"}, deny_private=False)


def validate_webhook_url(url: str) -> str:
    """Outbound webhooks (egress observability sinks, alerting hooks)."""
    return validate_url(url, allow_schemes={"http", "https"}, deny_private=True)


def validate_remote_probe_url(url: str) -> str:
    """For api_probe and network-audit endpoints that reach arbitrary hosts.

    Tightest preset: http/https only, no private hosts. Call sites MUST
    additionally require admin auth — this validator is the second line.
    """
    return validate_url(url, allow_schemes={"http", "https"}, deny_private=True)
