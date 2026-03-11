"""
ExecuTorch backend plugin.

Drop-in HTTP server wrapper for Meta's ExecuTorch embedded LLM engine.

ExecuTorch: https://github.com/pytorch/executorch
50KB base footprint, runs on everything from microcontrollers to high-end devices.

This is a template for wrapping ExecuTorch with an HTTP interface.
The actual HTTP server would need to be created separately.

Setup:
    # You'd need to build an HTTP wrapper around ExecuTorch
    # See: https://github.com/pytorch/executorch
    python server.py --model model.pte --port 8000

Then add to config.yaml:
    backends:
      - provider: executorch
        name: executorch-mobile
        url: http://localhost:8000
        priority: 3
"""

import logging
import httpx
from beigebox.backends.base import BaseBackend, BackendResponse

logger = logging.getLogger(__name__)


class ExecutorchBackend(BaseBackend):
    """
    Interface to ExecuTorch HTTP server wrapper.

    ExecuTorch: https://github.com/pytorch/executorch
    Meta's production-ready embedded LLM engine.
    50KB footprint, runs on devices from phones to microcontrollers.
    """

    async def forward(self, body: dict) -> BackendResponse:
        """Forward chat completion request to ExecuTorch server."""
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
            logger.error(f"ExecuTorch request failed: {e}")
            return BackendResponse(
                ok=False,
                status_code=500,
                backend_name=self.name,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Stream chat completion from ExecuTorch."""
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
            logger.error(f"ExecuTorch stream failed: {e}")
            yield f"data: {{'type': 'error', 'message': '{str(e)}'}}\n\n"

    async def health_check(self) -> bool:
        """Check if ExecuTorch server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models from ExecuTorch server."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.url}/v1/models")
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                self._available_models = models
                return models
        except Exception as e:
            logger.warning(f"Could not fetch model list from ExecuTorch: {e}")
            return []
