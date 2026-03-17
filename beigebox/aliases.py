"""
Model alias resolver — maps virtual model names to real model IDs.

Config (config.yaml):
  aliases:
    fast: "llama3.2:3b"
    smart: "qwen3:30b"
    cheap: "llama3.2:1b"
    code: "qwen2.5-coder:7b"

Usage:
  resolver = AliasResolver(cfg)
  model = resolver.resolve("fast")          # → "llama3.2:3b"
  model = resolver.resolve("llama3.2:3b")  # → passthrough
"""

import logging

logger = logging.getLogger(__name__)


class AliasResolver:
    """Resolves virtual model aliases to concrete model IDs."""

    def __init__(self, cfg: dict):
        self._aliases: dict[str, str] = cfg.get("aliases", {})
        if self._aliases:
            logger.info("Model aliases loaded: %s", list(self._aliases.keys()))

    def resolve(self, model: str) -> str:
        """Return the concrete model ID, or the original string if not an alias."""
        if not model:
            return model
        resolved = self._aliases.get(model, model)
        if resolved != model:
            logger.debug("Alias resolved: %r → %r", model, resolved)
        return resolved

    def list_aliases(self) -> dict[str, str]:
        """Return the full alias → model mapping."""
        return dict(self._aliases)
