"""
OpenRouter backend — API access to hosted models.
Extracts cost_usd from OpenRouter response headers/body for cost tracking.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)


class OpenRouterBackend(BaseBackend):
    """Backend for OpenRouter API."""

    def __init__(
        self,
        name: str,
        url: str,
        api_key: str = "",
        timeout: int = 60,
        priority: int = 2,
    ):
        super().__init__(name=name, url=url, timeout=timeout, priority=priority)
        # Resolve env var references like ${OPENROUTER_API_KEY}
        self.api_key = self._resolve_env(api_key)

    @staticmethod
    def _resolve_env(value: str) -> str:
        """Resolve ${ENV_VAR} references in config values."""
        if value and value.startswith("${") and value.endswith("}"):
            env_name = value[2:-1]
            return os.environ.get(env_name, "")
        return value

    def _headers(self) -> dict:
        """Build request headers with auth."""
        headers = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RALaBarge/beigebox",
            "X-Title": "BeigeBox",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _extract_cost(data: dict) -> float | None:
        """
        Extract cost from OpenRouter response.
        OpenRouter includes usage info; cost may be in the response body
        or computed from token counts and known pricing.
        """
        # Direct cost field (some OpenRouter responses include this)
        if "cost_usd" in data:
            try:
                return float(data["cost_usd"])
            except (ValueError, TypeError):
                pass

        # Check usage.cost (alternative location)
        usage = data.get("usage", {})
        if "cost" in usage:
            try:
                return float(usage["cost"])
            except (ValueError, TypeError):
                pass

        # If no explicit cost, return None — we can't compute it without pricing tables
        return None

    async def forward(self, body: dict) -> BackendResponse:
        """Forward a non-streaming request to OpenRouter."""
        if not self.api_key:
            return BackendResponse(
                ok=False, backend_name=self.name,
                error="No API key configured for OpenRouter",
            )

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.url}/chat/completions",
                    headers=self._headers(),
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
                cost = self._extract_cost(data)

                return BackendResponse(
                    ok=True,
                    status_code=resp.status_code,
                    data=data,
                    backend_name=self.name,
                    latency_ms=latency,
                    cost_usd=cost,
                )
        except httpx.TimeoutException:
            latency = (time.monotonic() - t0) * 1000
            logger.warning("OpenRouter backend '%s' timed out after %.0fms", self.name, latency)
            return BackendResponse(
                ok=False, backend_name=self.name, latency_ms=latency,
                error=f"Timeout after {self.timeout}s",
            )
        except Exception as e:
            latency = (time.monotonic() - t0) * 1000
            logger.warning("OpenRouter backend '%s' failed: %s", self.name, e)
            return BackendResponse(
                ok=False, backend_name=self.name, latency_ms=latency,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Forward a streaming request to OpenRouter, yielding SSE lines."""
        if not self.api_key:
            raise RuntimeError("No API key configured for OpenRouter")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield line
        except httpx.TimeoutException:
            logger.warning("OpenRouter backend '%s' stream timed out", self.name)
            raise
        except Exception as e:
            logger.warning("OpenRouter backend '%s' stream failed: %s", self.name, e)
            raise

    async def health_check(self) -> bool:
        """Check OpenRouter is reachable (lightweight models endpoint)."""
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.url}/models",
                    headers=self._headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Fetch available models from OpenRouter."""
        if not self.api_key:
            return []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.url}/models",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                self._available_models = [m for m in models if m]
                return self._available_models
        except Exception as e:
            logger.warning("Failed to list OpenRouter models: %s", e)
            return []
