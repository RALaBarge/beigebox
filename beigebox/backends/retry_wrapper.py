"""
Retry wrapper for backends with exponential backoff.

Wraps any backend to add retry logic for transient errors:
- 404: Model loading, not found
- 429: Rate limited
- 5xx: Server errors

Non-retried errors (permanent):
- 401, 403: Auth/permission errors
- 400: Bad request
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

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
        # 404: model loading, 429: rate limit, 5xx: server error
        return status_code in (404, 429, 500, 501, 502, 503, 504)

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
                # Permanent error (auth, not found, etc)
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

        # Should not reach here
        return response

    async def forward_stream(self, body: dict) -> AsyncIterator[str]:
        """Stream with retry on connection errors (not mid-stream)."""
        model = body.get("model", "")

        for attempt in range(self.max_retries + 1):
            try:
                async for line in self.backend.forward_stream(body):
                    yield line
                # Success
                return
            except Exception as e:
                # Connection error — retry if attempts remain
                if attempt < self.max_retries:
                    backoff = self._backoff_seconds(attempt + 1)
                    logger.warning(
                        "Backend '%s' stream error for '%s', retry in %.1fs: %s",
                        self.name,
                        model,
                        backoff,
                        e,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # No more retries — yield error to client
                logger.error(
                    "Backend '%s' stream exhausted retries for '%s': %s",
                    self.name,
                    model,
                    e,
                )
                import json

                error_chunk = json.dumps({
                    "choices": [{
                        "delta": {
                            "content": f"\n\n[BeigeBox: {self.name} failed: {e}]"
                        },
                        "index": 0
                    }],
                    "model": "beigebox-error",
                })
                yield f"data: {error_chunk}"
                yield "data: [DONE]"
                return

    async def health_check(self) -> bool:
        """Delegate to wrapped backend."""
        return await self.backend.health_check()

    async def list_models(self) -> list[str]:
        """Delegate to wrapped backend."""
        return await self.backend.list_models()

    def supports_model(self, model: str) -> bool:
        """Delegate to wrapped backend."""
        return self.backend.supports_model(model)
