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
        backend_router=None,
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
        self.backend_router = backend_router

    async def vote(self, prompt: str) -> AsyncGenerator[dict, None]:
        """
        Send prompt to all models in parallel, then judge best response.

        Yields:
          {type:"dispatch", model_count:N}
          {type:"token", model:"...", token:"..."}  -- streamed incrementally
          {type:"result", model:"...", response:"...", latency_ms:123}
          ...repeat for each model...
          {type:"judge_token", token:"..."}         -- judge response streaming
          {type:"evaluate", winner:"...", reasoning:"...", all_responses:[...]}
          {type:"finish", best_response:"...", winner:"...", verdict:"..."}
        """
        yield _ev("start", prompt=prompt, models=self.models, judge=self.judge_model)

        # ── 1. Dispatch — always parallel streaming ────────────────────────────
        yield _ev("dispatch", model_count=len(self.models))

        responses: list[tuple[str, str, int]] = []
        # Shared queue lets all N model coroutines fan out their token/result
        # events into a single ordered stream. The main loop drains until all
        # "result" events are accounted for — one per model. Tasks and the
        # drain loop run concurrently within the same event loop.
        queue: asyncio.Queue = asyncio.Queue()
        pending = len(self.models)
        tasks = [
            asyncio.create_task(
                self._stream_model_to_queue(model, prompt, queue)
            )
            for model in self.models
        ]
        while pending > 0:
            ev = await queue.get()
            yield ev
            if ev.get("type") == "result":
                responses.append((ev["model"], ev["response"], ev["latency_ms"]))
                pending -= 1
        await asyncio.gather(*tasks, return_exceptions=True)

        if not responses:
            yield _ev("error", message="No responses from any model")
            return

        # ── 2. Judge — stream tokens so the UI shows typing ───────────────────
        judge_tokens: list[str] = []
        try:
            async for ev in self._stream_judge(prompt, responses):
                if ev.get("type") == "judge_token":
                    judge_tokens.append(ev["token"])
                yield ev
        except Exception as e:
            yield _ev("error", message=f"Judge evaluation failed: {e}")
            return

        raw_verdict = "".join(judge_tokens)
        verdict = self._parse_json(raw_verdict)

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

    async def _stream_model_to_queue(
        self, model: str, prompt: str, queue: asyncio.Queue
    ) -> None:
        """Stream a single model's response into the shared queue.

        Uses backend_router if available, otherwise streams directly to
        backend_url so ensemble always works regardless of backends_enabled.
        keep_alive: -1 keeps the model resident in VRAM across rounds.
        """
        start = time.time()
        tokens: list[str] = []
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "stream": True,
            "keep_alive": -1,  # keep model in VRAM between ensemble rounds
        }
        try:
            async for line in self._iter_sse(body):
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if delta:
                        tokens.append(delta)
                        await queue.put(_ev("token", model=model, token=delta))
                except (json.JSONDecodeError, IndexError):
                    pass
            full = "".join(tokens)
            latency = int((time.time() - start) * 1000)
            await queue.put(_ev("result", model=model, response=full, latency_ms=latency))
        except Exception as e:
            logger.error("Stream failed for %s: %s", model, e)
            latency = int((time.time() - start) * 1000)
            await queue.put(_ev("result", model=model, response=f"Error: {e}", latency_ms=latency))

    def _apply_model_options(self, body: dict) -> dict:
        """Inject per-model options (num_gpu, num_ctx, etc.) from config.yaml.

        The ensemble bypasses the proxy pipeline so _inject_model_options()
        never runs here. We replicate that logic so num_gpu: 0 and similar
        settings take effect even when models are first loaded by ensemble.
        """
        model = body.get("model", "")
        model_opts = self.cfg.get("models", {}).get(model, {}).get("options", {})
        if model_opts:
            body = {**body, "options": {**body.get("options", {}), **model_opts}}
        return body

    async def _iter_sse(self, body: dict):
        """Yield SSE lines from backend_router or directly from backend_url."""
        body = self._apply_model_options(body)
        if self.backend_router:
            async for line in self.backend_router.forward_stream(body):
                yield line
        else:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self.backend_url}/v1/chat/completions",
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield line

    # ── Judge evaluation ───────────────────────────────────────────────────────

    async def _stream_judge(
        self, prompt: str, responses: list[tuple[str, str, int]]
    ) -> AsyncGenerator[dict, None]:
        """Stream judge tokens as judge_token events; yield no evaluate/finish."""
        models_list = ", ".join([m for m, _, _ in responses])
        responses_text = "\n\n".join([f"[{m}]:\n{r}" for m, r, _ in responses])

        system = (
            "You are an expert evaluator. Compare responses on quality, accuracy, completeness, and helpfulness. "
            "Respond ONLY with valid JSON:\n"
            '{"winner":"<model_name>","reasoning":"<brief explanation>"}'
        )
        user = (
            f"Original prompt: {prompt}\n\n"
            f"Responses to evaluate:\n{responses_text}\n\n"
            f"Which model provided the best response? Choose from: {models_list}"
        )

        judge_body = {
            "model": self.judge_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "stream": True,
            "keep_alive": -1,
        }

        try:
            async for line in self._iter_sse(judge_body):
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if delta:
                        yield _ev("judge_token", token=delta)
                except (json.JSONDecodeError, IndexError):
                    pass
        except Exception as e:
            logger.error("Judge stream failed: %s", e)
            # Fallback: emit a synthetic verdict token so the caller's
            # _parse_json always has something to work with and the "finish"
            # event still fires with a usable (first-model) winner.
            fallback = json.dumps({
                "winner": responses[0][0] if responses else "unknown",
                "reasoning": f"Judge failed: {e}",
            })
            yield _ev("judge_token", token=fallback)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Parse JSON from text, handling markdown fences and partial content.

        Three-tier fallback: direct parse → strip markdown fences and retry
        → regex scan for first {...} block. The judge is instructed to emit raw
        JSON only, but LLMs sometimes add fences or prose; this tolerates that.
        """
        from beigebox.utils.json_parse import extract_json_object

        result = extract_json_object(text)
        if result:
            return result

        # Fallback if no JSON found
        return {
            "winner": "unknown",
            "reasoning": "Could not parse judge response",
        }
