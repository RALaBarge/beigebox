"""
Ensemble tool — lets the Operator agent run multi-model voting.

Runs the same prompt against N models in parallel, asks a judge LLM to pick
the best response, and returns a structured summary the operator can reason
over or pass back to the user.

Input format (JSON string or plain comma-separated model list):
  {"prompt": "explain X", "models": ["llama3.2:3b", "mistral:7b"], "judge": "llama3.2:3b"}
  OR just pass a pipe-separated string: "explain X | llama3.2:3b,mistral:7b"

Output: plain-text summary of all responses + winner + reasoning.

Disabled by default. Enable in config.yaml:
  tools:
    ensemble:
      enabled: true
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class EnsembleTool:
    """Operator-callable wrapper around EnsembleVoter."""

    description = (
        "Run a prompt against multiple models in parallel and get a judge verdict on which "
        "response is best. Input: JSON with keys 'prompt', 'models' (list), and optional "
        "'judge' (model name). Example: "
        '{\"prompt\": \"explain recursion\", \"models\": [\"llama3.2:3b\", \"mistral:7b\"]}'
    )

    def __init__(self, judge_model: str | None = None, max_models: int = 6):
        self.judge_model = judge_model
        self.max_models = max_models
        logger.info("EnsembleTool initialized (judge=%s, max_models=%d)", judge_model, max_models)

    def run(self, input_str: str) -> str:
        """
        Parse input, run ensemble vote synchronously, return formatted result.
        """
        prompt, models, judge = self._parse_input(input_str)

        if not prompt:
            return "Error: ensemble tool requires a 'prompt' field."
        if len(models) < 2:
            return "Error: ensemble tool requires at least 2 models. Pass a 'models' list."
        if len(models) > self.max_models:
            models = models[: self.max_models]
            logger.warning("EnsembleTool: capped models at %d", self.max_models)

        try:
            return asyncio.run(self._run_async(prompt, models, judge))
        except RuntimeError:
            # Already inside an event loop (shouldn't happen in sync operator, but safe)
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._run_async(prompt, models, judge))
            finally:
                loop.close()

    async def _run_async(self, prompt: str, models: list[str], judge: str | None) -> str:
        from beigebox.agents.ensemble_voter import EnsembleVoter

        voter = EnsembleVoter(
            models=models,
            judge_model=judge or self.judge_model,
        )

        results: dict[str, str] = {}
        latencies: dict[str, int] = {}
        winner = ""
        reasoning = ""
        best_response = ""

        async for event in voter.vote(prompt):
            t = event.get("type", "")
            if t == "result":
                results[event["model"]] = event.get("response", "")
                latencies[event["model"]] = event.get("latency_ms", 0)
            elif t == "finish":
                winner = event.get("winner", "")
                reasoning = event.get("verdict", "")
                best_response = event.get("best_response", "")
            elif t == "error":
                return f"Ensemble error: {event.get('message', 'unknown error')}"

        if not results:
            return "Ensemble returned no results. Check that the models are available."

        lines = [f"Ensemble run across {len(results)} model(s):"]
        lines.append("")

        for model, response in results.items():
            tag = " ← WINNER" if model == winner else ""
            ms = latencies.get(model, 0)
            lines.append(f"[{model}]{tag} ({ms}ms):")
            # Truncate very long responses so the operator context stays manageable
            preview = response[:600] + ("…" if len(response) > 600 else "")
            lines.append(preview)
            lines.append("")

        if winner:
            lines.append(f"Winner: {winner}")
        if reasoning:
            lines.append(f"Judge reasoning: {reasoning}")

        return "\n".join(lines)

    @staticmethod
    def _parse_input(input_str: str) -> tuple[str, list[str], str | None]:
        """
        Accept two input formats:
          1. JSON: {"prompt": "...", "models": [...], "judge": "..."}
          2. Pipe-separated: "prompt text | model1,model2"
        """
        input_str = input_str.strip()

        # Try JSON first
        try:
            data = json.loads(input_str)
            prompt = data.get("prompt", "").strip()
            models = data.get("models", [])
            judge = data.get("judge") or data.get("judge_model")
            return prompt, models, judge
        except (json.JSONDecodeError, AttributeError):
            pass

        # Pipe format: "prompt text | model1,model2"
        if "|" in input_str:
            parts = input_str.split("|", 1)
            prompt = parts[0].strip()
            model_str = parts[1].strip()
            models = [m.strip() for m in model_str.split(",") if m.strip()]
            return prompt, models, None

        # Just a prompt with no model spec — can't run
        return input_str, [], None
