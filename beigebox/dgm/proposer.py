"""
DGM Proposer — LLM-driven config change proposals.

Asks a small model (3B) to look at the current config state and recent iteration
history, then suggest ONE specific change to try next.

Design choices:
  - ONE change per proposal: easier to measure causality. Multi-change proposals
    make it impossible to know which change caused an improvement (or regression).
  - Structured output: the model must return JSON. We give it a strict template.
  - History-aware: the model sees what was tried and what happened so it doesn't
    repeat failed experiments.
  - Rubric-aware: the model sees the active rubric so it can reason about what
    "better" means for this iteration.
  - Scope-constrained: the prompt explicitly lists the allowed keys so the model
    doesn't hallucinate keys that don't exist or aren't in the allowlist.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from beigebox.config import get_config, get_runtime_config
from beigebox.dgm.patcher import ALLOWED_KEYS, Patch
from beigebox.dgm.rubrics import Rubric
from beigebox.utils.json_parse import extract_json_object

logger = logging.getLogger(__name__)

# System prompt for the proposer model. Tight and directive — 3B models work
# best with clear, short instructions rather than long prose.
_PROPOSER_SYSTEM = """\
You are an AI system optimizer. You propose ONE config change that may improve \
response quality. You must output valid JSON and nothing else.

Rules:
- Propose exactly one change per response
- Only use keys from the provided allowed list
- Do not repeat changes that have already been tried
- Consider the active evaluation rubric when deciding what to improve
- Output format: {"key": "...", "value": ..., "reasoning": "one sentence"}"""

_PROPOSER_PROMPT = """\
ACTIVE RUBRIC: {rubric_name}
Rubric description: {rubric_description}

CURRENT CONFIG STATE (runtime overrides only):
{runtime_config}

RECENT ITERATION HISTORY (last {n_history} iterations):
{history}

ALLOWED CONFIG KEYS:
{allowed_keys}

Based on the history and rubric, propose one config change to try next.
Avoid keys that have been recently tried with no improvement.
Output JSON only: {{"key": "...", "value": ..., "reasoning": "..."}}"""


@dataclass
class Proposal:
    """A proposed config change from the proposer model."""

    patch: Patch
    raw_response: str       # raw LLM output (for debugging)
    latency_ms: float


class DGMProposer:
    """
    Generates config change proposals using a small LLM.

    Args:
        proposer_model: Model to use for proposals (3B is fine, instructions are tight).
        backend_url:    OpenAI-compatible endpoint.
        timeout:        Per-call timeout in seconds.
        temperature:    Proposer temperature. Slightly higher (0.4) for exploration.
    """

    def __init__(
        self,
        proposer_model: str = "llama3.2:3b",
        backend_url: str = "http://localhost:1337",
        timeout: float = 30.0,
        temperature: float = 0.4,
    ) -> None:
        self._model = proposer_model
        self._url = backend_url.rstrip("/") + "/v1/chat/completions"
        self._timeout = timeout
        self._temperature = temperature

    async def propose(
        self,
        rubric: Rubric,
        history: list[dict],
        n_history: int = 10,
    ) -> Proposal | None:
        """
        Ask the model to propose one config change.

        Args:
            rubric:    Active rubric — tells the model what "better" means.
            history:   List of past iteration dicts (most recent last).
            n_history: How many recent iterations to show the model.

        Returns:
            Proposal if parsing succeeded, None if the model output was unusable.
        """
        rt = get_runtime_config()

        # Trim history to the last n_history items to stay within context
        recent = history[-n_history:] if history else []
        history_text = self._format_history(recent)

        # Build the allowed keys list with descriptions
        keys_text = "\n".join(
            f"  {k}: {desc}" for k, (_, desc) in sorted(ALLOWED_KEYS.items())
        )

        prompt = _PROPOSER_PROMPT.format(
            rubric_name=rubric.name,
            rubric_description=rubric.description,
            runtime_config=json.dumps(rt, indent=2) if rt else "{}",
            n_history=len(recent),
            history=history_text or "  (no history yet)",
            allowed_keys=keys_text,
        )

        t_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url,
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": _PROPOSER_SYSTEM},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": self._temperature,
                        "max_tokens": 300,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.error("dgm.proposer.call_failed: %s", exc)
            return None

        latency_ms = (time.monotonic() - t_start) * 1000
        raw = resp.json()["choices"][0]["message"]["content"]

        return self._parse(raw, latency_ms)

    def _parse(self, raw: str, latency_ms: float) -> Proposal | None:
        """Parse the model's JSON output into a Proposal."""
        try:
            parsed = extract_json_object(raw)
        except Exception as exc:
            logger.warning("dgm.proposer.parse_failed raw=%r error=%s", raw[:200], exc)
            return None

        key = parsed.get("key", "")
        value = parsed.get("value")
        reasoning = str(parsed.get("reasoning", ""))

        if not key or value is None:
            logger.warning("dgm.proposer.missing_fields parsed=%r", parsed)
            return None

        # Coerce value type to match the allowlist expectation
        if key in ALLOWED_KEYS:
            allowed_types, _ = ALLOWED_KEYS[key]
            primary_type = allowed_types[0]
            try:
                value = primary_type(value)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "dgm.proposer.type_coercion_failed key=%s value=%r error=%s",
                    key, value, exc,
                )
                return None

        patch = Patch(key=key, value=value, reasoning=reasoning)
        logger.info(
            "dgm.proposer.proposed key=%s value=%r latency_ms=%.0f",
            key, value, latency_ms,
        )
        return Proposal(patch=patch, raw_response=raw, latency_ms=latency_ms)

    def _format_history(self, history: list[dict]) -> str:
        """Format iteration history as a compact text block for the prompt."""
        if not history:
            return ""
        lines = []
        for i, h in enumerate(history, 1):
            outcome = "KEPT" if h.get("kept") else "REVERTED"
            lines.append(
                f"  [{i}] {outcome} key={h.get('key')} "
                f"value={h.get('value')} "
                f"rubric={h.get('rubric')} "
                f"winner={h.get('winner')} "
                f"confidence={h.get('confidence', 0):.2f}"
            )
        return "\n".join(lines)
