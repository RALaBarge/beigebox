"""
Shared retry utilities — used by backends and observability egress.

Extracted from beigebox/backends/retry_wrapper.py so that the same
backoff + retryability logic can be reused without duplicating it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetryConfig:
    """Parameters that control retry / backoff behaviour."""

    max_retries: int = 2
    backoff_base: float = 1.5
    backoff_max: float = 10.0


def is_retryable(status_code: int) -> bool:
    """Return True if *status_code* represents a transient, retryable error.

    Retryable:
      - 429  Too Many Requests (rate-limited)
      - 500  Internal Server Error
      - 502  Bad Gateway
      - 503  Service Unavailable
      - 504  Gateway Timeout

    Non-retryable (permanent):
      - 400  Bad Request
      - 401 / 403  Auth / permission errors
      - 404  Not Found
      - 501  Not Implemented
    """
    return status_code in (429, 500, 502, 503, 504)


def backoff_seconds(
    attempt: int,
    retry_after: float | None = None,
    config: RetryConfig | None = None,
) -> float:
    """Calculate the number of seconds to wait before *attempt*.

    ``attempt`` should be passed as ``attempt + 1`` from the retry loop so
    the first retry gets ``base ** 1`` rather than ``base ** 0``.

    If *retry_after* is provided (parsed from a Retry-After / x-ratelimit-*
    header) it is used directly, capped at *config.backoff_max*.
    """
    if config is None:
        config = RetryConfig()
    if retry_after is not None and retry_after > 0:
        return min(retry_after, config.backoff_max)
    delay = config.backoff_base ** attempt
    return min(delay, config.backoff_max)
