"""
Shadow Agent — counterfactual parallel operator run.

Runs alongside the primary operator on autonomous turn 0 only, with an
alternative assumption injected into the prompt. If the shadow's plan diverges
meaningfully from the primary's (Jaccard similarity below threshold), it is
surfaced as an alternative_plan SSE event for the user to consider.

Budget: shadow uses max_tool_calls=3 (vs primary's ~10).
Timing: fire-and-forget on turn 0; collected with 2s timeout after primary streams.

Config (config.yaml):
    harness:
      shadow_agents:
        enabled: false
        model: ""                 # defaults to operator model
        timeout: 30
        max_tool_calls: 3
        divergence_threshold: 0.3
"""
from __future__ import annotations

import asyncio
import logging
import re

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

_SHADOW_PREFIX = (
    "[Shadow run — challenge the default approach]\n"
    "Before committing to the obvious solution, consider: what if the standard "
    "approach has a hidden cost or complexity? What alternative architecture or "
    "method would a senior engineer prefer? Explore one meaningful alternative "
    "in your plan, then proceed with the better option.\n\n"
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


class ShadowAgent:
    """
    Counterfactual shadow operator. Turn-0 only. Fire-and-forget.

    Usage in api_harness_autonomous:
        _shadow = ShadowAgent.from_config()
        if turn_n == 0 and _shadow.enabled:
            _shadow_task = asyncio.ensure_future(_shadow.run_shadow(question, vs))
        # ... stream primary normally ...
        if turn_n == 0 and _shadow.enabled:
            shadow_answer = await _shadow.collect(_shadow_task)
            if shadow_answer and ShadowAgent.diverges(final_answer or "", shadow_answer):
                yield f"data: {json.dumps({'type': 'alternative_plan', 'content': shadow_answer})}\\n\\n"
    """

    def __init__(
        self,
        model: str,
        backend_url: str,
        timeout: int = 30,
        max_tool_calls: int = 3,
        divergence_threshold: float = 0.3,
    ):
        self._model = model
        self._backend_url = backend_url
        self._timeout = timeout
        self._max_tool_calls = max_tool_calls
        self._divergence_threshold = divergence_threshold
        self._enabled = True

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def run_shadow(self, question: str, vector_store) -> str | None:
        """
        Run operator with counterfactual prompt injection.
        Returns answer string or None on failure/timeout.
        """
        try:
            from beigebox.storage.vector_store import VectorStore
            from beigebox.agents.operator import Operator

            shadow_question = _SHADOW_PREFIX + question

            def _run_sync():
                op = Operator(
                    vector_store=vector_store,
                    model_override=self._model or None,
                    max_tool_calls=self._max_tool_calls,
                )
                return op.run(shadow_question, history=None)

            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_sync),
                timeout=self._timeout,
            )
            return result or None
        except asyncio.TimeoutError:
            logger.debug("Shadow agent timed out after %ds", self._timeout)
            return None
        except Exception as exc:
            logger.debug("Shadow agent failed: %s", exc)
            return None

    async def collect(self, task: asyncio.Task, wait: float = 2.0) -> str | None:
        """Collect result from a running shadow task with a short timeout."""
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except (asyncio.TimeoutError, Exception):
            return None

    # ------------------------------------------------------------------

    @staticmethod
    def diverges(primary: str, shadow: str, threshold: float = 0.3) -> bool:
        """
        True if primary and shadow share < (1-threshold) Jaccard word overlap.
        threshold=0.3 → diverges when < 70% word overlap (meaningful alternative).
        Pure stdlib.
        """
        if not primary or not shadow:
            return False
        pw = _words(primary)
        sw = _words(shadow)
        if not pw or not sw:
            return False
        intersection = len(pw & sw)
        union = len(pw | sw)
        similarity = intersection / union if union else 1.0
        return similarity < (1.0 - threshold)

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "ShadowAgent":
        """Build from config. Returns disabled no-op if not configured."""
        try:
            cfg = get_config()
            rt  = get_runtime_config()
            sh_cfg = cfg.get("harness", {}).get("shadow_agents", {})
            enabled = rt.get(
                "shadow_agents_enabled",
                sh_cfg.get("enabled", False),
            )
            if not enabled:
                s = cls.__new__(cls)
                s._enabled = False
                s._model = ""
                s._backend_url = ""
                s._timeout = 30
                s._max_tool_calls = 3
                s._divergence_threshold = 0.3
                return s

            backend_url = (
                cfg.get("backend", {}).get("url", "http://localhost:11434")
            )
            model = (
                sh_cfg.get("model")
                or rt.get("default_model")
                or cfg.get("backend", {}).get("default_model", "")
            )
            return cls(
                model=model,
                backend_url=backend_url,
                timeout=int(sh_cfg.get("timeout", 30)),
                max_tool_calls=int(sh_cfg.get("max_tool_calls", 3)),
                divergence_threshold=float(sh_cfg.get("divergence_threshold", 0.3)),
            )
        except Exception as exc:
            logger.warning("ShadowAgent.from_config failed, shadow disabled: %s", exc)
            s = cls.__new__(cls)
            s._enabled = False
            s._model = ""
            s._backend_url = ""
            s._timeout = 30
            s._max_tool_calls = 3
            s._divergence_threshold = 0.3
            return s
