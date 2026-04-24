"""
DGM Judge — pairwise response comparison using a small (3B) model.

Why pairwise instead of absolute scoring?
  Absolute: "Rate this response 1-10" — small models are inconsistent and
  anchored; the same response gets wildly different scores across rubrics.

  Pairwise: "Which of A or B is better?" — even a 3B model is reliably good
  at this. It mirrors how humans evaluate, it's less sensitive to calibration
  drift, and it's harder to game because the model sees both responses at once.

The judge is given a rotating rubric (see rubrics.py) so it evaluates from a
different angle each time, mitigating Goodhart's Law.

Output: JudgeVerdict with winner ("A" | "B" | "tie"), confidence, and reasoning.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from beigebox.dgm.rubrics import Rubric
from beigebox.utils.json_parse import extract_json_object

logger = logging.getLogger(__name__)

# Judge system prompt — instructs the 3B model on its role and output format.
# Kept tight because small models lose track of long instructions.
_JUDGE_SYSTEM = """\
You are a response quality judge. You will be given a user request and two \
responses (A and B). Evaluate them using the provided rubric, then output \
a JSON verdict. Be objective and concise in your reasoning.

Output format (JSON only, no other text):
{
  "winner": "A" or "B" or "tie",
  "confidence": 0.0 to 1.0,
  "reasoning": "one sentence explaining your choice"
}"""

# Template for the evaluation prompt shown to the judge.
_JUDGE_PROMPT = """\
RUBRIC: {rubric_description}

USER REQUEST:
{request}

RESPONSE A:
{response_a}

RESPONSE B:
{response_b}

Which response better satisfies the rubric? Output JSON only."""


@dataclass
class JudgeVerdict:
    """Result of a pairwise judge evaluation."""

    winner: Literal["A", "B", "tie"]
    confidence: float           # 0.0–1.0
    reasoning: str
    rubric_name: str
    latency_ms: float           # Judge call latency

    def a_wins(self) -> bool:
        return self.winner == "A"

    def b_wins(self) -> bool:
        return self.winner == "B"

    def is_tie(self) -> bool:
        return self.winner == "tie"

    def to_dict(self) -> dict:
        return {
            "winner": self.winner,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "rubric_name": self.rubric_name,
            "latency_ms": self.latency_ms,
        }


class DGMJudge:
    """
    Pairwise response judge for DGM iterations.

    Sends both responses to a small LLM with a rotating rubric and asks it to
    pick the winner. Multiple calls are averaged to reduce noise (best_of_n).

    Args:
        judge_model:  Model name for judge calls (defaults to 3B routing model).
        backend_url:  OpenAI-compatible endpoint (usually BeigeBox itself).
        timeout:      Per-call timeout in seconds.
        best_of_n:    Number of judge calls to average per verdict (default 3).
                      Higher = more reliable, slower. 3 is a good default for 3B.
        temperature:  Judge temperature. Low (0.1) for consistency.
    """

    def __init__(
        self,
        judge_model: str = "qwen3:4b",
        backend_url: str = "http://localhost:1337",
        timeout: float = 30.0,
        best_of_n: int = 3,
        temperature: float = 0.1,
    ) -> None:
        self._model = judge_model
        self._url = backend_url.rstrip("/") + "/v1/chat/completions"
        self._timeout = timeout
        self._best_of_n = max(1, best_of_n)
        self._temperature = temperature

    async def compare(
        self,
        request: str,
        response_a: str,
        response_b: str,
        rubric: Rubric,
    ) -> JudgeVerdict:
        """
        Compare two responses using the given rubric.

        Runs best_of_n comparisons and returns the majority verdict.
        If all calls fail, returns a tie with 0.0 confidence.

        Args:
            request:    The original user request text.
            response_a: The "before" response (baseline / current config).
            response_b: The "after" response (proposed config change).
            rubric:     Active rubric from the rotator.

        Returns:
            JudgeVerdict with winner, confidence, and reasoning.
        """
        prompt = _JUDGE_PROMPT.format(
            rubric_description=rubric.description,
            request=request,
            response_a=response_a,
            response_b=response_b,
        )

        verdicts: list[JudgeVerdict] = []
        t_start = time.monotonic()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self._best_of_n):
                try:
                    verdict = await self._single_call(client, prompt, rubric.name)
                    verdicts.append(verdict)
                    logger.debug(
                        "dgm.judge attempt=%d winner=%s confidence=%.2f",
                        attempt + 1,
                        verdict.winner,
                        verdict.confidence,
                    )
                except Exception as exc:
                    logger.warning("dgm.judge attempt=%d failed: %s", attempt + 1, exc)

        total_ms = (time.monotonic() - t_start) * 1000

        if not verdicts:
            logger.error("dgm.judge all %d attempts failed — returning tie", self._best_of_n)
            return JudgeVerdict(
                winner="tie",
                confidence=0.0,
                reasoning="All judge calls failed",
                rubric_name=rubric.name,
                latency_ms=total_ms,
            )

        return self._aggregate(verdicts, rubric.name, total_ms)

    async def _single_call(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        rubric_name: str,
    ) -> JudgeVerdict:
        """Make a single judge LLM call and parse the verdict."""
        t_start = time.monotonic()
        resp = await client.post(
            self._url,
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": self._temperature,
                "max_tokens": 200,
                "stream": False,
            },
        )
        resp.raise_for_status()
        latency_ms = (time.monotonic() - t_start) * 1000

        from beigebox.response_normalizer import normalize_response
        content = normalize_response(resp.json()).content
        parsed = extract_json_object(content)

        winner = str(parsed.get("winner", "tie")).upper()
        if winner not in ("A", "B", "TIE"):
            winner = "tie"

        return JudgeVerdict(
            winner=winner,  # type: ignore[arg-type]
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=str(parsed.get("reasoning", "")),
            rubric_name=rubric_name,
            latency_ms=latency_ms,
        )

    def _aggregate(
        self,
        verdicts: list[JudgeVerdict],
        rubric_name: str,
        total_ms: float,
    ) -> JudgeVerdict:
        """
        Aggregate multiple verdicts into a single majority verdict.

        Weighted by confidence: a high-confidence B win counts more than a
        low-confidence B win. This is more robust than simple majority voting.
        """
        weights: dict[str, float] = {"A": 0.0, "B": 0.0, "tie": 0.0}
        for v in verdicts:
            weights[v.winner] += v.confidence

        winner = max(weights, key=lambda k: weights[k])

        # Normalised confidence: how much did the winner dominate?
        total_weight = sum(weights.values()) or 1.0
        confidence = weights[winner] / total_weight

        # Guard: shouldn't be called with empty list, but be defensive
        if not verdicts:
            return JudgeVerdict(
                winner="tie", confidence=0.0, reasoning="no verdicts",
                rubric_name=rubric_name, latency_ms=total_ms,
            )

        # Use the reasoning from the highest-confidence individual verdict
        best = max(verdicts, key=lambda v: v.confidence)

        logger.info(
            "dgm.judge.aggregate winner=%s confidence=%.2f rubric=%s "
            "votes(A=%.2f B=%.2f tie=%.2f) n=%d",
            winner,
            confidence,
            rubric_name,
            weights["A"],
            weights["B"],
            weights["tie"],
            len(verdicts),
        )

        return JudgeVerdict(
            winner=winner,  # type: ignore[arg-type]
            confidence=confidence,
            reasoning=best.reasoning,
            rubric_name=rubric_name,
            latency_ms=total_ms,
        )
