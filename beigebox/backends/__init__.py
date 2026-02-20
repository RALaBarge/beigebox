"""
Multi-backend support for BeigeBox.
Priority-based routing with fallback across Ollama, OpenRouter, etc.
"""
from beigebox.backends.router import MultiBackendRouter
from beigebox.backends.base import BaseBackend
from beigebox.backends.ollama import OllamaBackend
from beigebox.backends.openrouter import OpenRouterBackend

__all__ = [
    "MultiBackendRouter",
    "BaseBackend",
    "OllamaBackend",
    "OpenRouterBackend",
]
