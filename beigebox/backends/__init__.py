"""
Multi-backend support for BeigeBox.
Priority-based routing with fallback across Ollama, OpenRouter, OpenAI-compatible, etc.
"""
from beigebox.backends.router import MultiBackendRouter
from beigebox.backends.base import BaseBackend
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend
from beigebox.backends.openai_compat import OpenAICompatibleBackend

__all__ = [
    "MultiBackendRouter",
    "BaseBackend",
    "OllamaBackend",
    "OpenRouterBackend",
    "OpenAICompatibleBackend",
]
