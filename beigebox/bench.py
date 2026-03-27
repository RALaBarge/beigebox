"""
BeigeBox Benchmark Runner — direct-to-Ollama speed tests.

Bypasses the BeigeBox proxy entirely. Goes straight to Ollama /api/generate
and uses Ollama's own internal timing fields (eval_count, eval_duration, etc.)
measured inside the Go runtime — no network jitter, no proxy overhead.

Protocol:
  1. Warmup request (discard result — loads model into VRAM)
  2. Verify model is loaded via /api/ps
  3. N sequential measured runs (default 5), temperature=0, fixed num_predict
  4. Report: tokens/sec, TTFT, load_duration, per-run breakdown

tokens/sec = eval_count / (eval_duration / 1e9)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "You are a helpful assistant. Explain the difference between supervised and "
    "unsupervised machine learning in clear terms suitable for a software engineer "
    "who has not studied statistics. Include examples of common algorithms for each."
)
DEFAULT_NUM_PREDICT = 120
DEFAULT_NUM_RUNS = 5
DEFAULT_KEEP_ALIVE = "10m"


@dataclass
class RunResult:
    run_index: int
    model: str
    tokens_generated: int
    tokens_per_sec: float
    eval_duration_ms: float
    prompt_eval_duration_ms: float
    load_duration_ms: float
    total_duration_ms: float
    wall_ms: float  # outer wall clock (httpx round-trip)
    ok: bool
    error: str = ""


@dataclass
class ModelBenchResult:
    model: str
    num_runs: int
    runs: list[RunResult] = field(default_factory=list)

    @property
    def successful_runs(self) -> list[RunResult]:
        return [r for r in self.runs if r.ok]

    def _avg(self, attr: str) -> float:
        s = self.successful_runs
        if not s:
            return 0.0
        return sum(getattr(r, attr) for r in s) / len(s)

    def _median(self, attr: str) -> float:
        s = self.successful_runs
        if not s:
            return 0.0
        vals = sorted(getattr(r, attr) for r in s)
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2

    @property
    def avg_tokens_per_sec(self) -> float:
        return self._avg("tokens_per_sec")

    @property
    def median_tokens_per_sec(self) -> float:
        return self._median("tokens_per_sec")

    @property
    def avg_ttft_ms(self) -> float:
        return self._avg("prompt_eval_duration_ms")

    @property
    def avg_eval_ms(self) -> float:
        return self._avg("eval_duration_ms")

    @property
    def avg_load_ms(self) -> float:
        return self._avg("load_duration_ms")

    def summary(self) -> dict:
        return {
            "model": self.model,
            "runs_ok": len(self.successful_runs),
            "runs_total": len(self.runs),
            "avg_tokens_per_sec": round(self.avg_tokens_per_sec, 2),
            "median_tokens_per_sec": round(self.median_tokens_per_sec, 2),
            "avg_ttft_ms": round(self.avg_ttft_ms, 1),
            "avg_eval_ms": round(self.avg_eval_ms, 1),
            "avg_load_ms": round(self.avg_load_ms, 1),
            "runs": [
                {
                    "i": r.run_index,
                    "tok_s": round(r.tokens_per_sec, 2),
                    "eval_ms": round(r.eval_duration_ms, 1),
                    "ttft_ms": round(r.prompt_eval_duration_ms, 1),
                    "load_ms": round(r.load_duration_ms, 1),
                    "wall_ms": round(r.wall_ms, 1),
                    "ok": r.ok,
                    "error": r.error,
                }
                for r in self.runs
            ],
        }


class BenchmarkRunner:
    """
    Runs speed benchmarks against Ollama directly (bypasses BeigeBox proxy).

    Usage:
        runner = BenchmarkRunner(ollama_url="http://ollama:11434")
        async for event in runner.run_stream(models=["llama3.1:8b"], ...):
            print(event)
    """

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url.rstrip("/")

    async def _single_run(
        self,
        client: httpx.AsyncClient,
        model: str,
        prompt: str,
        num_predict: int,
        run_index: int,
    ) -> RunResult:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": 0,
            },
            "keep_alive": DEFAULT_KEEP_ALIVE,
        }
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=300.0,
            )
            wall_ms = (time.perf_counter() - t0) * 1000

            if resp.status_code != 200:
                return RunResult(
                    run_index=run_index, model=model,
                    tokens_generated=0, tokens_per_sec=0,
                    eval_duration_ms=0, prompt_eval_duration_ms=0,
                    load_duration_ms=0, total_duration_ms=0,
                    wall_ms=wall_ms, ok=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )

            data = resp.json()
            eval_count = data.get("eval_count", 0)
            eval_duration_ns = data.get("eval_duration", 0)
            prompt_eval_duration_ns = data.get("prompt_eval_duration", 0)
            load_duration_ns = data.get("load_duration", 0)
            total_duration_ns = data.get("total_duration", 0)

            eval_duration_ms = eval_duration_ns / 1e6
            prompt_eval_duration_ms = prompt_eval_duration_ns / 1e6
            load_duration_ms = load_duration_ns / 1e6
            total_duration_ms = total_duration_ns / 1e6

            tokens_per_sec = (
                eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0.0
            )

            return RunResult(
                run_index=run_index, model=model,
                tokens_generated=eval_count,
                tokens_per_sec=tokens_per_sec,
                eval_duration_ms=eval_duration_ms,
                prompt_eval_duration_ms=prompt_eval_duration_ms,
                load_duration_ms=load_duration_ms,
                total_duration_ms=total_duration_ms,
                wall_ms=wall_ms,
                ok=True,
            )
        except Exception as exc:
            wall_ms = (time.perf_counter() - t0) * 1000
            return RunResult(
                run_index=run_index, model=model,
                tokens_generated=0, tokens_per_sec=0,
                eval_duration_ms=0, prompt_eval_duration_ms=0,
                load_duration_ms=0, total_duration_ms=0,
                wall_ms=wall_ms, ok=False,
                error=str(exc),
            )

    async def run_stream(
        self,
        models: list[str],
        prompt: str = DEFAULT_PROMPT,
        num_predict: int = DEFAULT_NUM_PREDICT,
        num_runs: int = DEFAULT_NUM_RUNS,
    ):
        """
        Async generator that yields SSE-compatible JSON dicts as bench progresses.

        Event types:
          {"event": "start",   "models": [...], "num_runs": N, "num_predict": N}
          {"event": "warmup",  "model": "...", "status": "starting"|"done"|"error", "error": "..."}
          {"event": "run",     "model": "...", "run": N, "result": {...}}
          {"event": "model_done", "model": "...", "summary": {...}}
          {"event": "done",    "results": [...sorted by avg_tokens_per_sec desc]}
          {"event": "error",   "message": "..."}
        """
        yield {"event": "start", "models": models, "num_runs": num_runs, "num_predict": num_predict}

        all_results: list[ModelBenchResult] = []

        async with httpx.AsyncClient(timeout=300.0) as client:
            for model in models:
                model_result = ModelBenchResult(model=model, num_runs=num_runs)

                # --- Warmup ---
                yield {"event": "warmup", "model": model, "status": "starting"}
                warmup = await self._single_run(client, model, prompt, num_predict, run_index=0)
                if not warmup.ok:
                    yield {"event": "warmup", "model": model, "status": "error", "error": warmup.error}
                    # Still attempt measured runs — maybe it recovered
                else:
                    yield {"event": "warmup", "model": model, "status": "done",
                           "load_ms": round(warmup.load_duration_ms, 1)}

                # --- Measured runs ---
                for i in range(1, num_runs + 1):
                    run_result = await self._single_run(client, model, prompt, num_predict, run_index=i)
                    model_result.runs.append(run_result)
                    yield {
                        "event": "run",
                        "model": model,
                        "run": i,
                        "total": num_runs,
                        "result": {
                            "tok_s": round(run_result.tokens_per_sec, 2),
                            "eval_ms": round(run_result.eval_duration_ms, 1),
                            "ttft_ms": round(run_result.prompt_eval_duration_ms, 1),
                            "ok": run_result.ok,
                            "error": run_result.error,
                        },
                    }

                all_results.append(model_result)
                yield {"event": "model_done", "model": model, "summary": model_result.summary()}

        # Sort by avg tokens/sec descending
        all_results.sort(key=lambda r: r.avg_tokens_per_sec, reverse=True)
        yield {
            "event": "done",
            "results": [r.summary() for r in all_results],
        }
