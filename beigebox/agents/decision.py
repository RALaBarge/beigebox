"""
Decision LLM — Future module.

This is where the routing/augmentation LLM will live.
A small, fast model (e.g., Qwen 3B MoE) reads user input and decides:
  - Does this need web search?
  - Route to coder model or general model?
  - Pull relevant conversation history via RAG?

Currently a no-op. The intercept layer in proxy.py is where this gets called.
"""

import logging

logger = logging.getLogger(__name__)


class DecisionAgent:
    """Placeholder for future decision LLM."""

    def __init__(self, model: str = "", backend_url: str = ""):
        self.model = model
        self.backend_url = backend_url
        self.enabled = False
        logger.info("DecisionAgent initialized (disabled — future feature)")

    def should_search(self, user_message: str) -> bool:
        """Determine if the user's message needs web search augmentation."""
        # Future: send user_message to small LLM, parse decision
        return False

    def select_model(self, user_message: str, default_model: str) -> str:
        """Choose which backend model to route to."""
        # Future: analyze message and pick coder vs general vs large
        return default_model

    def get_rag_context(self, user_message: str) -> str | None:
        """Retrieve relevant past conversation context."""
        # Future: query vector store, format as context
        return None
