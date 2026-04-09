"""
RalphOrchestrator — autonomous spec-driven development loop

Implements the "Ralph" pattern:
  while not tests_pass and iteration < max_iterations:
      spec = reload_from_disk()          # picks up edits mid-run ("signs")
      response = call_operator(spec, last_test_output)
      result = run_test_command()
      if result.exit_code == 0: done

Key design decisions:

  FRESH CONTEXT PER ITERATION
    Each operator call is stateless — only the spec + last test output go in.
    No accumulating conversation history. This keeps the main model context
    small regardless of how many iterations run.

  SUBAGENTS HOLD HEAVY CONTEXT
    The operator has full tool access (read files, run commands, call subagents).
    Heavy work (read 40 files, analyse a large codebase) happens inside the
    operator's own tool calls, not in the Ralph loop's context. The loop only
    sees the operator's final response, not the intermediate tool call outputs.

  SPEC IS RELOADED EACH ITERATION
    PROMPT.md is re-read from disk before every call. Edit the file mid-run
    to steer the agent ("signs") — the next iteration picks up the change
    without needing to restart.

  TEST AS BACK PRESSURE
    The test command exit code is the only acceptance criterion. Ralph doesn't
    decide when it's done — the tests do.

Events emitted (SSE-compatible dicts):
  {type: "start",           run_id, spec_path, test_cmd, max_iterations, model}
  {type: "iteration_start", iteration, spec_preview}
  {type: "agent_chunk",     iteration, chunk}          # streaming tokens
  {type: "agent_done",      iteration, response, latency_ms}
  {type: "test_run",        iteration, cmd}
  {type: "test_result",     iteration, exit_code, passed, stdout, stderr, latency_ms}
  {type: "finish",          iterations, passed, answer}
  {type: "error",           message}
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)

MAX_TEST_OUTPUT_CHARS = 4000   # truncate test output sent back to the agent
MAX_SPEC_CHARS = 20000         # safety limit on spec size


def _ev(type_: str, **kw) -> dict:
    return {"type": type_, "ts": round(time.monotonic() * 1000), **kw}


class RalphOrchestrator:
    """
    Autonomous loop: spec + tests → done.

    The operator is the agent. Each iteration it receives:
      - The full spec (from disk, so edits take effect immediately)
      - A summary of the last test run (exit code + truncated output)
      - The iteration number and remaining budget

    The operator responds with whatever it wants to do. It has full tool
    access so it can read files, write files, run commands, etc.

    Ralph just checks if the tests pass after each iteration.
    """

    def __init__(
        self,
        spec_path: str | None = None,
        spec_inline: str | None = None,
        test_cmd: str = "",
        working_dir: str | None = None,
        max_iterations: int = 20,
        model: str | None = None,
        backend_router=None,
        injection_queue: asyncio.Queue | None = None,
    ):
        cfg = get_config()
        self.cfg = cfg
        self.backend_url = cfg["backend"]["url"].rstrip("/")
        self.backend_router = backend_router
        # injection_queue receives mid-run steering messages from the API
        # endpoint (POST /api/v1/harness/ralph/inject). The run loop drains it
        # at the start of each iteration and appends items to the prompt.
        self.injection_queue = injection_queue

        self.spec_path = Path(spec_path).expanduser() if spec_path else None
        self.spec_inline = spec_inline
        self.test_cmd = test_cmd.strip()
        self.working_dir = working_dir or os.getcwd()
        self.max_iterations = max_iterations

        self.model = (
            model
            or cfg.get("models", {}).get("profiles", {}).get("agentic")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self.run_id = str(uuid4())[:8]

    def _load_spec(self) -> str:
        """Load spec from disk (each iteration) or return inline text."""
        if self.spec_path and self.spec_path.exists():
            text = self.spec_path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_SPEC_CHARS:
                text = text[:MAX_SPEC_CHARS] + f"\n\n[...truncated at {MAX_SPEC_CHARS} chars]"
            return text
        if self.spec_inline:
            return self.spec_inline
        return "(no spec provided — act on general goal)"

    def _run_tests(self) -> dict:
        """Run the test command synchronously. Returns result dict."""
        if not self.test_cmd:
            return {
                "exit_code": 0,
                "passed": True,
                "stdout": "(no test command configured)",
                "stderr": "",
                "latency_ms": 0,
            }
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                self.test_cmd,
                shell=True,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            latency = round((time.monotonic() - t0) * 1000)
            # Take the tail (most recent output) when truncating — test failures
            # almost always manifest at the end of stdout/stderr, not the start.
            stdout = result.stdout[-MAX_TEST_OUTPUT_CHARS:] if result.stdout else ""
            stderr = result.stderr[-MAX_TEST_OUTPUT_CHARS:] if result.stderr else ""
            return {
                "exit_code": result.returncode,
                "passed": result.returncode == 0,
                "stdout": stdout,
                "stderr": stderr,
                "latency_ms": latency,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "passed": False,
                "stdout": "",
                "stderr": "Test command timed out after 120s",
                "latency_ms": 120000,
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "passed": False,
                "stdout": "",
                "stderr": f"Failed to run test command: {e}",
                "latency_ms": 0,
            }

    def _build_prompt(
        self,
        spec: str,
        iteration: int,
        last_test: dict | None,
        injected: list[str],
    ) -> str:
        """
        Build the per-iteration operator prompt.

        Kept deliberately compact: spec + test status + iteration budget.
        Heavy context (file reads etc.) happens inside the operator's tool calls.
        """
        parts = [
            f"# Autonomous Development Task — Iteration {iteration} of {self.max_iterations}",
            "",
            "## Specification",
            spec,
        ]

        if last_test:
            parts += ["", "## Last Test Run"]
            if last_test["passed"]:
                parts.append("✓ All tests passed.")
            else:
                parts.append(f"✗ Tests failed (exit code {last_test['exit_code']})")
                if last_test["stdout"]:
                    parts += ["", "**stdout:**", "```", last_test["stdout"][-2000:], "```"]
                if last_test["stderr"]:
                    parts += ["", "**stderr:**", "```", last_test["stderr"][-2000:], "```"]
        else:
            parts += ["", "## Status", "First iteration — no test results yet."]

        if injected:
            parts += ["", "## Steering Instructions (added mid-run)"]
            for msg in injected:
                parts.append(f"- {msg}")

        parts += [
            "",
            "## Instructions",
            f"Working directory: `{self.working_dir}`",
            f"Test command: `{self.test_cmd or '(none)'}`",
            "",
            "Make changes to bring the implementation in line with the specification.",
            "After you respond, the test command will be run automatically.",
            "Focus on what the tests are telling you. Do not over-explain.",
        ]

        return "\n".join(parts)

    async def _call_operator(
        self,
        prompt: str,
        iteration: int,
    ) -> AsyncGenerator[dict, None]:
        """Call the operator and yield streaming events."""
        messages = [{"role": "user", "content": prompt}]

        system = (
            "You are an autonomous software development agent running in a loop. "
            "You will be given a specification and test results. "
            "Your job is to make changes to the codebase so the tests pass. "
            "Use your tools to read files, write files, and run commands. "
            "For large files or complex analysis, delegate to subagents. "
            "Be decisive and focused — do not ask clarifying questions."
        )

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "stream": True,
            "temperature": 0.2,
        }

        t0 = time.monotonic()
        full_response = ""

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{self.backend_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            import json as _json
                            chunk_data = _json.loads(raw)
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response += content
                                yield _ev("agent_chunk", iteration=iteration, chunk=content)
                        except Exception:
                            continue

        except Exception as e:
            yield _ev("error", message=f"Operator call failed (iteration {iteration}): {e}")
            return

        latency = round((time.monotonic() - t0) * 1000)
        yield _ev("agent_done", iteration=iteration, response=full_response, latency_ms=latency)

    async def run(self) -> AsyncGenerator[dict, None]:
        """Main loop. Yields SSE-compatible event dicts."""
        spec_path_str = str(self.spec_path) if self.spec_path else "(inline)"

        yield _ev(
            "start",
            run_id=self.run_id,
            spec_path=spec_path_str,
            test_cmd=self.test_cmd,
            max_iterations=self.max_iterations,
            model=self.model,
            working_dir=self.working_dir,
        )

        last_test: dict | None = None
        injected: list[str] = []
        final_response = ""

        for iteration in range(1, self.max_iterations + 1):
            # Check for injected steering messages
            if self.injection_queue:
                while not self.injection_queue.empty():
                    try:
                        msg = self.injection_queue.get_nowait()
                        injected.append(msg)
                        yield _ev("injected", iteration=iteration, message=msg)
                    except asyncio.QueueEmpty:
                        break

            # Reload spec from disk (picks up "signs" — edits made mid-run)
            spec = self._load_spec()

            yield _ev(
                "iteration_start",
                iteration=iteration,
                spec_preview=spec[:200],
                remaining=self.max_iterations - iteration + 1,
            )

            # Call operator
            async for ev in self._call_operator(
                self._build_prompt(spec, iteration, last_test, injected),
                iteration,
            ):
                if ev["type"] == "error":
                    yield ev
                    return
                if ev["type"] == "agent_done":
                    final_response = ev.get("response", "")
                yield ev

            # Clear injected after they're included in a prompt
            injected = []

            # Run tests
            yield _ev("test_run", iteration=iteration, cmd=self.test_cmd)

            # _run_tests is synchronous (subprocess.run). run_in_executor
            # offloads it to the thread pool so the event loop stays responsive
            # while the test command is running.
            loop = asyncio.get_running_loop()
            test_result = await loop.run_in_executor(None, self._run_tests)
            last_test = test_result

            yield _ev(
                "test_result",
                iteration=iteration,
                exit_code=test_result["exit_code"],
                passed=test_result["passed"],
                stdout=test_result["stdout"],
                stderr=test_result["stderr"],
                latency_ms=test_result["latency_ms"],
            )

            if test_result["passed"]:
                yield _ev(
                    "finish",
                    iterations=iteration,
                    passed=True,
                    capped=False,
                    answer=final_response,
                )
                return

            # Brief pause before next iteration
            await asyncio.sleep(0.5)

        # Hit iteration cap without passing
        yield _ev(
            "finish",
            iterations=self.max_iterations,
            passed=False,
            capped=True,
            answer=final_response,
        )
