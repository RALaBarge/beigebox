"""
Orchestrator — parallel LLM task spawning.

Allows the Operator agent to run multiple LLM tasks concurrently
and collect results. Useful for divide-and-conquer approaches:
  - Analyze multiple documents in parallel
  - Get multiple model perspectives
  - Break large problems into sub-tasks

Registered as a LangChain tool on the Operator agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)


class ParallelOrchestrator:
    """
    Execute parallel LLM tasks with timeout and error handling.

    Each task is a dict: {"model": "...", "prompt": "..."}
    Results are collected and concatenated.
    """

    def __init__(
        self,
        backend_url: str | None = None,
        max_parallel_tasks: int = 5,
        task_timeout_seconds: int = 120,
        total_timeout_seconds: int = 300,
    ):
        cfg = get_config()
        self.backend_url = (backend_url or cfg["backend"]["url"]).rstrip("/")
        self.max_parallel = max_parallel_tasks
        self.task_timeout = task_timeout_seconds
        self.total_timeout = total_timeout_seconds

    async def run(self, plan: list[dict]) -> dict:
        """
        Execute a plan of parallel tasks.

        Args:
            plan: List of task dicts, each with "model" and "prompt" keys.
                  Optional: "system" for a system prompt.

        Returns:
            {
                "success": True/False,
                "results": [...],
                "errors": [...],
                "total_ms": 1234.5,
                "tasks_completed": 3,
                "tasks_failed": 1,
            }
        """
        if not plan:
            return {"success": False, "error": "Empty plan", "results": []}

        # Silently cap rather than reject — the operator may over-plan and
        # dropping tail tasks is safer than refusing to run anything.
        if len(plan) > self.max_parallel:
            logger.warning(
                "Plan has %d tasks, capping at %d", len(plan), self.max_parallel
            )
            plan = plan[: self.max_parallel]

        t0 = time.monotonic()
        tasks = [self._run_task(i, task) for i, task in enumerate(plan)]

        try:
            # return_exceptions=True so a single failing task doesn't cancel
            # the whole gather — results and exceptions are separated below.
            # wait_for wraps the gather to enforce a hard wall-clock budget.
            raw_results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.total_timeout,
            )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - t0) * 1000
            return {
                "success": False,
                "error": f"Total timeout ({self.total_timeout}s) exceeded",
                "results": [],
                "total_ms": round(elapsed, 1),
            }

        elapsed = (time.monotonic() - t0) * 1000
        results = []
        errors = []

        for i, result in enumerate(raw_results):
            if isinstance(result, Exception):
                errors.append({"task": i, "error": str(result)})
            elif isinstance(result, dict) and result.get("error"):
                errors.append({"task": i, "error": result["error"]})
            else:
                results.append(result)

        return {
            "success": len(results) > 0,
            "results": results,
            "errors": errors,
            "total_ms": round(elapsed, 1),
            "tasks_completed": len(results),
            "tasks_failed": len(errors),
        }

    async def _run_task(self, index: int, task: dict) -> dict:
        """Run a single task against the backend."""
        model = task.get("model", "")
        prompt = task.get("prompt", "")
        system = task.get("system", "")

        if not prompt:
            return {"task": index, "error": "Empty prompt"}

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.task_timeout) as client:
                resp = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()

            latency = (time.monotonic() - t0) * 1000
            from beigebox.response_normalizer import normalize_response
            normalized = normalize_response(data)

            return {
                "task": index,
                "model": model,
                "content": normalized.content,
                "latency_ms": round(latency, 1),
                "tokens": normalized.usage.total_tokens,
            }
        except asyncio.TimeoutError:
            return {"task": index, "error": f"Task timeout ({self.task_timeout}s)"}
        except Exception as e:
            return {"task": index, "error": str(e)}

    def run_sync(self, plan_json: str) -> str:
        """
        Sync wrapper for use as a LangChain tool.
        Accepts JSON string, returns JSON string.
        """
        try:
            plan = json.loads(plan_json)
            if not isinstance(plan, list):
                return json.dumps({"error": "Plan must be a JSON array of tasks"})
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        # Run in existing or new event loop
        try:
            # If we're already inside an event loop (e.g. FastAPI), asyncio.run()
            # would raise "cannot run nested event loop". The run_in_executor path
            # is a workaround, but it immediately falls through to asyncio.run()
            # anyway — this codepath is only hit when Operator is called sync.
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = loop.run_in_executor(
                    pool, lambda: asyncio.run(self.run(plan))
                )
                result = asyncio.run(self.run(plan))
        except RuntimeError:
            # No running event loop — safe to use asyncio.run directly.
            result = asyncio.run(self.run(plan))

        return json.dumps(result, indent=2)
