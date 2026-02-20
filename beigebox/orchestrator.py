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

        # Enforce max parallel tasks
        if len(plan) > self.max_parallel:
            logger.warning(
                "Plan has %d tasks, capping at %d", len(plan), self.max_parallel
            )
            plan = plan[: self.max_parallel]

        t0 = time.monotonic()
        tasks = [self._run_task(i, task) for i, task in enumerate(plan)]

        try:
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
            choices = data.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""

            return {
                "task": index,
                "model": model,
                "content": content,
                "latency_ms": round(latency, 1),
                "tokens": data.get("usage", {}).get("total_tokens", 0),
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
            loop = asyncio.get_running_loop()
            # Already in async context — create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = loop.run_in_executor(
                    pool, lambda: asyncio.run(self.run(plan))
                )
                # This is tricky in sync context; for LangChain tool use,
                # the Operator agent runs synchronously
                result = asyncio.run(self.run(plan))
        except RuntimeError:
            # No running loop — safe to use asyncio.run
            result = asyncio.run(self.run(plan))

        return json.dumps(result, indent=2)


def get_orchestrator_tool():
    """
    Create a LangChain-compatible tool for the Orchestrator.
    Returns None if orchestrator is disabled in config.
    """
    cfg = get_config()
    orch_cfg = cfg.get("orchestrator", {})

    if not orch_cfg.get("enabled", False):
        return None

    orchestrator = ParallelOrchestrator(
        max_parallel_tasks=orch_cfg.get("max_parallel_tasks", 5),
        task_timeout_seconds=orch_cfg.get("task_timeout_seconds", 120),
        total_timeout_seconds=orch_cfg.get("total_timeout_seconds", 300),
    )

    try:
        from langchain_core.tools import Tool

        return Tool(
            name="parallel_orchestrator",
            description=(
                "Execute multiple LLM tasks in parallel. "
                "Input: JSON array of tasks, each with 'model' and 'prompt' keys. "
                "Example: [{\"model\": \"code\", \"prompt\": \"Analyze this function\"}, "
                "{\"model\": \"large\", \"prompt\": \"Explain the implications\"}]. "
                "Returns collected results from all tasks."
            ),
            func=orchestrator.run_sync,
        )
    except ImportError:
        logger.warning("langchain_core not available — orchestrator tool disabled")
        return None
