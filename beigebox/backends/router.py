"""
Multi-Backend Router — priority-based routing with fallback.

When backends_enabled is true, the proxy delegates forwarding to this router
instead of making direct httpx calls. The router tries backends in priority
order and falls back on timeout/error.

Transparent to clients: same OpenAI-compatible request in, same response out.
"""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from beigebox.backends.base import BaseBackend, BackendResponse
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend
from beigebox.backends.openai_compat import OpenAICompatibleBackend

logger = logging.getLogger(__name__)

# Provider name → backend class
PROVIDERS: dict[str, type[BaseBackend]] = {
    "ollama": OllamaBackend,
    "openrouter": OpenRouterBackend,
    "openai_compat": OpenAICompatibleBackend,
}


class MultiBackendRouter:
    """
    Routes requests across multiple backends by priority.
    Lower priority number = tried first.
    """

    def __init__(self, backends_config: list[dict]):
        self.backends: list[BaseBackend] = []
        for cfg in backends_config:
            backend = self._create_backend(cfg)
            if backend:
                # Wrap with retry logic for transient error handling
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

        # Sort by priority (lower = first)
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

        # OpenRouter needs api_key
        if provider == "openrouter":
            kwargs["api_key"] = cfg.get("api_key", "")

        return cls(**kwargs)

    def get_backend(self, name: str) -> BaseBackend | None:
        """Get a specific backend by name."""
        for b in self.backends:
            if b.name == name:
                return b
        return None

    async def forward(self, body: dict) -> BackendResponse:
        """
        Forward a non-streaming request.
        Tries backends in priority order; returns first success.
        """
        model = body.get("model", "")
        errors: list[str] = []

        for backend in self.backends:
            if not backend.supports_model(model):
                continue

            logger.debug("Trying backend '%s' for model '%s'", backend.name, model)
            response = await backend.forward(body)

            if response.ok:
                logger.info(
                    "Backend '%s' served model '%s' in %.0fms",
                    backend.name, model, response.latency_ms,
                )
                return response

            # Failed — log and try next
            errors.append(f"{backend.name}: {response.error}")
            logger.warning(
                "Backend '%s' failed for model '%s': %s",
                backend.name, model, response.error,
            )

        # All backends exhausted
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
        Tries backends in priority order; yields from first that connects.
        Falls back on connection/timeout errors (NOT mid-stream failures).
        """
        model = body.get("model", "")
        errors: list[str] = []

        for backend in self.backends:
            if not backend.supports_model(model):
                continue

            logger.debug("Trying stream from backend '%s' for model '%s'", backend.name, model)
            try:
                async for line in backend.forward_stream(body):
                    yield line
                # If we get here, streaming completed successfully
                return
            except Exception as e:
                errors.append(f"{backend.name}: {e}")
                logger.warning(
                    "Backend '%s' stream failed for model '%s': %s",
                    backend.name, model, e,
                )
                continue

        # All backends exhausted — yield an error as SSE
        import json
        error_msg = "; ".join(errors) if errors else "No backends available"
        error_chunk = json.dumps({
            "choices": [{"delta": {"content": f"\n\n[BeigeBox: All backends failed: {error_msg}]"}, "index": 0}],
            "model": "beigebox-error",
        })
        yield f"data: {error_chunk}"
        yield "data: [DONE]"

    async def list_all_models(self) -> dict:
        """
        Aggregate models from all backends into a unified /v1/models response.
        Deduplicates by model id.
        """
        seen: set[str] = set()
        all_models: list[dict] = []

        for backend in self.backends:
            try:
                models = await backend.list_models()
                for model_id in models:
                    if model_id not in seen:
                        seen.add(model_id)
                        all_models.append({
                            "id": model_id,
                            "object": "model",
                            "owned_by": backend.name,
                        })
            except Exception as e:
                logger.warning("Failed to list models from '%s': %s", backend.name, e)

        return {
            "object": "list",
            "data": all_models,
        }

    async def health(self) -> dict:
        """Health check all backends."""
        results = {}
        for backend in self.backends:
            try:
                ok = await backend.health_check()
                results[backend.name] = {"healthy": ok, "priority": backend.priority}
            except Exception as e:
                results[backend.name] = {"healthy": False, "error": str(e)}
        return results
