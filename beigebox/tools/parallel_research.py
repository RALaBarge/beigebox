"""
ParallelResearchTool — launches multiple ResearchAgentTool instances concurrently.

Coordinates parallel execution, aggregates results, handles partial failures.

Input format (JSON string):
    {
        "tasks": [
            {"topic": "RAG poisoning", "research_questions": ["vectors?"], "depth": "medium"},
            {"topic": "MCP injection", "research_questions": ["patterns?"], "depth": "quick"}
        ],
        "max_workers": 4
    }
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from beigebox.tools.research_agent import ResearchAgentTool

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()


class ParallelResearchTool:
    description = (
        'Run multiple research agents in parallel. '
        'input MUST be a JSON object with key "tasks" (list of research task objects). '
        'Each task: {"topic": str, "research_questions": [str], "depth": "quick"|"medium"|"deep"}. '
        'Optional: "max_workers" (int, default 4). '
        'Returns aggregated results from all research agents. '
        'Example: {"tasks":[{"topic":"AI safety","research_questions":["Key risks?"]}],"max_workers":2}'
    )

    def __init__(self, workspace_out: Path | None = None):
        self._root = (workspace_out or _WORKSPACE_OUT).resolve()
        self._research_tool = ResearchAgentTool(workspace_out=self._root)

    async def _run_single(self, task: dict) -> tuple[str, dict | str]:
        """Run a single research task, returning (topic, result_or_error)."""
        topic = task.get("topic", "unknown")
        questions = task.get("research_questions", [])
        depth = task.get("depth", "medium")
        max_turns = task.get("max_turns", 10)

        try:
            result = await self._research_tool.execute(
                topic=topic,
                research_questions=questions,
                max_turns=max_turns,
                depth=depth,
            )
            return (topic, result)
        except Exception as e:
            logger.error("Parallel research task '%s' failed: %s", topic, e)
            return (topic, str(e))

    async def _run_parallel(self, tasks: list[dict], max_workers: int) -> dict:
        """Execute tasks with concurrency limit, aggregate results."""
        t0 = time.monotonic()
        semaphore = asyncio.Semaphore(max_workers)

        async def bounded(task):
            async with semaphore:
                return await self._run_single(task)

        raw_results = await asyncio.gather(
            *(bounded(t) for t in tasks),
            return_exceptions=True,
        )

        results = {}
        errors = {}
        for item in raw_results:
            if isinstance(item, Exception):
                errors["unknown"] = str(item)
                continue
            topic, result = item
            if isinstance(result, str):
                errors[topic] = result
            else:
                results[topic] = result

        elapsed = round(time.monotonic() - t0, 2)
        status = "complete" if not errors else ("partial_failure" if results else "failed")

        output = {
            "results": results,
            "execution_time": elapsed,
            "status": status,
            "errors": errors,
            "task_count": len(tasks),
            "completed": len(results),
            "failed": len(errors),
        }

        # Save aggregated results
        self._save_aggregate(output)
        return output

    def _save_aggregate(self, output: dict):
        """Save aggregated results to workspace/out/parallel_research_results.md."""
        self._root.mkdir(parents=True, exist_ok=True)
        filepath = self._root / "parallel_research_results.md"

        lines = [
            "# Parallel Research Results",
            f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
            f"*Status: {output['status']} | "
            f"Completed: {output['completed']}/{output['task_count']} | "
            f"Time: {output['execution_time']}s*",
            "",
        ]

        for topic, findings in output["results"].items():
            lines.append(f"## {topic}")
            if isinstance(findings, dict):
                lines.append(f"*Confidence: {findings.get('confidence', 0):.0%}*")
                lines.append("")
                lines.append(findings.get("findings", "No findings."))
                sources = findings.get("sources", [])
                if sources:
                    lines.append("")
                    lines.append("**Sources:**")
                    for s in sources:
                        lines.append(f"- {s}")
            else:
                lines.append(str(findings))
            lines.append("")

        if output["errors"]:
            lines.append("## Errors")
            for topic, err in output["errors"].items():
                lines.append(f"- **{topic}**: {err}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")

    async def execute(self, tasks: list[dict], max_workers: int = 4) -> dict:
        """Async entry point for direct Python callers."""
        return await self._run_parallel(tasks, max_workers)

    def run(self, input_text: str) -> str:
        """Synchronous entry point for the Operator tool registry."""
        try:
            params = json.loads(input_text)
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, TypeError, ValueError):
            return (
                'Error: input must be a JSON object. '
                'Example: {"tasks":[{"topic":"AI safety","research_questions":["Key risks?"]}]}'
            )

        tasks = params.get("tasks", [])
        if not tasks or not isinstance(tasks, list):
            return 'Error: "tasks" must be a non-empty list of research task objects.'

        # Validate each task
        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                return f'Error: task at index {i} must be a dict.'
            if not task.get("topic"):
                return f'Error: task at index {i} missing "topic".'
            if not task.get("research_questions"):
                return f'Error: task at index {i} missing "research_questions".'

        max_workers = int(params.get("max_workers", 4))
        max_workers = max(1, min(max_workers, 8))  # Clamp to [1, 8]

        try:
            # Run async in the current or new event loop
            try:
                loop = asyncio.get_running_loop()
                # Already in an async context — schedule as a task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = loop.run_in_executor(
                        pool,
                        lambda: asyncio.run(self._run_parallel(tasks, max_workers))
                    )
                    # This won't work from sync context if loop is running.
                    # Fall back to asyncio.run in a thread.
                    raise RuntimeError("use thread fallback")
            except RuntimeError:
                result = asyncio.run(self._run_parallel(tasks, max_workers))
        except Exception as e:
            logger.error("ParallelResearchTool failed: %s", e)
            return json.dumps({
                "results": {},
                "execution_time": 0,
                "status": "failed",
                "errors": {"_global": str(e)},
                "task_count": len(tasks),
                "completed": 0,
                "failed": len(tasks),
            })

        return json.dumps(result)
