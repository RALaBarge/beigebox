"""
Adversarial Context Pruner — strips irrelevant content from operator turn context.

Runs a cheap LLM call between autonomous turns to compress cur_question down to
only what the next step actually needs. Named "adversarial" because it actively
tries to remove content rather than preserve it.

Never blocks the pipeline — returns original cur_question on any error/timeout.

Config (config.yaml):
    operator:
      context_pruning:
        enabled: false
        model: ""       # defaults to operator model
        timeout: 8
"""
from __future__ import annotations

import json
import logging

import httpx

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a context pruner for an autonomous agent harness.
You receive a working context block and the name of the next step to execute.
Your job: compress the context to only what is needed for that specific step.

Remove: completed-step details, redundant objectives, verbose progress notes,
        repeated file path listings, anything already done.
Keep:   the objective, the next-step instruction, relevant file paths,
        hard constraints, and any warnings or blockers.

Return ONLY the compressed context — no commentary, no explanation, no wrapper.
If the context is already concise (under 300 words), return it unchanged.\
"""


class ContextPruner:
    """Cheap LLM pass that compresses autonomous turn context between turns."""

    def __init__(self, model: str, backend_url: str, timeout: int = 8):
        self._model = model
        self._backend_url = backend_url.rstrip("/")
        self._timeout = timeout
        self._enabled = True

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def prune(self, cur_question: str, next_step_name: str) -> str:
        """
        Compress cur_question to what's needed for next_step_name.

        Sync (httpx). Always returns original on error/timeout.
        Call via run_in_executor from async context.
        """
        if not self._enabled or not cur_question.strip():
            return cur_question

        user_msg = (
            f"Next step: {next_step_name}\n\n"
            f"Context to compress:\n{cur_question}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens":  512,
            "stream": False,
        }

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._backend_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                pruned = (
                    data["choices"][0]["message"]["content"].strip()
                )
                # Sanity check — must be non-empty and shorter
                if pruned and len(pruned) < len(cur_question):
                    return pruned
                return cur_question
        except Exception as exc:
            logger.debug("Context pruner failed (returning original): %s", exc)
            return cur_question

    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "ContextPruner":
        """Build from config.yaml. Returns a disabled no-op pruner if not configured."""
        try:
            cfg = get_config()
            rt  = get_runtime_config()
            prune_cfg = cfg.get("operator", {}).get("context_pruning", {})
            enabled = rt.get(
                "context_pruning_enabled",
                prune_cfg.get("enabled", False),
            )
            if not enabled:
                pruner = cls.__new__(cls)
                pruner._enabled = False
                pruner._model = ""
                pruner._backend_url = ""
                pruner._timeout = 8
                return pruner

            backend_url = (
                cfg.get("embedding", {}).get("backend_url")
                or cfg.get("backend", {}).get("url", "http://localhost:11434")
            )
            model = (
                prune_cfg.get("model")
                or rt.get("default_model")
                or cfg.get("backend", {}).get("default_model", "")
            )
            timeout = int(prune_cfg.get("timeout", 8))
            return cls(model=model, backend_url=backend_url, timeout=timeout)
        except Exception as exc:
            logger.warning("ContextPruner.from_config failed, pruning disabled: %s", exc)
            pruner = cls.__new__(cls)
            pruner._enabled = False
            pruner._model = ""
            pruner._backend_url = ""
            pruner._timeout = 8
            return pruner
