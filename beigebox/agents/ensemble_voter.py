"""
EnsembleVoter — parallel model responses with LLM judge.

Send the same prompt to N models in parallel. Collect all responses.
Ask a judge LLM to evaluate which is best. Stream results back.

Usage:
    voter = EnsembleVoter(models=["llama3.2:3b", "mistral:7b"], judge_model="llama3.2:3b")
    async for event in voter.vote(prompt):
        print(event)   # {type:"dispatch"|"result"|"evaluate"|"finish", ...}
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)


def _ev(type_: str, **kw) -> dict:
    """Create event dict with timestamp."""
    return {"type": type_, "ts": round(time.monotonic() * 1000), **kw}


class EnsembleVoter:
    """Vote on responses from multiple models using an LLM judge."""

    def __init__(
        self,
        models: list[str],
        judge_model: str | None = None,
        temperature: float = 0.2,
    ):
        cfg = get_config()
        self.cfg = cfg
        self.backend_url = cfg["backend"]["url"].rstrip("/")
        self.models = models
        self.judge_model = (
            judge_model
            or cfg.get("operator", {}).get("model")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self.temperature = temperature

    async def vote(self, prompt: str) -> AsyncGenerator[dict, None]:
        """
        Send prompt to all models in parallel, then judge best response.

        Yields:
          {type:"dispatch", model_count:N}
          {type:"result", model:"...", response:"...", latency_ms:123}
          ...repeat for each model...
          {type:"evaluate", winner:"...", reasoning:"...", all_responses:[...]}
          {type:"finish", best_response:"...", winner:"...", verdict:"..."}
        """
        yield _ev("start", prompt=prompt, models=self.models, judge=self.judge_model)

        # ── 1. Dispatch ────────────────────────────────────────────────────────
        yield _ev("dispatch", model_count=len(self.models))

        responses = await self._query_all_models(prompt)

        for model_name, response, latency in responses:
            yield _ev(
                "result",
                model=model_name,
                response=response,
                latency_ms=latency,
            )

        if not responses:
            yield _ev("error", message="No responses from any model")
            return

        # ── 2. Judge ───────────────────────────────────────────────────────────
        try:
            verdict = await self._judge_responses(prompt, responses)
        except Exception as e:
            yield _ev("error", message=f"Judge evaluation failed: {e}")
            return

        winner = verdict.get("winner", "")
        reasoning = verdict.get("reasoning", "")
        best_response = next(
            (r for m, r, _ in responses if m == winner), responses[0][1] if responses else ""
        )

        yield _ev(
            "evaluate",
            winner=winner,
            reasoning=reasoning,
            all_responses=[
                {"model": m, "response": r} for m, r, _ in responses
            ],
        )

        yield _ev(
            "finish",
            winner=winner,
            best_response=best_response,
            verdict=reasoning,
        )

    # ── Model queries ──────────────────────────────────────────────────────────

    async def _query_all_models(
        self, prompt: str
    ) -> list[tuple[str, str, int]]:
        """Query all models in parallel. Returns [(model_name, response, latency_ms), ...]"""
        tasks = [
            self._query_model(model, prompt) for model in self.models
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _query_model(self, model: str, prompt: str) -> tuple[str, str, int]:
        """Query a single model. Returns (model_name, response, latency_ms)."""
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                latency = int((time.time() - start) * 1000)
                return (model, content, latency)
        except Exception as e:
            logger.error(f"Failed to query {model}: {e}")
            latency = int((time.time() - start) * 1000)
            return (model, f"Error: {str(e)}", latency)

    # ── Judge evaluation ───────────────────────────────────────────────────────

    async def _judge_responses(
        self, prompt: str, responses: list[tuple[str, str, int]]
    ) -> dict:
        """
        Ask judge model to pick the best response.
        Returns: {winner: "model_name", reasoning: "why this is best"}
        """
        models_list = ", ".join([m for m, _, _ in responses])
        responses_text = "\n\n".join(
            [f"[{m}]:\n{r}" for m, r, _ in responses]
        )

        system = (
            "You are an expert evaluator. Compare responses on quality, accuracy, completeness, and helpfulness. "
            "Respond ONLY with valid JSON:\n"
            '{"winner":"<model_name>","reasoning":"<brief explanation>"}'
        )

        user = (
            f"Original prompt: {prompt}\n\n"
            f"Responses to evaluate:\n{responses_text}\n\n"
            f"Which model provided the best response? "
            f"Choose from: {models_list}"
        )

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json={
                        "model": self.judge_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": self.temperature,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                # Parse JSON from response
                verdict = self._parse_json(content)
                return verdict
        except Exception as e:
            logger.error(f"Judge call failed: {e}")
            # Fallback: pick first response
            return {
                "winner": responses[0][0] if responses else "unknown",
                "reasoning": f"Judge evaluation failed: {str(e)}. Defaulting to first response.",
            }

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Parse JSON from text, handling markdown fences and partial content."""
        # Try direct parse
        try:
            return json.loads(text)
        except:
            pass

        # Strip markdown fences
        text = text.replace("```json", "").replace("```", "")

        # Try again
        try:
            return json.loads(text)
        except:
            pass

        # Regex extraction: find { ... }
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass

        # Fallback
        return {
            "winner": "unknown",
            "reasoning": "Could not parse judge response",
        }
