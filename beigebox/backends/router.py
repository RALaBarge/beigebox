"""
Multi-Backend Router — priority-based routing with fallback and latency-aware
backend deprioritization.

When backends_enabled is true, the proxy delegates forwarding to this router
instead of making direct httpx calls. The router tries backends in priority
order and falls back on timeout/error.

Latency-aware routing (optional, per-backend):
  Set latency_p95_threshold_ms in a backend's config block.
  The router maintains a rolling window of recent latencies and skips backends
  whose rolling P95 exceeds the threshold on the first pass, trying them only
  as a last resort.  0 = disabled (default).

Transparent to clients: same OpenAI-compatible request in, same response out.
"""

from __future__ import annotations

import fnmatch
import logging
import random
import time
from typing import AsyncIterator

from beigebox.backends.base import BaseBackend, BackendResponse
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend
from beigebox.backends.openai_compat import OpenAICompatibleBackend
from beigebox.backends.plugin_loader import load_backend_plugins
from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

# Provider name → backend class
# Built-in providers (always available)
PROVIDERS: dict[str, type[BaseBackend]] = {
    "ollama": OllamaBackend,
    "openrouter": OpenRouterBackend,
    "openai_compat": OpenAICompatibleBackend,
}

# Load custom backend plugins from backends/plugins/
_PLUGINS = load_backend_plugins("backends/plugins")
PROVIDERS.update(_PLUGINS)

_LATENCY_WINDOW = 100  # rolling window size per backend


class LatencyTracker:
    """
    In-memory rolling window of recent request latencies per backend.

    Thread-safety: all access is from the single asyncio event loop, so no
    locking is needed.
    """

    def __init__(self, window_size: int = _LATENCY_WINDOW):
        self._window_size = window_size
        self._samples: dict[str, list[float]] = {}

    def record(self, backend_name: str, latency_ms: float) -> None:
        """Append a latency sample, evicting the oldest if at capacity."""
        samples = self._samples.setdefault(backend_name, [])
        samples.append(latency_ms)
        if len(samples) > self._window_size:
            del samples[0]

    def p95(self, backend_name: str) -> float | None:
        """Rolling P95 latency, or None if no samples yet."""
        samples = self._samples.get(backend_name)
        if not samples:
            return None
        s = sorted(samples)
        idx = min(int(len(s) * 0.95), len(s) - 1)
        return s[idx]

    def sample_count(self, backend_name: str) -> int:
        return len(self._samples.get(backend_name, []))

    def is_degraded(self, backend_name: str, threshold_ms: float) -> bool:
        """True if the rolling P95 exceeds threshold_ms (0 = disabled)."""
        if threshold_ms <= 0:
            return False
        p95 = self.p95(backend_name)
        return p95 is not None and p95 > threshold_ms


class MultiBackendRouter:
    """
    Routes requests across multiple backends by priority with optional
    latency-aware deprioritization.

    Lower priority number = tried first.
    If a backend's rolling P95 exceeds its configured threshold it is
    deferred to the second pass — only used if all healthy backends fail.
    """

    def __init__(self, backends_config: list[dict]):
        self.backends: list[BaseBackend] = []
        self._thresholds: dict[str, float] = {}  # backend name → ms threshold (0=off)
        self._weights: dict[str, float] = {}     # backend name → traffic split weight (0=off)
        self._allow_unqualified_models: dict[str, bool] = {}  # backend opt-in for plain model ids
        self._allowed_models: dict[str, list[str]] = {}  # backend name → list of allowed model globs
        self._tracker = LatencyTracker()

        for cfg in backends_config:
            backend = self._create_backend(cfg)
            if backend:
                from beigebox.backends.retry_wrapper import RetryableBackendWrapper
                max_retries = cfg.get("max_retries", 2)
                backoff_base = cfg.get("backoff_base", 1.5)
                backoff_max = cfg.get("backoff_max", 10.0)
                wrapped = RetryableBackendWrapper(
                    backend,
                    max_retries=max_retries,
                    backoff_base=backoff_base,
                    backoff_max=backoff_max,
                )
                self.backends.append(wrapped)
                # Per-backend latency threshold (0 = disabled)
                threshold = float(cfg.get("latency_p95_threshold_ms", 0))
                self._thresholds[wrapped.name] = threshold
                if threshold > 0:
                    logger.info(
                        "Backend '%s': latency-aware routing enabled (P95 threshold %.0fms)",
                        wrapped.name, threshold,
                    )
                # Per-backend A/B traffic split weight (0 = disabled, uses priority order)
                weight = float(cfg.get("traffic_split", 0))
                self._weights[wrapped.name] = weight
                if weight > 0:
                    logger.info(
                        "Backend '%s': A/B split weight=%.1f",
                        wrapped.name, weight,
                    )
                self._allow_unqualified_models[wrapped.name] = bool(cfg.get("allow_unqualified_models", False))
                # Per-backend allowed models list (empty = unrestricted, offer all models)
                allowed_models = cfg.get("allowed_models", [])
                if isinstance(allowed_models, list):
                    self._allowed_models[wrapped.name] = allowed_models
                    if allowed_models:
                        logger.info(
                            "Backend '%s': allowed_models restricted to %s",
                            wrapped.name, allowed_models,
                        )

        self.backends.sort(key=lambda b: b.priority)
        names = [f"{b.name}(p{b.priority})" for b in self.backends]
        logger.info("Multi-backend router initialized: %s", " → ".join(names))

    @staticmethod
    def _create_backend(cfg: dict) -> BaseBackend | None:
        """Instantiate a backend from config dict."""
        provider = cfg.get("provider", "ollama")
        cls = PROVIDERS.get(provider)
        if not cls:
            logger.warning("Unknown backend provider '%s', skipping", provider)
            return None

        name = cfg.get("name", provider)
        url = cfg.get("url", "")
        if not url:
            logger.warning("Backend '%s' has no url, skipping", name)
            return None

        kwargs = {
            "name": name,
            "url": url,
            "timeout": cfg.get("timeout", 120),
            "priority": cfg.get("priority", 99),
        }

        if provider == "openrouter":
            kwargs["api_key"] = cfg.get("api_key", "")

        return cls(**kwargs)

    def _unwrap(self, backend: BaseBackend) -> BaseBackend:
        return getattr(backend, "backend", backend)

    def _is_openrouter(self, backend: BaseBackend) -> bool:
        return isinstance(self._unwrap(backend), OpenRouterBackend)

    def _global_allow_openrouter_for_plain_models(self) -> bool:
        rt = get_runtime_config()
        cfg = get_config()
        return bool(
            rt.get(
                "allow_openrouter_for_plain_models",
                cfg.get("routing", {}).get("allow_openrouter_for_plain_models", False),
            )
        )

    def _can_attempt_model(self, backend: BaseBackend, model: str) -> bool:
        # Check allowed_models list first (if configured, non-empty = restrictive)
        allowed = self._allowed_models.get(backend.name)
        if allowed:  # Non-empty list = restrict to these models
            if not any(fnmatch.fnmatch(model, p) for p in allowed):
                return False
        # Empty list = unrestricted (existing behaviour preserved)

        # OpenRouter uses provider/model IDs (e.g. "openai/gpt-4o"). Plain model names
        # (no "/") are for Ollama/local backends by default. The allow_unqualified_models
        # flag or the global allow_openrouter_for_plain_models override lets OpenRouter
        # try plain names too — useful when you want to route a local model name to OR as
        # a fallback when Ollama is down.
        if "/" not in model and self._is_openrouter(backend):
            return self._allow_unqualified_models.get(backend.name, False) or self._global_allow_openrouter_for_plain_models()
        return backend.supports_model(model)

    def get_backend(self, name: str) -> BaseBackend | None:
        """Get a specific backend by name."""
        for b in self.backends:
            if b.name == name:
                return b
        return None

    def get_openrouter_backend(self):
        """Return first OpenRouterBackend (unwrapped from RetryableBackendWrapper), or None."""
        for b in self.backends:
            inner = self._unwrap(b)
            if isinstance(inner, OpenRouterBackend):
                return inner
        return None

    def _partition_backends(self, model: str) -> tuple[list[BaseBackend], list[BaseBackend]]:
        """
        Split backends into (fast, degraded) lists for two-pass routing.
        Backends that don't support the model are excluded from both lists.
        """
        fast: list[BaseBackend] = []
        degraded: list[BaseBackend] = []
        for backend in self.backends:
            if not self._can_attempt_model(backend, model):
                continue
            threshold = self._thresholds.get(backend.name, 0)
            if self._tracker.is_degraded(backend.name, threshold):
                degraded.append(backend)
            else:
                fast.append(backend)
        return fast, degraded

    def _select_ab(self, backends: list[BaseBackend]) -> list[BaseBackend]:
        """
        Reorder backends using A/B traffic_split weights.

        When any backend has traffic_split > 0, one backend is selected as the
        primary choice via weighted random selection; the remaining backends
        follow in their original priority order as fallbacks.

        When no weights are configured (all zero) the list is returned unchanged
        and existing priority-based ordering applies.
        """
        if not backends:
            return backends
        weights = [self._weights.get(b.name, 0.0) for b in backends]
        if not any(w > 0 for w in weights):
            return backends  # no A/B split configured — keep priority order
        # random.choices handles zero-weight entries naturally (0 probability),
        # so backends without traffic_split set are effectively excluded from
        # primary selection but still appear as fallbacks in the returned list.
        chosen = random.choices(backends, weights=weights, k=1)[0]
        rest = [b for b in backends if b is not chosen]
        logger.debug("A/B split selected primary backend '%s'", chosen.name)
        return [chosen] + rest

    async def forward(self, body: dict) -> BackendResponse:
        """
        Forward a non-streaming request.

        First pass: try fast (non-degraded) backends in priority order.
        Second pass: if all fast backends fail, try degraded backends as fallback.

        If ``_bb_force_backend`` is present in the body it is stripped and used
        to target a specific named backend, bypassing normal selection.
        """
        force_name: str | None = body.pop("_bb_force_backend", None)
        if force_name:
            backend = self.get_backend(force_name)
            if backend:
                logger.debug("routing_rules: forcing backend '%s'", force_name)
                return await backend.forward(body)
            logger.warning(
                "routing_rules: backend '%s' not found — falling back to normal routing",
                force_name,
            )

        model = body.get("model", "")
        errors: list[str] = []
        fast, degraded = self._partition_backends(model)
        fast = self._select_ab(fast)

        if degraded:
            logger.info(
                "Latency-aware routing: deferring %s (rolling P95 exceeds threshold)",
                ", ".join(b.name for b in degraded),
            )

        for backend in fast:
            logger.debug("Trying backend '%s' for model '%s'", backend.name, model)
            response = await backend.forward(body)
            if response.ok:
                self._tracker.record(backend.name, response.latency_ms)
                logger.info(
                    "Backend '%s' served model '%s' in %.0fms",
                    backend.name, model, response.latency_ms,
                )
                return response
            errors.append(f"{backend.name}: {response.error}")
            logger.warning("Backend '%s' failed for model '%s': %s", backend.name, model, response.error)

        # Second pass — degraded backends as last resort
        for backend in degraded:
            logger.warning("Falling back to degraded backend '%s' for model '%s'", backend.name, model)
            response = await backend.forward(body)
            if response.ok:
                self._tracker.record(backend.name, response.latency_ms)
                logger.info("Backend '%s' (degraded) served model '%s' in %.0fms",
                            backend.name, model, response.latency_ms)
                return response
            errors.append(f"{backend.name}: {response.error}")
            logger.warning("Backend '%s' failed for model '%s': %s", backend.name, model, response.error)

        error_summary = "; ".join(errors) if errors else "No backends available"
        logger.error("All backends exhausted for model '%s': %s", model, error_summary)
        return BackendResponse(
            ok=False,
            status_code=503,
            backend_name="router",
            error=f"All backends failed: {error_summary}",
        )

    async def forward_stream(self, body: dict) -> AsyncIterator[str]:
        """
        Forward a streaming request.

        First pass: fast backends in priority order.
        Second pass: degraded backends as fallback.
        Total elapsed time per backend is recorded in the rolling window.

        If ``_bb_force_backend`` is present in the body it is stripped and used
        to target a specific named backend, bypassing normal selection.
        """
        force_name: str | None = body.pop("_bb_force_backend", None)
        if force_name:
            backend = self.get_backend(force_name)
            if backend:
                logger.debug("routing_rules: forcing backend '%s' (stream)", force_name)
                async for line in backend.forward_stream(body):
                    yield line
                return
            logger.warning(
                "routing_rules: backend '%s' not found — falling back to normal routing",
                force_name,
            )

        model = body.get("model", "")
        errors: list[str] = []
        fast, degraded = self._partition_backends(model)
        fast = self._select_ab(fast)

        if degraded:
            logger.info(
                "Latency-aware routing (stream): deferring %s",
                ", ".join(b.name for b in degraded),
            )

        # Merge fast and degraded into a single loop so we can use a single
        # try/except with continue — streaming backends can't be retried
        # mid-stream so on any error we just move on to the next backend.
        for backend in fast + degraded:
            is_fallback = backend in degraded
            if is_fallback:
                logger.warning("Falling back to degraded backend '%s' for stream", backend.name)
            else:
                logger.debug("Trying stream from backend '%s' for model '%s'", backend.name, model)

            t0 = time.monotonic()
            try:
                async for line in backend.forward_stream(body):
                    yield line
                # Stream completed successfully — record total elapsed time
                self._tracker.record(backend.name, (time.monotonic() - t0) * 1000)
                return
            except Exception as e:
                errors.append(f"{backend.name}: {e}")
                logger.warning("Backend '%s' stream failed for model '%s': %s", backend.name, model, e)
                continue

        import json
        error_msg = "; ".join(errors) if errors else "No backends available"
        error_chunk = json.dumps({
            "choices": [{"delta": {"content": f"\n\n[BeigeBox: All backends failed: {error_msg}]"}, "index": 0}],
            "model": "beigebox-error",
        })
        yield f"data: {error_chunk}"
        yield "data: [DONE]"

    def get_backend_stats(self) -> list[dict]:
        """
        Per-backend rolling latency stats and degraded status.
        Used by GET /api/v1/backends.
        """
        stats = []
        for backend in self.backends:
            threshold = self._thresholds.get(backend.name, 0)
            p95 = self._tracker.p95(backend.name)
            count = self._tracker.sample_count(backend.name)
            # Unwrap RetryableBackendWrapper to get the real provider name
            inner = self._unwrap(backend)
            provider = type(inner).__name__.replace("Backend", "").lower()
            stats.append({
                "name": backend.name,
                "url": getattr(backend, "url", ""),
                "priority": backend.priority,
                "provider": provider,
                "rolling_p95_ms": round(p95, 1) if p95 is not None else None,
                "rolling_samples": count,
                "latency_threshold_ms": threshold,
                "degraded": self._tracker.is_degraded(backend.name, threshold),
                "traffic_split": self._weights.get(backend.name, 0.0),
                "allow_unqualified_models": (self._allow_unqualified_models.get(backend.name, False) or self._global_allow_openrouter_for_plain_models()),
            })
        return stats

    async def list_all_models(self) -> dict:
        """
        Aggregate models from all backends into a unified /v1/models response.
        Deduplicates by model id. Fetches all backends in parallel.
        """
        import asyncio as _asyncio

        async def _fetch(backend):
            try:
                return backend.name, await backend.list_models()
            except Exception as e:
                logger.warning("Failed to list models from '%s': %s", backend.name, e)
                return backend.name, []

        results = await _asyncio.gather(*[_fetch(b) for b in self.backends])

        seen: set[str] = set()
        all_models: list[dict] = []
        for backend_name, models in results:
            for model_id in models:
                if model_id not in seen:
                    seen.add(model_id)
                    all_models.append({
                        "id": model_id,
                        "object": "model",
                        "owned_by": backend_name,
                    })

        return {
            "object": "list",
            "data": all_models,
        }

    async def health(self) -> list[dict]:
        """
        Health check all backends and merge with rolling latency stats.
        Returns a list (not dict) so the dashboard can iterate directly.
        """
        latency_stats = {s["name"]: s for s in self.get_backend_stats()}
        results = []
        for backend in self.backends:
            try:
                ok = await backend.health_check()
            except Exception as e:
                logger.warning("Health check failed for '%s': %s", backend.name, e)
                ok = False
            entry = {"healthy": ok}
            entry.update(latency_stats.get(backend.name, {"name": backend.name}))
            # Attach Ollama hardware stats if available
            inner = self._unwrap(backend)
            if isinstance(inner, OllamaBackend):
                entry["hw_stats"] = inner.get_hw_stats()
            results.append(entry)
        return results
