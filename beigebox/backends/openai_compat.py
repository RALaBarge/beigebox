"""
Generic OpenAI-compatible backend.

Supports any endpoint that speaks OpenAI API format:
- llama.cpp server
- vLLM
- Text Generation WebUI (TGI)
- Aphrodite
- LocalAI
- Ollama (can also use this instead of OllamaBackend)
"""

from __future__ import annotations

import logging
import time

import httpx

from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleBackend(BaseBackend):
    """
    Generic backend for OpenAI-compatible endpoints.
    
    Works with any service that implements /v1/chat/completions and /v1/models.
    """

    def __init__(
        self,
        name: str,
        url: str,
        timeout: int = 120,
        priority: int = 1,
        api_key: str = "",
    ):
        super().__init__(name, url, timeout, priority)
        self.api_key = api_key

    async def forward(self, body: dict) -> BackendResponse:
        """Forward a non-streaming request."""
        t0 = time.monotonic()
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=body,
                    headers=headers,
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
                    cost_usd=None,  # Local/self-hosted is always free
                )
        except httpx.TimeoutException:
            latency = (time.monotonic() - t0) * 1000
            logger.warning(
                "OpenAI-compatible backend '%s' timed out after %.0fms",
                self.name,
                latency,
            )
            return BackendResponse(
                ok=False,
                backend_name=self.name,
                latency_ms=latency,
                error=f"Timeout after {self.timeout}s",
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.warning(
                "OpenAI-compatible backend '%s' failed: %s", self.name, e
            )
            return BackendResponse(
                ok=False,
                backend_name=self.name,
                latency_ms=latency,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Forward a streaming request, yielding SSE lines."""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield line
        except httpx.TimeoutException:
            logger.warning(
                "OpenAI-compatible backend '%s' stream timed out", self.name
            )
            raise
        except Exception as e:
            logger.warning(
                "OpenAI-compatible backend '%s' stream failed: %s", self.name, e
            )
            raise

    async def health_check(self) -> bool:
        """Check endpoint is reachable."""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.url}/v1/models",
                    headers=headers,
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Fetch available models from endpoint."""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.url}/v1/models",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                models = [
                    m.get("id", m.get("name", ""))
                    for m in data.get("data", [])
                ]
                self._available_models = [m for m in models if m]
                return self._available_models
        except Exception as e:
            logger.warning(
                "Failed to list models from '%s': %s", self.name, e
            )
            return []
