"""
llama.cpp backend plugin.

Drop-in HTTP server for llama.cpp. Implements OpenAI-compatible /v1/chat/completions.

Setup:
    # All backends use the shared MODELS_PATH (e.g., /mnt/storage/models)
    # This path is configured in config.yaml backend.models_path

    docker run -d -p 8000:8000 \
      -v /mnt/storage/models:/models \
      ghcr.io/ggerganov/llama.cpp:latest-server \
      --models-path /models

Then add to config.yaml:
    backends:
      - provider: llama_cpp
        name: llama-cpp-local
        url: http://localhost:8000
        priority: 1
        # Uses backend.models_path automatically
"""

import logging
import httpx
from beigebox.backends.base import BaseBackend, BackendResponse
from beigebox.config import get_config

logger = logging.getLogger(__name__)


class LlamaCppBackend(BaseBackend):
    """
    Interface to llama.cpp HTTP server.

    llama.cpp: https://github.com/ggerganov/llama.cpp
    Extremely lightweight C++ inference engine (~15KB binary), zero dependencies.

    Models are shared via backend.models_path from config.yaml, so all backends
    (Ollama, llama.cpp, Mini-SGLang, etc.) can access the same model files.
    """

    async def forward(self, body: dict) -> BackendResponse:
        """Forward chat completion request to llama.cpp server."""
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
            logger.error(f"llama.cpp request failed: {e}")
            return BackendResponse(
                ok=False,
                status_code=500,
                backend_name=self.name,
                error=str(e),
            )

    async def forward_stream(self, body: dict):
        """Stream chat completion from llama.cpp."""
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
            logger.error(f"llama.cpp stream failed: {e}")
            yield f"data: {{'type': 'error', 'message': '{str(e)}'}}\n\n"

    async def health_check(self) -> bool:
        """Check if llama.cpp server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models from llama.cpp server."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(f"{self.url}/v1/models")
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                self._available_models = models
                return models
        except Exception as e:
            logger.warning(f"Could not fetch model list from llama.cpp: {e}")
            return []
