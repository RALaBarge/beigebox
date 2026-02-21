"""
HarnessOrchestrator — goal-directed multi-agent coordinator.

Given a high-level goal, this agent:
  1. Plans: breaks the goal into subtasks, assigns each to a model or the operator
  2. Dispatches: runs all subtasks in parallel via the existing ParallelOrchestrator
  3. Evaluates: decides if the results are sufficient or if more work is needed
  4. Synthesizes: produces a final answer when satisfied (or hits the iteration cap)

Designed to stream intermediate state back to the caller so the UI can show
live progress in the master pane.

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
from typing import AsyncGenerator

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_ROUNDS = 8          # Hard cap on plan→dispatch→evaluate cycles
MAX_TASKS_PER_ROUND = 6 # Max parallel subtasks per round


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
    ):
        cfg = get_config()
        self.cfg = cfg
        self.backend_url = cfg["backend"]["url"].rstrip("/")
        self.model = (
            model
            or cfg.get("operator", {}).get("model")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self.max_rounds = max_rounds
        self.task_stagger_seconds = task_stagger_seconds
        # Targets the orchestrator knows about: "operator" or "model:<id>"
        self.available_targets: list[str] = available_targets or ["operator"]

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self, goal: str) -> AsyncGenerator[dict, None]:
        """
        Async generator. Yields event dicts as work progresses:

          {type:"plan",    round:1, tasks:[{target, prompt, rationale}]}
          {type:"dispatch",round:1, task_count:3}
          {type:"result",  round:1, target:"llama3.2:3b", content:"...", latency_ms:1234}
          {type:"evaluate",round:1, assessment:"...", continue:true}
          {type:"finish",  answer:"...", rounds:2}
          {type:"error",   message:"..."}
        """
        history: list[dict] = []   # running log of all results across rounds
        round_num = 0

        yield _ev("start", goal=goal, model=self.model, targets=self.available_targets)

        while round_num < self.max_rounds:
            round_num += 1

            # ── 1. Plan ───────────────────────────────────────────────────────
            try:
                plan_result = await self._plan(goal, history, round_num)
            except Exception as e:
                yield _ev("error", message=f"Planning failed: {e}")
                return

            action = plan_result.get("action", "dispatch")

            if action == "finish":
                yield _ev("finish", answer=plan_result.get("answer", ""), rounds=round_num - 1)
                return

            tasks = plan_result.get("tasks", [])
            reasoning = plan_result.get("reasoning", "")

            yield _ev("plan", round=round_num, reasoning=reasoning, tasks=tasks)

            if not tasks:
                yield _ev("finish", answer="No tasks generated — goal may be too vague.", rounds=round_num)
                return

            # ── 2. Dispatch ───────────────────────────────────────────────────
            yield _ev("dispatch", round=round_num, task_count=len(tasks))

            results = await self._dispatch(tasks)

            for r in results:
                yield _ev("result", round=round_num, **r)
                history.append({"round": round_num, **r})

            # ── 3. Evaluate ───────────────────────────────────────────────────
            try:
                eval_result = await self._evaluate(goal, history, round_num)
            except Exception as e:
                yield _ev("error", message=f"Evaluation failed: {e}")
                return

            eval_action = eval_result.get("action", "continue")
            assessment = eval_result.get("assessment", "")

            yield _ev("evaluate", round=round_num, assessment=assessment,
                      action=eval_action)

            if eval_action == "finish":
                yield _ev("finish", answer=eval_result.get("answer", ""), rounds=round_num)
                return

        # Hit round cap — synthesize best-effort answer
        try:
            final = await self._synthesize(goal, history)
        except Exception:
            final = "Round limit reached. See intermediate results above."
        yield _ev("finish", answer=final, rounds=round_num, capped=True)

    # ── LLM calls ─────────────────────────────────────────────────────────────

    async def _plan(self, goal: str, history: list[dict], round_num: int) -> dict:
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

        user = (
            f"Goal: {goal}\n\n"
            f"Round: {round_num}\n\n"
            f"Results so far:\n{history_summary}"
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
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "temperature": 0.2,
                },
                headers={"Authorization": "Bearer beigebox"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch(self, tasks: list[dict]) -> list[dict]:
        """
        Run all tasks with a small stagger between launches.

        Firing everything simultaneously causes race conditions when multiple
        tasks route to the operator — each one tries to init a LangChain chain
        and open ChromaDB at the same time. A short stagger lets the first
        operator call get past its init before the next one arrives.

        Model-only tasks don't need the stagger (Ollama queues them fine) but
        it doesn't hurt them either — the delay is small relative to inference
        time.
        """
        capped = tasks[:MAX_TASKS_PER_ROUND]

        async def _staggered(i: int, task: dict) -> dict:
            if i > 0:
                await asyncio.sleep(i * self.task_stagger_seconds)
            return await self._run_task(task)

        jobs = [_staggered(i, t) for i, t in enumerate(capped)]
        return await asyncio.gather(*jobs, return_exceptions=False)

    async def _run_task(self, task: dict) -> dict:
        """Run a single task against the appropriate target."""
        target = task.get("target", "")
        prompt = task.get("prompt", "")
        rationale = task.get("rationale", "")
        t0 = time.monotonic()

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
                "target": target,
                "prompt": prompt,
                "rationale": rationale,
                "content": content,
                "latency_ms": latency_ms,
                "status": "done",
            }
        except Exception as e:
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            return {
                "target": target,
                "prompt": prompt,
                "rationale": rationale,
                "content": f"Error: {e}",
                "latency_ms": latency_ms,
                "status": "error",
            }

    async def _run_operator(self, query: str) -> str:
        """Route a task to the BeigeBox operator agent via its own endpoint."""
        port = self.cfg.get("server", {}).get("port", 8000)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"http://localhost:{port}/api/v1/operator",
                json={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("error") or str(data)

    async def _run_model(self, model_id: str, prompt: str) -> str:
        """Run a prompt against a specific model."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.backend_url}/v1/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                headers={"Authorization": "Bearer beigebox"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return "None."
        parts = []
        for i, r in enumerate(history):
            parts.append(
                f"[{i+1}] Round {r.get('round','')} · {r.get('target','')} "
                f"({r.get('latency_ms',0):.0f}ms)\n"
                f"Task: {r.get('prompt','')[:200]}\n"
                f"Result: {r.get('content','')[:600]}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _parse_json(raw: str, fallback: dict) -> dict:
        """Try to parse JSON from LLM output, stripping markdown fences."""
        text = raw.strip()
        # Strip ```json ... ``` fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find a JSON object in the text
            import re
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        logger.warning("HarnessOrchestrator: could not parse JSON from LLM output: %s", raw[:200])
        return fallback
