"""
DGM Config Patcher — safe apply/revert for runtime_config.yaml changes.

The DGM loop proposes a change, applies it, measures outcomes, then keeps or
reverts. This module handles the apply/revert lifecycle safely.

Safety principles:
  1. ALLOWLIST: only keys on the whitelist can be modified. The proposer LLM
     can't change security settings, auth keys, or storage paths.
  2. ATOMIC writes: uses update_runtime_config() which does a temp-file rename,
     so a crash mid-write never corrupts runtime_config.yaml.
  3. SNAPSHOT before apply: the original value is stored so revert is exact.
  4. TYPE VALIDATION: proposed values are checked against expected types before
     application to prevent type errors at request time.

Scope 2 targets (config keys + system prompts):
  - models.default
  - models.profiles.routing
  - models.profiles.agentic
  - models.profiles.summary
  - decision_llm.temperature
  - decision_llm.system_prompt
  - operator.model
  - operator.max_iterations
  - operator.timeout
  - routing.session_cache.ttl_seconds
  - harness.stagger.operator_seconds
  - harness.stagger.model_seconds
  - auto_summarization.token_budget
  - auto_summarization.keep_last
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from beigebox.config import get_runtime_config, update_runtime_config

logger = logging.getLogger(__name__)


# ── Allowlist ──────────────────────────────────────────────────────────────
# Maps dot-notation key → (allowed_types, description).
# Only keys listed here can be modified by the DGM proposer.
# Add new keys here as DGM scope expands.

ALLOWED_KEYS: dict[str, tuple[tuple, str]] = {
    # Model selection — which model handles which role
    "models.default":           ((str,), "Global default model"),
    "models.profiles.routing":  ((str,), "Model used for routing decisions"),
    "models.profiles.agentic":  ((str,), "Model used for operator/tool use"),
    "models.profiles.summary":  ((str,), "Model used for auto-summarisation"),
    "operator.model":           ((str,), "Operator model (overrides profiles.agentic)"),

    # Decision LLM — the routing judge
    "decision_llm.temperature":   ((float, int), "Routing judge temperature (0.0–2.0)"),
    "decision_llm.system_prompt": ((str,), "Routing judge system prompt"),

    # Operator behaviour
    "operator.max_iterations":    ((int,), "Max tool-calling iterations per request"),
    "operator.timeout":           ((int, float), "Per-LLM-call timeout (seconds)"),

    # Routing
    "routing.session_cache.ttl_seconds": ((int,), "Session stickiness TTL (seconds)"),

    # Harness pacing
    "harness.stagger.operator_seconds": ((float, int), "Delay between operator launches"),
    "harness.stagger.model_seconds":    ((float, int), "Delay between model launches"),

    # Auto-summarisation
    "auto_summarization.token_budget":  ((int,), "Token count that triggers summarisation"),
    "auto_summarization.keep_last":     ((int,), "Recent turns kept intact during summary"),
}


@dataclass
class Patch:
    """
    A proposed config change.

    key:       Dot-notation config key (must be in ALLOWED_KEYS).
    value:     New value to apply.
    reasoning: Why the proposer thinks this change will help.
    """
    key: str
    value: Any
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "reasoning": self.reasoning}


@dataclass
class PatchResult:
    """
    Outcome of applying a patch.

    ok:            True if the patch was applied without errors.
    original:      The value that was in place before (used for revert).
    error:         Error message if ok=False.
    """
    ok: bool
    original: Any = None
    error: str = ""


class ConfigPatcher:
    """
    Applies and reverts config patches to runtime_config.yaml.

    Usage:
        patcher = ConfigPatcher()
        result = patcher.apply(Patch(key="models.default", value="qwen3:4b"))
        if result.ok:
            # ... run benchmark ...
            if not improved:
                patcher.revert(Patch(key="models.default", ...), result.original)
    """

    def validate(self, patch: Patch) -> str | None:
        """
        Validate a patch before applying.

        Returns None if valid, or an error message string if invalid.
        """
        # Must be on the allowlist
        if patch.key not in ALLOWED_KEYS:
            return (
                f"Key '{patch.key}' not in DGM allowlist. "
                f"Allowed: {sorted(ALLOWED_KEYS)}"
            )

        # Value must be the right type
        allowed_types, desc = ALLOWED_KEYS[patch.key]
        if not isinstance(patch.value, allowed_types):
            return (
                f"Key '{patch.key}' expects type(s) {[t.__name__ for t in allowed_types]}, "
                f"got {type(patch.value).__name__}"
            )

        # Range checks for numeric keys
        if patch.key == "decision_llm.temperature":
            if not (0.0 <= float(patch.value) <= 2.0):
                return f"Temperature must be in [0.0, 2.0], got {patch.value}"

        if patch.key in ("operator.max_iterations", "auto_summarization.keep_last"):
            if int(patch.value) < 1:
                return f"{patch.key} must be >= 1, got {patch.value}"

        if patch.key == "routing.session_cache.ttl_seconds":
            if int(patch.value) < 60:
                return f"Session TTL must be >= 60s, got {patch.value}"

        return None

    def apply(self, patch: Patch) -> PatchResult:
        """
        Apply a patch to runtime_config.yaml.

        Reads the current value first so we can revert exactly.
        Returns PatchResult with ok=True and the saved original value.
        """
        error = self.validate(patch)
        if error:
            logger.warning("dgm.patcher.validate_failed key=%s error=%s", patch.key, error)
            return PatchResult(ok=False, error=error)

        # Snapshot the current runtime config value
        rt = get_runtime_config()
        original = self._get_nested(rt, patch.key)

        # Apply via the safe atomic writer
        ok = update_runtime_config(patch.key, patch.value)
        if not ok:
            return PatchResult(
                ok=False,
                original=original,
                error=f"update_runtime_config returned False for key={patch.key}",
            )

        logger.info(
            "dgm.patcher.applied key=%s old=%r new=%r reason=%r",
            patch.key,
            original,
            patch.value,
            patch.reasoning[:80] if patch.reasoning else "",
        )
        return PatchResult(ok=True, original=original)

    def revert(self, patch: Patch, original: Any) -> bool:
        """
        Revert a patch to its original value.

        If the original was None (key didn't exist), the key is removed.

        Args:
            patch:    The patch that was applied.
            original: The value returned in PatchResult.original.

        Returns:
            True if revert succeeded.
        """
        ok = update_runtime_config(patch.key, original)
        logger.info(
            "dgm.patcher.reverted key=%s restored=%r ok=%s",
            patch.key,
            original,
            ok,
        )
        return ok

    def _get_nested(self, d: dict, dotkey: str) -> Any:
        """
        Retrieve a value from a nested dict using dot notation.
        Returns None if any intermediate key is missing.
        """
        parts = dotkey.split(".")
        current = d
        for part in parts:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current
