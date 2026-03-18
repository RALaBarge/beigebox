"""
Ollama backend — local LLM inference via Ollama's OpenAI-compatible API.
Cost is always $0 (local compute).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)

_PS_CACHE_TTL = 30.0  # seconds


class OllamaBackend(BaseBackend):
    """Backend for local Ollama instances."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ps_cache: list[dict[str, Any]] = []
        self._ps_fetched_at: float = 0.0

    async def forward(self, body: dict) -> BackendResponse:
        """Forward a non-streaming request to Ollama."""
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=body,
                )
                latency = (time.monotonic() - t0) * 1000
                if resp.status_code >= 400:
                    return BackendResponse(
                        ok=False,
                        status_code=resp.status_code,
                        backend_name=self.name,
                        latency_ms=latency,
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    )
                data = resp.json()
                return BackendResponse(
                    ok=True,
                    status_code=resp.status_code,
                    data=data,
                    backend_name=self.name,
                    latency_ms=latency,
                    cost_usd=None,  # Local is always free
                )
        except httpx.TimeoutException:
            latency = (time.monotonic() - t0) * 1000
            logger.warning("Ollama backend '%s' timed out after %.0fms", self.name, latency)
            return BackendResponse(
                ok=False, backend_name=self.name, latency_ms=latency,
                error=f"Timeout after {self.timeout}s",
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.warning("Ollama backend '%s' failed: %s", self.name, e)
            return BackendResponse(
                ok=False, backend_name=self.name, latency_ms=latency,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Forward a streaming request to Ollama, yielding SSE lines."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.url}/v1/chat/completions",
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield line
        except httpx.TimeoutException:
            logger.warning("Ollama backend '%s' stream timed out", self.name)
            raise
        except Exception as e:
            logger.warning("Ollama backend '%s' stream failed: %s", self.name, e)
            raise

    async def fetch_ps_stats(self) -> list[dict[str, Any]]:
        """
        Call GET /api/ps and cache the result for _PS_CACHE_TTL seconds.

        Returns a list of running-model dicts from Ollama, e.g.:
          [{"name": "llama3:8b", "size_vram": 4831838208, "details": {"parameter_size": "8B"},
            "model": {...}}]
        Returns [] on any error (non-fatal).
        """
        now = time.monotonic()
        if now - self._ps_fetched_at < _PS_CACHE_TTL:
            return self._ps_cache
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/api/ps")
                if resp.status_code == 200:
                    data = resp.json()
                    self._ps_cache = data.get("models", [])
                    self._ps_fetched_at = now
                    return self._ps_cache
        except Exception as e:
            logger.debug("Ollama /api/ps failed for '%s': %s", self.name, e)
        return self._ps_cache  # return stale cache on error

    def get_hw_stats(self) -> list[dict[str, Any]]:
        """
        Parse the cached /api/ps data into a list of per-model hardware stats dicts:
          {
            "model": str,
            "gpu_layers": int,
            "vram_used_mb": int,
            "context_window": int,
          }

        gpu_layers: taken directly from model.num_gpu (layers on GPU).
        vram_used_mb: size_vram from /api/ps (bytes → MB).  Falls back to
            back-of-envelope: layers_on_gpu * (size / total_layers).
        context_window: model.context_length if present, else 0.
        """
        result = []
        for entry in self._ps_cache:
            name = entry.get("name", "")
            model_info = entry.get("model", {})

            # GPU layers
            gpu_layers: int = int(model_info.get("num_gpu", 0))
            total_layers: int = int(model_info.get("num_layer", 0))

            # VRAM: prefer direct measurement from /api/ps
            size_vram_bytes: int = int(entry.get("size_vram", 0))
            if size_vram_bytes > 0:
                vram_used_mb = size_vram_bytes // (1024 * 1024)
            elif gpu_layers > 0 and total_layers > 0:
                # back-of-envelope: gpu_layers * (total_size / total_layers)
                total_size_bytes: int = int(entry.get("size", 0))
                vram_used_mb = int(gpu_layers * (total_size_bytes / total_layers) / (1024 * 1024))
            else:
                vram_used_mb = 0

            context_window: int = int(model_info.get("context_length", 0))

            result.append({
                "model": name,
                "gpu_layers": gpu_layers,
                "total_layers": total_layers,
                "vram_used_mb": vram_used_mb,
                "context_window": context_window,
            })
        return result

    async def health_check(self) -> bool:
        """Check Ollama is reachable and refresh /api/ps cache."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/api/tags")
                ok = resp.status_code == 200
            if ok:
                # Refresh hardware stats alongside the health check — fire and forget
                # result; errors are non-fatal.
                await self.fetch_ps_stats()
            return ok
        except Exception:
            return False

    def supports_model(self, model: str) -> bool:
        """Route model to Ollama if it appears in Ollama's own model list.

        If _available_models is populated (populated by list_models()), an exact
        match always wins — this covers hf.co/... and other non-standard IDs that
        Ollama itself advertises.  Only fall back to the slash heuristic when the
        model list is empty (cold start / health-check not yet run), where a "/"
        still reliably signals an OpenRouter-style ID like "openai/gpt-4o".
        """
        if self._available_models:
            return model in self._available_models
        # Cold-start fallback: reject provider/model IDs (OpenRouter-style).
        if "/" in model:
            return False
        return super().supports_model(model)

    async def list_models(self) -> list[str]:
        """Fetch available models from Ollama."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.url}/v1/models")
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id", m.get("name", "")) for m in data.get("data", [])]
                # Cache in _available_models so supports_model() can answer
                # without an extra network call between health checks.
                self._available_models = [m for m in models if m]
                return self._available_models
        except Exception as e:
            logger.warning("Failed to list Ollama models from '%s': %s", self.name, e)
            return []
