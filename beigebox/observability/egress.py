"""
Observability egress — fire-and-forget event delivery to external sinks.

Architecture
------------
- EgressHook   — abstract base; one instance per configured sink
- WebhookEgress — HTTP POST/PUT with batching and shared RetryConfig backoff

Events are enqueued via ``emit(event)``.  A background worker drains the
queue in batches (batch_size or batch_timeout_ms, whichever fires first).
Errors are logged and silently swallowed — egress **never** blocks or
propagates exceptions to the proxy pipeline.

Duplicate delivery is acceptable; receivers should be idempotent.

Wire-up
-------
Call ``build_egress_hooks(cfg)`` at startup to get a list of hooks from
``config.yaml``.  Pass that list into ``WireLog.__init__`` (or call
``emit_all(hooks, event)`` wherever events originate).

Config shape (config.yaml):

    observability:
      egress:
        webhooks:
          - url: https://my-logging.com/ingest
            batch_size: 100
            batch_timeout_ms: 5000
            max_retries: 2
          - url: http://prometheus-pushgateway:9091
            method: put
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from beigebox.utils.retry import RetryConfig, is_retryable, backoff_seconds

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class EgressHook(ABC):
    """Base class for all observability egress sinks."""

    @abstractmethod
    async def emit(self, event: dict[str, Any]) -> None:
        """Enqueue *event* for delivery.  Must be non-blocking."""

    @abstractmethod
    async def flush(self) -> None:
        """Best-effort flush of any buffered events (called at shutdown)."""

    @abstractmethod
    async def start(self) -> None:
        """Start background workers.  Called once during server startup."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful stop.  Flush remaining events then cancel background tasks."""


# ---------------------------------------------------------------------------
# Webhook egress
# ---------------------------------------------------------------------------

_DEFAULT_BATCH_SIZE = 50
_DEFAULT_BATCH_TIMEOUT_MS = 3000
_DEFAULT_METHOD = "post"
_QUEUE_MAXSIZE = 10_000  # drop events if the queue grows this large


class WebhookEgress(EgressHook):
    """HTTP POST/PUT webhook with batching and exponential backoff retry.

    Parameters
    ----------
    url:
        Full URL to POST/PUT batches to.
    method:
        HTTP method — ``"post"`` (default) or ``"put"``.
    batch_size:
        Flush when this many events have accumulated.
    batch_timeout_ms:
        Flush after this many milliseconds even if batch_size not reached.
    retry_config:
        RetryConfig controlling max_retries, backoff_base, backoff_max.
    """

    def __init__(
        self,
        url: str,
        method: str = _DEFAULT_METHOD,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        batch_timeout_ms: int = _DEFAULT_BATCH_TIMEOUT_MS,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self.url = url
        self.method = method.lower()
        self.batch_size = max(1, batch_size)
        self.batch_timeout_s = batch_timeout_ms / 1000.0
        self.retry_config = retry_config or RetryConfig()

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._worker_task: asyncio.Task | None = None

    # ── EgressHook interface ────────────────────────────────────────────────

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(
            self._drain_loop(), name=f"webhook-egress-{self.url}"
        )
        logger.info("WebhookEgress started: %s (batch=%d, timeout=%.1fs)",
                    self.url, self.batch_size, self.batch_timeout_s)

    async def stop(self) -> None:
        await self.flush()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def emit(self, event: dict[str, Any]) -> None:
        """Non-blocking enqueue.  Drops event and logs a warning if queue is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("WebhookEgress queue full (%d), dropping event for %s",
                           _QUEUE_MAXSIZE, self.url)

    async def flush(self) -> None:
        """Drain whatever is currently in the queue right now."""
        batch: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._send_with_retry(batch)

    # ── Internal machinery ──────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        """Background coroutine: collect events and flush them in batches."""
        while True:
            batch: list[dict[str, Any]] = []
            deadline = time.monotonic() + self.batch_timeout_s

            # Collect up to batch_size events within the timeout window
            while len(batch) < self.batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(event)
                except asyncio.TimeoutError:
                    break
                except asyncio.CancelledError:
                    # Worker cancelled — flush what we have and exit
                    if batch:
                        await self._send_with_retry(batch)
                    return

            if batch:
                await self._send_with_retry(batch)

    async def _send_with_retry(self, batch: list[dict[str, Any]]) -> None:
        """POST/PUT *batch* with exponential backoff retry.  Never raises."""
        cfg = self.retry_config
        last_exc: Exception | None = None

        for attempt in range(cfg.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    fn = client.post if self.method == "post" else client.put
                    resp = await fn(self.url, json=batch)

                if resp.status_code < 300:
                    logger.debug("WebhookEgress: delivered %d events to %s", len(batch), self.url)
                    return

                if not is_retryable(resp.status_code):
                    logger.error(
                        "WebhookEgress: non-retryable HTTP %d from %s — dropping %d events",
                        resp.status_code, self.url, len(batch),
                    )
                    return

                # Retryable HTTP error
                retry_after_val: float | None = None
                if resp.status_code == 429:
                    try:
                        retry_after_val = float(resp.headers.get("retry-after", 0) or 0)
                    except (ValueError, TypeError):
                        pass

                wait = backoff_seconds(attempt + 1, retry_after_val, cfg)
                last_exc = Exception(f"HTTP {resp.status_code}")

            except Exception as exc:  # network errors, timeouts, etc.
                wait = backoff_seconds(attempt + 1, None, cfg)
                last_exc = exc

            if attempt < cfg.max_retries:
                logger.warning(
                    "WebhookEgress: delivery to %s failed (%s), retry in %.1fs (%d/%d)",
                    self.url, last_exc, wait, attempt + 1, cfg.max_retries,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "WebhookEgress: exhausted retries for %s — dropping %d events: %s",
                    self.url, len(batch), last_exc,
                )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_egress_hooks(cfg: dict) -> list[EgressHook]:
    """Instantiate egress hooks from ``config.yaml`` ``observability.egress`` block.

    Returns an empty list if the section is absent or has no webhooks.
    """
    obs_cfg = cfg.get("observability", {})
    egress_cfg = obs_cfg.get("egress", {})
    webhook_cfgs = egress_cfg.get("webhooks", [])

    hooks: list[EgressHook] = []
    for wh in webhook_cfgs:
        url = wh.get("url", "").strip()
        if not url:
            logger.warning("WebhookEgress: skipping entry with no url")
            continue

        retry_config = RetryConfig(
            max_retries=int(wh.get("max_retries", 2)),
            backoff_base=float(wh.get("backoff_base", 1.5)),
            backoff_max=float(wh.get("backoff_max", 10.0)),
        )
        hook = WebhookEgress(
            url=url,
            method=wh.get("method", _DEFAULT_METHOD),
            batch_size=int(wh.get("batch_size", _DEFAULT_BATCH_SIZE)),
            batch_timeout_ms=int(wh.get("batch_timeout_ms", _DEFAULT_BATCH_TIMEOUT_MS)),
            retry_config=retry_config,
        )
        hooks.append(hook)

    return hooks


async def start_egress_hooks(hooks: list[EgressHook]) -> None:
    """Start all egress hooks.  Call once during server lifespan startup."""
    for hook in hooks:
        await hook.start()


async def stop_egress_hooks(hooks: list[EgressHook]) -> None:
    """Gracefully stop all egress hooks.  Call during server lifespan shutdown."""
    for hook in hooks:
        try:
            await hook.stop()
        except Exception as exc:
            logger.warning("Error stopping egress hook: %s", exc)


async def emit_all(hooks: list[EgressHook], event: dict[str, Any]) -> None:
    """Emit *event* to every hook.  Non-blocking; swallows all errors."""
    for hook in hooks:
        try:
            await hook.emit(event)
        except Exception as exc:
            logger.debug("EgressHook.emit error (suppressed): %s", exc)
