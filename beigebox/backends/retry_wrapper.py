"""
Retry wrapper for backends with exponential backoff.

Wraps any backend to add retry logic for transient errors:
- 429: Rate limited
- 5xx: Server errors (except 501 Not Implemented — permanent)
- Stall: stream produces no tokens for stream_stall_timeout_seconds (config: advanced.stream_stall_timeout_seconds)

Non-retried errors (permanent):
- 400: Bad request
- 401, 403: Auth/permission errors
- 404: Not found (always permanent — wrong model ID, data policy block, etc.)
- 501: Not implemented

On stream exhaustion the exception is re-raised so the MultiBackendRouter
can fall through to the next backend rather than silently injecting error
text into the response stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx

from beigebox.backends.base import BaseBackend, BackendResponse
from beigebox.utils.retry import RetryConfig, is_retryable, backoff_seconds as _backoff_seconds

logger = logging.getLogger(__name__)


class StreamStallError(Exception):
    """Raised when a streaming backend produces no data within the stall timeout."""


async def _stall_guarded(aiter, timeout_secs: float):
    """
    Yield items from an async iterator.
    Raises StreamStallError if no item arrives within timeout_secs.

    Per-item timeout (not total) — each received token resets the clock.
    This catches a backend that goes silent mid-response while the
    connection is still open, which wouldn't be detected by httpx's
    overall request timeout.
    """
    while True:
        try:
            item = await asyncio.wait_for(aiter.__anext__(), timeout=timeout_secs)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise StreamStallError(f"Stream stalled: no data for {timeout_secs:.0f}s")
        yield item


class RetryableBackendWrapper:
    """
    Wraps any backend with exponential backoff retry logic.

    Transparently adds retry handling for transient errors.
    Maintains full compatibility with router interface.
    """

    def __init__(
        self,
        backend: BaseBackend,
        max_retries: int = 2,
        backoff_base: float = 1.5,
        backoff_max: float = 10.0,
    ):
        self.backend = backend
        self._retry_config = RetryConfig(
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        # Keep flat attributes for external callers that read them directly
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

        # Expose backend properties for router compatibility
        self.name = backend.name
        self.url = backend.url
        self.timeout = backend.timeout
        self.priority = backend.priority
        self._available_models = backend._available_models

    def _is_retryable(self, status_code: int) -> bool:
        """Determine if a failure is retryable (transient)."""
        return is_retryable(status_code)

    def _backoff_seconds(self, attempt: int, retry_after: float | None = None) -> float:
        """Calculate backoff time for attempt N, respecting Retry-After if present."""
        return _backoff_seconds(attempt, retry_after, self._retry_config)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        """Parse Retry-After header (seconds or HTTP-date). Returns seconds or None."""
        val = response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset-requests")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            # HTTP-date format — compute delta from now
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone
            try:
                reset_dt = parsedate_to_datetime(val)
                delta = (reset_dt - datetime.now(timezone.utc)).total_seconds()
                return max(delta, 0.0)
            except Exception:
                return None

    async def forward(self, body: dict) -> BackendResponse:
        """Forward with retry on transient errors."""
        model = body.get("model", "")

        for attempt in range(self.max_retries + 1):
            response = await self.backend.forward(body)

            if response.ok:
                return response

            # Check if retryable
            if not self._is_retryable(response.status_code):
                logger.debug(
                    "Backend '%s' returned non-retryable %d for '%s': %s",
                    self.name,
                    response.status_code,
                    model,
                    response.error,
                )
                return response

            # Transient error — retry if attempts remain
            if attempt < self.max_retries:
                # 429 gets a minimum 5s backoff floor — most rate-limit reset windows
                # are at least a few seconds; the exponential formula alone could
                # produce sub-second delays on the first retry.
                base_backoff = self._backoff_seconds(attempt + 1)
                backoff = max(base_backoff, 5.0) if response.status_code == 429 else base_backoff
                logger.warning(
                    "Backend '%s' transient %d for '%s', retry in %.1fs (%d/%d)",
                    self.name,
                    response.status_code,
                    model,
                    backoff,
                    attempt + 1,
                    self.max_retries,
                )
                await asyncio.sleep(backoff)
                continue

            # No more retries
            logger.error(
                "Backend '%s' exhausted retries for '%s' (last: %s)",
                self.name,
                model,
                response.error,
            )
            return response

        return response  # unreachable but satisfies type checker

    async def forward_stream(self, body: dict) -> AsyncIterator[str]:
        """
        Stream with retry on transient errors **before any bytes are sent**.

        Once a single chunk has been yielded to the caller, the bytes are in
        the client's response buffer and we cannot retry without corrupting
        the stream — a second attempt would replay tokens from the start,
        producing duplicated content and two `[DONE]` markers. So mid-stream
        failures (StreamStallError after partial output, connection drop
        after first chunk, etc.) are re-raised to the router, which propagates
        the failure to the client cleanly rather than papering over it.

        HTTP 4xx errors that are non-retryable (404, 400, 401, 403) are
        re-raised immediately so the MultiBackendRouter can try the next
        backend. Retryable errors (429, 5xx) before first chunk are retried
        with backoff; after exhaustion the last exception is re-raised.
        """
        from beigebox.config import get_config as _get_config
        stall_secs: float = _get_config().get("advanced", {}).get(
            "stream_stall_timeout_seconds", 30.0
        )

        model = body.get("model", "")
        last_exc: Exception | None = None
        retry_after_hint: float | None = None

        for attempt in range(self.max_retries + 1):
            yielded_anything = False
            try:
                async for line in _stall_guarded(
                    self.backend.forward_stream(body), stall_secs
                ):
                    yielded_anything = True
                    yield line
                return  # stream completed successfully
            except StreamStallError as e:
                if yielded_anything:
                    logger.error(
                        "Backend '%s' stream stall mid-response for '%s' "
                        "(partial bytes already delivered, no retry): %s",
                        self.name, model, e,
                    )
                    raise
                last_exc = e
                retry_after_hint = None
                logger.warning(
                    "Backend '%s' pre-stream stall for '%s' (%s), retry %d/%d",
                    self.name, model, e, attempt + 1, self.max_retries,
                )
            except httpx.HTTPStatusError as e:
                if yielded_anything:
                    logger.error(
                        "Backend '%s' HTTP %d mid-stream for '%s' "
                        "(partial bytes already delivered, no retry)",
                        self.name, e.response.status_code, model,
                    )
                    raise
                status = e.response.status_code
                if not self._is_retryable(status):
                    # Permanent HTTP error — propagate immediately, no retry
                    logger.debug(
                        "Backend '%s' non-retryable HTTP %d for '%s': %s",
                        self.name, status, model, e,
                    )
                    raise
                last_exc = e
                retry_after_hint = self._retry_after(e.response) if status == 429 else None
            except Exception as e:
                if yielded_anything:
                    logger.error(
                        "Backend '%s' connection error mid-stream for '%s' "
                        "(partial bytes already delivered, no retry): %s",
                        self.name, model, e,
                    )
                    raise
                # Connection error, timeout, etc. — pre-first-chunk only
                last_exc = e
                retry_after_hint = None

            if attempt < self.max_retries:
                backoff = self._backoff_seconds(attempt + 1, retry_after_hint)
                logger.warning(
                    "Backend '%s' stream error for '%s', retry in %.1fs (%d/%d)%s: %s",
                    self.name, model, backoff, attempt + 1, self.max_retries,
                    " (Retry-After)" if retry_after_hint else "", last_exc,
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(
                    "Backend '%s' stream exhausted retries for '%s': %s",
                    self.name, model, last_exc,
                )
                raise last_exc

    async def health_check(self) -> bool:
        """Delegate to wrapped backend."""
        return await self.backend.health_check()

    async def list_models(self) -> list[str]:
        """Delegate to wrapped backend."""
        return await self.backend.list_models()

    def supports_model(self, model: str) -> bool:
        """Delegate to wrapped backend."""
        return self.backend.supports_model(model)
