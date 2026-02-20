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

    def __init__(self, name: str, url: str, timeout: int = 120, priority: int = 1):
        self.name = name
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.priority = priority
        self._available_models: list[str] = []

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
        """Check if this backend can serve the given model."""
        # If we haven't fetched models yet, assume yes (try and fail)
        if not self._available_models:
            return True
        return model in self._available_models

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} url={self.url!r} priority={self.priority}>"
