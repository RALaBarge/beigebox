"""
Mini-SGLang backend plugin.

Drop-in HTTP server for Mini-SGLang (lightweight serving framework).

Setup:
    git clone https://github.com/sgl-project/mini-sglang
    cd mini-sglang
    python -m mini_sglang.server --host 0.0.0.0 --port 8000

Then add to config.yaml:
    backends:
      - provider: mini_sglang
        name: mini-sglang
        url: http://localhost:8000
        priority: 2
"""

import logging
import httpx
from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)


class MiniSglangBackend(BaseBackend):
    """
    Interface to Mini-SGLang HTTP server.

    Mini-SGLang: https://github.com/sgl-project/mini-sglang
    Clean, modular serving framework (only 5k Python lines).
    Great for understanding and customizing inference serving.
    """

    async def forward(self, body: dict) -> BackendResponse:
        """Forward chat completion request to Mini-SGLang server."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.url}/v1/chat/completions",
                    json=body,
                )
                resp.raise_for_status()
                return BackendResponse(
                    ok=True,
                    status_code=resp.status_code,
                    data=resp.json(),
                    backend_name=self.name,
                )
        except Exception as e:
            logger.error(f"Mini-SGLang request failed: {e}")
            return BackendResponse(
                ok=False,
                status_code=500,
                backend_name=self.name,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Stream chat completion from Mini-SGLang."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.url}/v1/chat/completions",
                    json=body,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line
        except Exception as e:
            logger.error(f"Mini-SGLang stream failed: {e}")
            yield f"data: {{'type': 'error', 'message': '{str(e)}'}}\n\n"

    async def health_check(self) -> bool:
        """Check if Mini-SGLang server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models from Mini-SGLang server."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.url}/v1/models")
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                self._available_models = models
                return models
        except Exception as e:
            logger.warning(f"Could not fetch model list from Mini-SGLang: {e}")
            return []
