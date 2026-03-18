"""
Base backend abstraction.
All backends implement this interface so the router can treat them uniformly.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BackendResponse:
    """Standardized response from any backend."""
    ok: bool
    status_code: int = 200
    data: dict = field(default_factory=dict)
    backend_name: str = ""
    latency_ms: float = 0.0
    cost_usd: float | None = None  # Only populated by API backends
    error: str = ""

    @property
    def content(self) -> str:
        """Extract assistant content from response data."""
        choices = self.data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return ""


class BaseBackend(abc.ABC):
    """
    Abstract base for LLM backends.
    Each backend knows how to forward requests and report health.
    """

    def __init__(self, name: str, url: str, timeout: int = 120, priority: int = 1,
                 timeout_ms: int | None = None):
        self.name = name
        self.url = url.rstrip("/")
        # timeout_ms (per-backend, ms) takes precedence over timeout (global, seconds).
        # timeout_ms=None means "use the global timeout passed as `timeout`".
        if timeout_ms is not None:
            self.timeout = timeout_ms / 1000.0
            logger.debug(
                "Backend '%s': using per-endpoint timeout %.1fs (timeout_ms=%d)",
                name, self.timeout, timeout_ms,
            )
        else:
            self.timeout = timeout
        self.priority = priority
        self._available_models: list[str] = []
        self.models_path = self._resolve_models_path()

    def _resolve_models_path(self) -> str:
        """
        Resolve model path with smart fallback chain:
        1. OLLAMA_DATA environment variable (if set) → {OLLAMA_DATA}/models
        2. MODELS_PATH environment variable (if set)
        3. backend.model_paths list in config (first existing path)
        4. backend.models_path single path in config
        5. Default: /mnt/storage/models

        This allows:
        - Reusing Ollama's existing model directory
        - ENV var overrides (Docker-friendly)
        - Multiple mount points with fallback (scattered models)
        - Single unified path (simple case)
        """
        import os
        from pathlib import Path

        # Check 1: OLLAMA_DATA env var (if using Ollama)
        ollama_data = os.getenv("OLLAMA_DATA")
        if ollama_data:
            ollama_models = os.path.join(ollama_data, "models")
            if Path(ollama_models).exists():
                logger.debug(f"Using OLLAMA_DATA models: {ollama_models}")
                return ollama_models

        # Check 2: MODELS_PATH env var (explicit override)
        models_path_env = os.getenv("MODELS_PATH")
        if models_path_env:
            logger.debug(f"Using MODELS_PATH from env: {models_path_env}")
            return models_path_env

        # Check 3 & 4: Config file paths (with fallback chain)
        try:
            from beigebox.config import get_config
            cfg = get_config()
            backend_cfg = cfg.get("backend", {})

            # Check for model_paths list (priority order, first existing wins)
            model_paths = backend_cfg.get("model_paths", [])
            if model_paths:
                for path in model_paths:
                    if Path(path).exists():
                        logger.debug(f"Using first existing model_paths entry: {path}")
                        return path
                logger.warning(f"No model_paths entries exist, trying fallback: {model_paths}")
                return model_paths[0]  # Return first even if doesn't exist (user's responsibility)

            # Check for single models_path (simple case)
            single_path = backend_cfg.get("models_path")
            if single_path:
                logger.debug(f"Using models_path from config: {single_path}")
                return single_path
        except Exception as e:
            logger.debug(f"Error reading config for model paths: {e}")

        # Check 5: Default fallback
        default = "/mnt/storage/models"
        logger.debug(f"Using default model path: {default}")
        return default

    @abc.abstractmethod
    async def forward(self, body: dict) -> BackendResponse:
        """
        Forward a chat completion request.
        Body is OpenAI-compatible format.
        Returns BackendResponse with data or error.
        """
        ...

    @abc.abstractmethod
    async def forward_stream(self, body: dict):
        """
        Forward a streaming chat completion request.
        Yields raw SSE lines (str).
        """
        ...

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if this backend is reachable and responsive."""
        ...

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Return list of available model names on this backend."""
        ...

    def supports_model(self, model: str) -> bool:
        """Check if this backend can serve the given model.

        Optimistic default when the model list is empty: assume yes and let
        the backend fail naturally. This avoids blocking requests at startup
        before list_models() has been called.
        """
        if not self._available_models:
            return True
        return model in self._available_models

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} url={self.url!r} priority={self.priority}>"
