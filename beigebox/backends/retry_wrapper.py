"""
Retry wrapper for backends with exponential backoff.

Wraps any backend to add retry logic for transient errors:
- 429: Rate limited
- 5xx: Server errors (except 501 Not Implemented — permanent)

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

logger = logging.getLogger(__name__)


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
        # 429: rate limit, 500/502/503/504: transient server errors
        return status_code in (429, 500, 502, 503, 504)

    def _backoff_seconds(self, attempt: int) -> float:
        """Calculate backoff time for attempt N (exponential)."""
        delay = self.backoff_base ** attempt
        return min(delay, self.backoff_max)

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
                backoff = self._backoff_seconds(attempt + 1)
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
        Stream with retry on transient errors before any bytes are sent.

        HTTP 4xx errors that are non-retryable (404, 400, 401, 403) are
        re-raised immediately so the MultiBackendRouter can try the next
        backend.  Retryable errors (429, 5xx) are retried with backoff;
        after exhaustion the last exception is re-raised so the router
        can still fall through rather than leaking error text inline.
        """
        model = body.get("model", "")
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                async for line in self.backend.forward_stream(body):
                    yield line
                return  # stream completed successfully
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if not self._is_retryable(status):
                    # Permanent HTTP error — propagate immediately, no retry
                    logger.debug(
                        "Backend '%s' non-retryable HTTP %d for '%s': %s",
                        self.name, status, model, e,
                    )
                    raise
                last_exc = e
            except Exception as e:
                # Connection error, timeout, etc.
                last_exc = e

            if attempt < self.max_retries:
                backoff = self._backoff_seconds(attempt + 1)
                logger.warning(
                    "Backend '%s' stream error for '%s', retry in %.1fs (%d/%d): %s",
                    self.name, model, backoff, attempt + 1, self.max_retries, last_exc,
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
