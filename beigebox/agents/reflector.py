"""
Background Reflector — slow-path analysis for temporal layering.

After each autonomous turn, the reflector analyses the turn output and queues
a short insight for injection into the next turn's context. Runs as an
asyncio.Task (fire-and-forget) — never blocks the primary turn loop.

Inspired by DeerFlow's reflection pattern (oss/deer-flow/backend/src/agents/).

Config (config.yaml):
    operator:
      reflection:
        enabled: false
        model: ""       # e.g. qwen2.5:0.5b — defaults to operator model
        timeout: 20
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a background reflection agent for an autonomous coding assistant.
Analyse the completed agent turn output and produce ONE concise insight
(2–3 sentences maximum) that will help the NEXT turn work better.

Focus on: what worked well, what to avoid repeating, implicit constraints
discovered, or a concrete suggestion for the next step.

Return ONLY the insight — no headers, no bullet points, no preamble.\
"""


class Reflector:
    """
    Async fire-and-forget reflector.

    Usage:
        await reflector.reflect_async(turn_answer, cur_question, step_name)
        # ... primary turn runs ...
        insight = reflector.consume_insight()  # None if not ready yet
        if insight:
            cur_question += f"\\n\\n## Background reflection\\n{insight}"
    """

    def __init__(self, model: str, backend_url: str, timeout: int = 20):
        self._model = model
        self._backend_url = backend_url.rstrip("/")
        self._timeout = timeout
        self._enabled = True
        self._insight: str | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def reflect_async(
        self,
        turn_answer: str,
        cur_question: str,
        step_name: str,
    ) -> None:
        """
        Fire background reflection. Cancels any in-flight task from last turn.
        Non-blocking — returns immediately.
        """
        if not self._enabled:
            return
        if self._task and not self._task.done():
            self._task.cancel()
        self._insight = None
        self._task = asyncio.ensure_future(
            self._do_reflect(turn_answer, cur_question, step_name)
        )

    async def _do_reflect(
        self,
        turn_answer: str,
        cur_question: str,
        step_name: str,
    ) -> None:
        """Actual async LLM call. Stores result in self._insight on success."""
        user_msg = (
            f"Step just completed: {step_name}\n\n"
            f"Agent output:\n{turn_answer[:1500]}\n\n"
            f"Context that was used:\n{cur_question[:800]}"
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "temperature": 0.3,
            "max_tokens":  150,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._backend_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                from beigebox.response_normalizer import normalize_response
                insight = normalize_response(resp.json()).content.strip()
                if insight:
                    self._insight = insight
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Reflector LLM call failed: %s", exc)

    def consume_insight(self) -> str | None:
        """
        Return and clear the pending insight.
        Returns None if reflection is not done yet or not available.
        """
        if self._task is None or not self._task.done():
            return None
        insight = self._insight
        self._insight = None
        return insight

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "Reflector":
        """Build from config. Returns disabled no-op if not configured."""
        try:
            cfg = get_config()
            rt  = get_runtime_config()
            ref_cfg = cfg.get("operator", {}).get("reflection", {})
            enabled = rt.get(
                "reflection_enabled",
                ref_cfg.get("enabled", False),
            )
            if not enabled:
                r = cls.__new__(cls)
                r._enabled = False
                r._model = ""
                r._backend_url = ""
                r._timeout = 20
                r._insight = None
                r._task = None
                return r

            backend_url = (
                cfg.get("embedding", {}).get("backend_url")
                or cfg.get("backend", {}).get("url", "http://localhost:11434")
            )
            model = (
                ref_cfg.get("model")
                or rt.get("default_model")
                or cfg.get("backend", {}).get("default_model", "")
            )
            timeout = int(ref_cfg.get("timeout", 20))
            return cls(model=model, backend_url=backend_url, timeout=timeout)
        except Exception as exc:
            logger.warning("Reflector.from_config failed, reflection disabled: %s", exc)
            r = cls.__new__(cls)
            r._enabled = False
            r._model = ""
            r._backend_url = ""
            r._timeout = 20
            r._insight = None
            r._task = None
            return r
