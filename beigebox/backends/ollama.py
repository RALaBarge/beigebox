"""
Ollama backend — local LLM inference via Ollama's OpenAI-compatible API.
Cost is always $0 (local compute).
"""

from __future__ import annotations

import logging
import time

import httpx

from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)


class OllamaBackend(BaseBackend):
    """Backend for local Ollama instances."""

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

    async def health_check(self) -> bool:
        """Check Ollama is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Fetch available models from Ollama."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.url}/v1/models")
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id", m.get("name", "")) for m in data.get("data", [])]
                self._available_models = [m for m in models if m]
                return self._available_models
        except Exception as e:
            logger.warning("Failed to list Ollama models from '%s': %s", self.name, e)
            return []
