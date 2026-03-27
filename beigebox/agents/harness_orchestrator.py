"""
HarnessOrchestrator — goal-directed multi-agent coordinator with resilience.

Given a high-level goal, this agent:
  1. Plans: breaks the goal into subtasks, assigns each to a model or the operator
  2. Dispatches: runs all subtasks in parallel with retry logic and stagger
  3. Evaluates: decides if the results are sufficient or if more work is needed
  4. Synthesizes: produces a final answer when satisfied (or hits the iteration cap)

Features:
  - Retry with exponential backoff for transient errors
  - Error classification (timeout, connection, not_found, rate_limit, internal_error)
  - Adaptive stagger (higher for operator tasks to prevent ChromaDB lock contention)
  - Run persistence to SQLite with replay capability
  - Designed to stream intermediate state back to the caller for UI progress display

Usage (async generator — yields dicts):

    orch = HarnessOrchestrator(available_targets=["operator", "llama3.2:3b"])
    async for event in orch.run("Write and critique a haiku about latency"):
        print(event)   # {type: "plan"|"dispatch"|"result"|"evaluate"|"finish"|"error", ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator
from uuid import uuid4

import httpx

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_ROUNDS = 8          # Hard cap on plan→dispatch→evaluate cycles
MAX_TASKS_PER_ROUND = 6 # Max parallel subtasks per round

# Error classification for retry logic
RETRYABLE_ERRORS = {"timeout", "connection", "not_found", "internal_error"}
NON_RETRYABLE_ERRORS = {"rate_limit", "unknown"}


# ── Event helpers ─────────────────────────────────────────────────────────────

def _ev(type_: str, **kw) -> dict:
    return {"type": type_, "ts": round(time.monotonic() * 1000), **kw}


# ── Main class ────────────────────────────────────────────────────────────────

class HarnessOrchestrator:
    """
    LLM-driven harness master.

    Runs a plan → dispatch → evaluate loop until the LLM calls finish()
    or the round cap is hit.
    """

    def __init__(
        self,
        available_targets: list[str] | None = None,
        model: str | None = None,
        max_rounds: int = MAX_ROUNDS,
        task_stagger_seconds: float = 0.4,
        backend_router=None,
        injection_queue: asyncio.Queue | None = None,
        sqlite_store=None,
        wire_log=None,
    ):
        cfg = get_config()
        rt = get_runtime_config()
        self.cfg = cfg
        self.backend_url = cfg["backend"]["url"].rstrip("/")
        self.backend_router = backend_router
        self.injection_queue = injection_queue
        self.model = (
            model
            or (rt and rt.get("operator_model"))
            or cfg.get("operator", {}).get("model")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self.max_rounds = max_rounds
        
        # Read harness config for retry and stagger settings
        harness_cfg = cfg.get("harness", {})
        retry_cfg = harness_cfg.get("retry", {})
        stagger_cfg = harness_cfg.get("stagger", {})
        
        self.max_retries = retry_cfg.get("max_retries", 2)
        self.backoff_base = retry_cfg.get("backoff_base", 1.5)
        self.backoff_max = retry_cfg.get("backoff_max", 10)
        
        self.operator_stagger_seconds = stagger_cfg.get("operator_seconds", 1.0)
        self.model_stagger_seconds = stagger_cfg.get("model_seconds", task_stagger_seconds or 0.4)
        
        # Timeouts per target type
        timeout_cfg = harness_cfg.get("timeouts", {})
        self.task_timeout = timeout_cfg.get("task_seconds", 120)
        self.operator_timeout = timeout_cfg.get("operator_seconds", 180)
        
        # Storage settings
        self.store_runs = harness_cfg.get("store_runs", True)
        
        # Targets the orchestrator knows about: "operator" or "model:<id>"
        self.available_targets: list[str] = available_targets or ["operator"]
        
        # Current run tracking for storage
        self.run_id: str | None = None
        self.run_start_time: float | None = None

        # Tap / wire observability
        self._wire_db = sqlite_store
        self._wire_log = wire_log  # WireLog instance for JSONL output

    # ── Wire tap ──────────────────────────────────────────────────────────────

    def _wire(
        self,
        event_type: str,
        run_id: str,
        content: str = "",
        turn_id: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Fire-and-forget structured tap event. Never raises.

        Writes to both SQLite (wire_events table) and the WireLog JSONL file
        so events appear in /api/v1/tap regardless of which backend is active.
        """
        # Build shared fields used by both sinks
        _meta = meta or {}
        elapsed_ms = _meta.get("latency_ms")
        token_estimate = max(1, len(content) // 4) if content else 0

        # WireLog handles both JSONL and SQLite (dual-write) when it has a
        # sqlite_store.  We only fall back to a direct SQLite write when there
        # is no WireLog instance at all.
        if self._wire_log is not None:
            try:
                self._wire_log.log(
                    direction="internal",
                    role="harness",
                    content=content,
                    model=self.model or "",
                    token_count=token_estimate,
                    latency_ms=elapsed_ms,
                    event_type=event_type,
                    source="harness",
                    run_id=run_id,
                    turn_id=turn_id,
                    meta=meta,
                )
            except Exception as e:
                logger.debug("_wire JSONL failed (%s): %s", event_type, e)
        elif self._wire_db is not None:
            # Fallback: no WireLog, write directly to SQLite
            try:
                self._wire_db.log_wire_event(
                    event_type=event_type,
                    source="harness",
                    content=content,
                    role="harness",
                    model=self.model or "",
                    run_id=run_id,
                    turn_id=turn_id,
                    meta=meta,
                )
            except Exception as e:
                logger.debug("_wire SQLite failed (%s): %s", event_type, e)

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self, goal: str) -> AsyncGenerator[dict, None]:
        """
        Async generator. Yields event dicts as work progresses:

          {type:"start",   goal, model, targets, run_id}
          {type:"plan",    round:1, tasks:[{target, prompt, rationale}]}
          {type:"dispatch",round:1, task_count:3}
          {type:"result",  round:1, target:"llama3.2:3b", content:"...", latency_ms:1234, status:"done"|"error", attempts:N}
          {type:"evaluate",round:1, assessment:"...", action:"continue"|"finish"}
          {type:"finish",  answer:"...", rounds:2, capped:false}
          {type:"error",   message:"..."}
        """
        # Initialize run tracking
        self.run_id = uuid4().hex[:16]
        self.run_start_time = time.time()
        _run_id = self.run_id

        # history accumulates every task result across all rounds. The planner
        # and evaluator receive the full history so they can build on prior work
        # rather than re-discovering the same facts each round.
        history: list[dict] = []
        round_num = 0

        self._wire("harness_start", _run_id, content=goal,
                   meta={"model": self.model, "targets": self.available_targets})

        yield _ev("start", run_id=_run_id, goal=goal, model=self.model, targets=self.available_targets)

        injections: list[str] = []

        try:
          while round_num < self.max_rounds:
            round_num += 1
            _turn_id = f"{_run_id}:r{round_num}"

            # Drain any user-injected steering messages before planning
            if self.injection_queue:
                while not self.injection_queue.empty():
                    try:
                        msg = self.injection_queue.get_nowait()
                        injections.append(msg)
                        self._wire("harness_inject", _run_id, content=msg,
                                   turn_id=_turn_id, meta={"round": round_num})
                        yield _ev("injected", message=msg, round=round_num)
                    except asyncio.QueueEmpty:
                        break

            # ── 1. Plan ───────────────────────────────────────────────────────
            try:
                plan_result = await self._plan(goal, history, round_num, injections=injections)
                injections.clear()
            except Exception as e:
                self._wire("harness_error", _run_id, content=str(e),
                           turn_id=_turn_id, meta={"phase": "plan", "round": round_num})
                yield _ev("error", message=f"Planning failed: {e}")
                return

            action = plan_result.get("action", "dispatch")

            if action == "finish":
                answer = plan_result.get("answer", "")
                self._wire("harness_end", _run_id, content=answer,
                           meta={"rounds": round_num - 1, "capped": False, "via": "plan_finish",
                                 "latency_ms": round((time.time() - self.run_start_time) * 1000, 1)})
                yield _ev("finish", answer=answer, rounds=round_num - 1)
                return

            tasks = plan_result.get("tasks", [])
            reasoning = plan_result.get("reasoning", "")

            # Stamp each task with a deterministic ID (round + index) so the UI
            # can give each task its own card even when the same target appears
            # more than once in a round. setdefault preserves any id the LLM set.
            for i, t in enumerate(tasks):
                t.setdefault("task_id", f"r{round_num}-t{i}")

            self._wire("harness_plan", _run_id, content=reasoning,
                       turn_id=_turn_id,
                       meta={"round": round_num, "task_count": len(tasks),
                             "tasks": [{"target": t.get("target"), "task_id": t.get("task_id")} for t in tasks]})
            yield _ev("plan", round=round_num, reasoning=reasoning, tasks=tasks)

            if not tasks:
                self._wire("harness_end", _run_id, content="No tasks generated.",
                           meta={"rounds": round_num, "capped": False, "via": "empty_plan",
                                 "latency_ms": round((time.time() - self.run_start_time) * 1000, 1)})
                yield _ev("finish", answer="No tasks generated — goal may be too vague.", rounds=round_num)
                return

            # ── 2. Dispatch ───────────────────────────────────────────────────
            self._wire("harness_dispatch", _run_id,
                       turn_id=_turn_id,
                       meta={"round": round_num, "task_count": len(tasks)})
            yield _ev("dispatch", round=round_num, task_count=len(tasks))

            results = []
            async for r in self._dispatch(tasks):
                _task_turn_id = f"{_turn_id}:{r.get('task_id', '')}"
                _r_content = r.get("content", "")
                self._wire("harness_turn", _run_id,
                           content=_r_content[:500],
                           turn_id=_task_turn_id,
                           meta={"round": round_num, "target": r.get("target"),
                                 "status": r.get("status"), "latency_ms": r.get("latency_ms"),
                                 "attempts": r.get("attempts", 1),
                                 "content_tokens": max(1, len(_r_content) // 4)})

                # Attempt thought extraction from turn content (JSON responses from operator)
                _extracted_thought: str | None = None
                try:
                    _parsed_turn = json.loads(_r_content)
                    if isinstance(_parsed_turn, dict):
                        _extracted_thought = _parsed_turn.get("thought") or _parsed_turn.get("reasoning")
                except Exception:
                    pass
                if _extracted_thought:
                    self._wire("harness_thought", _run_id,
                               content=_extracted_thought[:500],
                               turn_id=_task_turn_id,
                               meta={"round": round_num, "target": r.get("target"),
                                     "source": "turn_content"})

                yield _ev("result", round=round_num, **r)
                history.append({"round": round_num, **r})
                results.append(r)

            # ── 3. Evaluate ───────────────────────────────────────────────────
            try:
                eval_result = await self._evaluate(goal, history, round_num)
            except Exception as e:
                self._wire("harness_error", _run_id, content=str(e),
                           turn_id=_turn_id, meta={"phase": "evaluate", "round": round_num})
                yield _ev("error", message=f"Evaluation failed: {e}")
                return

            eval_action = eval_result.get("action", "continue")
            assessment = eval_result.get("assessment", "")

            self._wire("harness_evaluate", _run_id, content=assessment,
                       turn_id=_turn_id,
                       meta={"round": round_num, "action": eval_action})
            yield _ev("evaluate", round=round_num, assessment=assessment,
                      action=eval_action)

            if eval_action == "finish":
                answer = eval_result.get("answer", "")
                self._wire("harness_end", _run_id, content=answer,
                           meta={"rounds": round_num, "capped": False, "via": "evaluate_finish",
                                 "latency_ms": round((time.time() - self.run_start_time) * 1000, 1)})
                yield _ev("finish", answer=answer, rounds=round_num)
                return

        except Exception as e:
            self._wire("harness_error", _run_id, content=str(e),
                       meta={"phase": "run_loop", "round": round_num})
            raise

        # Hit round cap — synthesize best-effort answer
        try:
            final = await self._synthesize(goal, history)
        except Exception:
            final = "Round limit reached. See intermediate results above."
        self._wire("harness_end", _run_id, content=final[:500],
                   meta={"rounds": round_num, "capped": True,
                         "latency_ms": round((time.time() - self.run_start_time) * 1000, 1)})
        yield _ev("finish", answer=final, rounds=round_num, capped=True)

    # ── LLM calls ─────────────────────────────────────────────────────────────

    async def _plan(self, goal: str, history: list[dict], round_num: int, injections: list[str] | None = None) -> dict:
        """
        Ask the orchestrator LLM to produce a task plan (or finish if done).
        Returns: {action: "dispatch"|"finish", tasks: [...], reasoning: "...", answer: "..."}
        """
        target_list = "\n".join(
            f"  - {t}" for t in self.available_targets
        )
        history_summary = self._format_history(history) if history else "No results yet."

        system = (
            "You are a harness orchestrator. Your job is to break a goal into parallel subtasks "
            "and assign each to the best available agent or model. "
            "You will be called repeatedly until the goal is fully addressed.\n\n"
            f"Available targets:\n{target_list}\n\n"
            "Respond ONLY with valid JSON matching one of these schemas:\n\n"
            "If you have enough information to answer the goal:\n"
            '{"action":"finish","answer":"<complete answer>","reasoning":"<why done>"}\n\n'
            "If more work is needed:\n"
            '{"action":"dispatch","reasoning":"<why these tasks>","tasks":['
            '{"target":"<target from list>","prompt":"<specific task prompt>","rationale":"<why this target>"}'
            "]}\n\n"
            f"Rules:\n"
            f"- Max {MAX_TASKS_PER_ROUND} tasks per round\n"
            "- Be specific in prompts — each target only sees its own task\n"
            "- Use 'operator' for tasks needing tools, memory, or web search\n"
            "- Use model targets for generation, analysis, critique, or parallel perspectives\n"
            "- Respond with ONLY the JSON object, no markdown, no explanation outside JSON"
        )

        injection_block = ""
        if injections:
            msgs = "\n".join(f"  - {m}" for m in injections)
            injection_block = f"\n\nSTEERING INSTRUCTIONS FROM USER (high priority — follow these):\n{msgs}"

        user = (
            f"Goal: {goal}\n\n"
            f"Round: {round_num}\n\n"
            f"Results so far:\n{history_summary}"
            f"{injection_block}"
        )

        raw = await self._llm_call(system, user)
        return self._parse_json(raw, fallback={"action": "dispatch", "tasks": [], "reasoning": raw})

    async def _evaluate(self, goal: str, history: list[dict], round_num: int) -> dict:
        """
        Ask the LLM if the collected results are sufficient to answer the goal.
        Returns: {action: "finish"|"continue", assessment: "...", answer: "..."}
        """
        system = (
            "You are evaluating whether a set of parallel agent results fully addresses a goal.\n"
            "Respond ONLY with valid JSON:\n\n"
            "If the goal is fully addressed:\n"
            '{"action":"finish","answer":"<synthesized complete answer>","assessment":"<why sufficient>"}\n\n'
            "If more work is needed:\n"
            '{"action":"continue","assessment":"<what is missing or needs refinement>"}\n\n'
            "Respond with ONLY the JSON object."
        )

        user = (
            f"Goal: {goal}\n\n"
            f"Round {round_num} results:\n{self._format_history(history)}"
        )

        raw = await self._llm_call(system, user)
        return self._parse_json(raw, fallback={"action": "continue", "assessment": raw})

    async def _synthesize(self, goal: str, history: list[dict]) -> str:
        """Final synthesis when round cap is hit."""
        system = (
            "Synthesize the following parallel agent results into a single coherent answer "
            "that best addresses the original goal. Be concise and direct."
        )
        user = f"Goal: {goal}\n\nAll results:\n{self._format_history(history)}"
        return await self._llm_call(system, user)

    async def _llm_call(self, system: str, user: str) -> str:
        """Single non-streaming LLM call to the orchestrator model."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": 0.2,
        }
        if self.backend_router:
            resp = await self.backend_router.forward(body)
            if not resp.ok:
                raise Exception(resp.error or f"backend error {resp.status_code}")
            return resp.content
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer beigebox"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch(self, tasks: list[dict]) -> AsyncGenerator[dict, None]:
        """
        Run all tasks concurrently, yielding each result as soon as it completes.

        Operator tasks use higher stagger (1.0s) to avoid ChromaDB lock contention.
        Model tasks use lower stagger (0.4s).
        """
        capped = tasks[:MAX_TASKS_PER_ROUND]
        if not capped:
            return

        queue: asyncio.Queue = asyncio.Queue()

        async def _run_and_enqueue(i: int, task: dict) -> None:
            target = task.get("target", "")
            # Operator tasks stagger more (1.0s) than model tasks (0.4s) because
            # the operator opens SQLite/ChromaDB; rapid concurrent opens cause
            # "database is locked" errors under high parallelism.
            stagger = self.operator_stagger_seconds if target == "operator" else self.model_stagger_seconds
            if i > 0:
                await asyncio.sleep(i * stagger)
            result = await self._run_task(task)
            await queue.put(result)

        job_tasks = [asyncio.create_task(_run_and_enqueue(i, t)) for i, t in enumerate(capped)]

        # Drain the shared queue until all N tasks have posted their result.
        # Yields each result as soon as it arrives (fan-out, stream-back pattern).
        pending = len(capped)
        while pending > 0:
            result = await queue.get()
            yield result
            pending -= 1

        # Await all tasks to propagate any unhandled exceptions before returning.
        # return_exceptions=True prevents a single failure from cancelling others.
        await asyncio.gather(*job_tasks, return_exceptions=True)

    async def _run_task(self, task: dict) -> dict:
        """
        Run a single task with retry logic and exponential backoff.

        Retryable errors (timeout, connection, not_found, internal_error)
        retry up to max_retries times with exponential backoff.
        Non-retryable errors (rate_limit, unknown) fail immediately.
        """
        target = task.get("target", "")
        prompt = task.get("prompt", "")
        rationale = task.get("rationale", "")
        task_id = task.get("task_id", f"{target}_{uuid4().hex[:6]}")
        t0 = time.monotonic()
        
        for attempt in range(self.max_retries + 1):
            try:
                if target == "operator":
                    content = await self._run_operator(prompt)
                elif target.startswith("model:"):
                    model_id = target[6:]
                    content = await self._run_model(model_id, prompt)
                else:
                    # Try as a bare model name
                    content = await self._run_model(target, prompt)

                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                return {
                    "task_id": task_id,
                    "target": target,
                    "prompt": prompt,
                    "rationale": rationale,
                    "content": content,
                    "latency_ms": latency_ms,
                    "status": "done",
                    "attempts": attempt + 1,
                }
            
            except Exception as e:
                # Classify the error
                error_type = self._classify_error(e)
                
                # Retry if error is retryable and attempts remain
                if attempt < self.max_retries and error_type in RETRYABLE_ERRORS:
                    wait_time = min(
                        self.backoff_base ** attempt,
                        self.backoff_max
                    )
                    logger.warning(
                        f"Task {target} failed (attempt {attempt+1}/{self.max_retries+1}): "
                        f"{error_type} — retrying in {wait_time:.1f}s: {str(e)[:100]}"
                    )
                    await asyncio.sleep(wait_time)
                    continue  # Retry
                
                # Final failure: non-retryable error or out of retries
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                return {
                    "task_id": task_id,
                    "target": target,
                    "prompt": prompt,
                    "rationale": rationale,
                    "content": f"Error: {error_type.upper()} — {str(e)[:500]}",
                    "latency_ms": latency_ms,
                    "status": "error",
                    "error_type": error_type,
                    "attempts": attempt + 1,
                }

        # Should never reach here, but safety net
        return {
            "task_id": task_id,
            "target": target,
            "prompt": prompt,
            "rationale": rationale,
            "content": "Error: Unknown failure after all retries",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            "status": "error",
            "error_type": "unknown",
            "attempts": self.max_retries + 1,
        }

    async def _run_operator(self, query: str) -> str:
        """Route a task to the BeigeBox operator agent via its own endpoint."""
        port = self.cfg.get("server", {}).get("port", 8000)
        api_key = self.cfg.get("auth", {}).get("api_key", "")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # Use 127.0.0.1 explicitly — 'localhost' can fail inside Docker
        # depending on /etc/hosts configuration.
        async with httpx.AsyncClient(timeout=self.operator_timeout) as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/api/v1/operator",
                json={"query": query},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("error") or str(data)

    async def _run_model(self, model_id: str, prompt: str) -> str:
        """Run a prompt against a specific model."""
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if self.backend_router:
            resp = await self.backend_router.forward(body)
            if not resp.ok:
                raise Exception(resp.error or f"backend error {resp.status_code}")
            return resp.content
        async with httpx.AsyncClient(timeout=self.task_timeout) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer beigebox"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """
        Classify exception into retryable categories.
        
        Returns:
            "timeout" — connection timeout (retryable)
            "connection" — connection refused/reset (retryable)
            "not_found" — 404 model not found (retryable, model may be loading)
            "rate_limit" — 429 rate limited (retryable but with longer backoff)
            "internal_error" — 500/502/503 server error (retryable)
            "unknown" — other error (non-retryable)
        """
        # String-based classification avoids importing httpx here (circular risk)
        # and works with both httpx exceptions and generic Exception strings.
        exc_str = str(exc).lower()

        if "timeout" in exc_str or "timed out" in exc_str:
            return "timeout"
        if "connection" in exc_str or "refused" in exc_str or "reset" in exc_str:
            return "connection"
        # 404 from Ollama = model not loaded yet; retry after backoff often succeeds
        if "404" in exc_str or "not found" in exc_str:
            return "not_found"
        if "429" in exc_str:
            return "rate_limit"
        if "500" in exc_str or "502" in exc_str or "503" in exc_str:
            return "internal_error"
        return "unknown"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _format_history(self, history: list[dict]) -> str:
        """Format results, highlighting retried/failed tasks."""
        if not history:
            return "None."
        parts = []
        for i, r in enumerate(history):
            status_marker = "✗" if r.get("status") == "error" else "✓"
            attempts = r.get("attempts", 1)
            attempts_note = f" ({attempts} attempts)" if attempts > 1 else ""
            
            parts.append(
                f"[{i+1}] {status_marker} Round {r.get('round','')} · {r.get('target','')} "
                f"({r.get('latency_ms',0):.0f}ms){attempts_note}\n"
                f"Task: {r.get('prompt','')[:200]}\n"
                f"Result: {r.get('content','')[:600]}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _parse_json(raw: str, fallback: dict) -> dict:
        """
        Try to parse JSON from LLM output with multiple recovery strategies.

        Handles:
          - Markdown fences (```json ... ``` or ``` ... ```)
          - Leading/trailing prose around a JSON object
          - Trailing commas (common small-model mistake)
          - Truncated JSON (attempt recovery by closing open braces/brackets)
        """
        import re

        text = raw.strip()

        # 1. Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Drop first line (```json or ```) and last line if it's a closing fence
            inner = lines[1:]
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            text = "\n".join(inner).strip()

        def _try_parse(s: str) -> dict | None:
            """Attempt json.loads with trailing-comma cleanup."""
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                # Remove trailing commas before } or ]
                cleaned = re.sub(r",\s*([}\]])", r"\1", s)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    return None

        # 2. Direct parse
        result = _try_parse(text)
        if result is not None:
            return result

        # 3. Extract first {...} block (handles leading/trailing prose)
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            result = _try_parse(m.group())
            if result is not None:
                return result

        # 4. Truncation recovery — small models sometimes hit their max_tokens
        #    mid-object. Count unclosed braces (tracking string state so brace
        #    chars inside string literals don't affect depth), then append the
        #    missing closers and retry the parse.
        try:
            depth = 0
            in_str = False
            escape = False
            for ch in text:
                if escape:
                    escape = False
                    continue
                if ch == '\\' and in_str:
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if not in_str:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
            if depth > 0:
                repaired = text.rstrip().rstrip(',') + "}" * depth
                result = _try_parse(repaired)
                if result is not None:
                    logger.debug("HarnessOrchestrator: recovered truncated JSON (closed %d brace(s))", depth)
                    return result
        except Exception:
            pass

        logger.warning("HarnessOrchestrator: could not parse JSON from LLM output: %s", raw[:200])
        return fallback
