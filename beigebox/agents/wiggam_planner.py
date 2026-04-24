"""
WiggamPlanner — consensus-driven task decomposition before the Ralph loop.

Chief Wiggam proposes a breakdown of the goal into granular tasks.
His officers critique it. Wiggam refines. Repeat until officers agree
every task is simple enough for Ralph to execute in one iteration.

The granularity gate: "Could Ralph handle this step on his own?"
If any officer says no to any task, that task gets broken down further.

Flow:
  Round 1:  Wiggam reads goal → proposes plan (tasks + acceptance criteria + test command)
  Round N:  Officers critique → vote per task (simple_enough / too_complex + reason)
            Wiggam synthesizes feedback → refines failing tasks
            Loop until consensus or round cap

Output (when consensus reached):
  {
    "spec_md":    "...",        # content for PROMPT.md
    "test_cmd":   "pytest ...", # shell command (exit 0 = done)
    "tasks":      [...],        # final task list with criteria
    "rounds":     N,
    "consensus":  True,
  }

Events emitted:
  {type: "start",         run_id, goal, wiggam_model, officer_models, max_rounds}
  {type: "wiggam_plan",   round, plan_md, tasks, test_cmd, reasoning}
  {type: "officer_vote",  round, officer_model, votes, feedback}
  {type: "consensus",     round, passed, failing_tasks}
  {type: "finish",        spec_md, test_cmd, tasks, rounds, consensus}
  {type: "error",         message}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator
from uuid import uuid4

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5


def _ev(type_: str, **kw) -> dict:
    return {"type": type_, "ts": round(time.monotonic() * 1000), **kw}


_WIGGAM_SYSTEM = """\
You are Chief Clancy Wiggam, head of planning for a software development team.
You are not a genius, but you are persistent, well-meaning, and you have learned
one important lesson from years of supervising your son Ralph:

  A task is only ready to execute if Ralph could do it.
  Ralph can write a single function, fix a specific failing test, or add one
  clearly-defined feature. Ralph cannot "design the architecture", "figure out
  the best approach", or "handle the edge cases". Those are not tasks — they are
  homework assignments that Ralph will just stare at.

Your job is to break a high-level goal into tasks that are:
  - Atomic: one file changed, one function written, one test fixed
  - Testable: there is a specific, runnable command that says pass or fail
  - Unambiguous: no creative interpretation required
  - Small: completable in a single focused coding session

When your officers say a task is too complex, they are right. Break it down further.
Do not argue with them about scope — just make the tasks smaller.

Always respond in valid JSON matching the schema given in the user message.
"""

_OFFICER_SYSTEM = """\
You are a senior software engineer reviewing a task breakdown proposed by your chief.
Your job is to identify tasks that are still too complex for a junior developer
(or an LLM running autonomously without human guidance).

A task is TOO COMPLEX if:
  - It requires architectural decisions ("design the X system")
  - It touches more than ~3 files
  - It has more than one acceptance criterion
  - The acceptance criterion is subjective ("works correctly", "handles edge cases")
  - A junior developer would need to ask clarifying questions before starting
  - It requires knowledge not present in the codebase or spec

A task is SIMPLE ENOUGH if:
  - It is a single, clearly-described code change
  - You can describe exactly what file(s) to edit and what the result looks like
  - The test command will definitively verify it

Be honest and direct. If a task is too complex, say so and explain exactly what
makes it complex. Do not rubber-stamp tasks to be polite.

Respond in valid JSON matching the schema given in the user message.
"""


class WiggamPlanner:
    """
    Multi-agent planning loop: Wiggam proposes, officers critique, repeat.
    """

    def __init__(
        self,
        goal: str,
        wiggam_model: str | None = None,
        officer_models: list[str] | None = None,
        max_rounds: int = MAX_ROUNDS,
        backend_router=None,
    ):
        cfg = get_config()
        self.cfg = cfg
        self.backend_url = cfg["backend"]["url"].rstrip("/")
        self.backend_router = backend_router
        self.goal = goal
        self.run_id = str(uuid4())[:8]
        self.max_rounds = max_rounds

        default_model = (
            cfg.get("models", {}).get("profiles", {}).get("agentic")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self.wiggam_model = wiggam_model or default_model
        self.officer_models = officer_models or [default_model]

    async def _call_json(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
    ) -> dict | None:
        """Call a model and parse JSON from the response."""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "temperature": temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.backend_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                from beigebox.response_normalizer import normalize_response
                content = normalize_response(resp.json()).content
                # Strip markdown code fences if present
                content = content.strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(
                        lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                    )
                return json.loads(content)
        except Exception as e:
            logger.warning(f"_call_json failed (model={model}): {e}")
            return None

    async def _wiggam_plan(self, round_: int, prev_feedback: list[dict]) -> dict | None:
        """Ask Wiggam to produce / refine the plan."""
        feedback_section = ""
        if prev_feedback:
            lines = ["## Officer Feedback from Last Round\n"]
            for fb in prev_feedback:
                lines.append(f"### {fb['officer_model']}")
                for v in fb.get("votes", []):
                    if not v.get("simple_enough"):
                        lines.append(
                            f"- Task '{v['task_id']}': TOO COMPLEX — {v.get('reason', '')}"
                        )
            feedback_section = "\n".join(lines)

        user_prompt = f"""## Goal
{self.goal}

{feedback_section}

## Your Task
Produce a task breakdown that satisfies this goal.
Every task must pass the "could Ralph do this?" test.
Also propose a single shell test command that will verify the full goal is complete.

Respond ONLY with valid JSON in this exact schema:
{{
  "reasoning": "brief explanation of your breakdown approach",
  "test_cmd": "shell command to verify the whole goal (e.g. pytest tests/ -x)",
  "tasks": [
    {{
      "task_id": "t1",
      "title": "short title",
      "description": "exactly what to do",
      "files_affected": ["path/to/file.py"],
      "acceptance_criterion": "one specific, testable statement of done"
    }}
  ]
}}
"""
        return await self._call_json(self.wiggam_model, _WIGGAM_SYSTEM, user_prompt, temperature=0.4)

    async def _officer_vote(
        self,
        officer_model: str,
        plan: dict,
        round_: int,
    ) -> dict | None:
        """Ask an officer to vote on each task."""
        tasks_json = json.dumps(plan.get("tasks", []), indent=2)
        user_prompt = f"""## Goal
{self.goal}

## Proposed Task Breakdown (Round {round_})
{tasks_json}

## Proposed Test Command
{plan.get('test_cmd', '(none)')}

## Your Task
Review each task. For each one, decide: is it SIMPLE ENOUGH for an autonomous
LLM agent to execute in a single session without human guidance?

Respond ONLY with valid JSON in this exact schema:
{{
  "overall_feedback": "one sentence summary",
  "votes": [
    {{
      "task_id": "t1",
      "simple_enough": true,
      "reason": "brief justification (required if simple_enough is false)"
    }}
  ],
  "test_cmd_ok": true,
  "test_cmd_feedback": "any issues with the test command (or empty string)"
}}
"""
        return await self._call_json(officer_model, _OFFICER_SYSTEM, user_prompt, temperature=0.2)

    def _check_consensus(self, officer_votes: list[dict]) -> tuple[bool, list[str]]:
        """
        Returns (consensus_reached, list_of_failing_task_ids).
        Consensus = every task rated simple_enough by majority of officers.
        """
        # Tally votes per task
        task_votes: dict[str, list[bool]] = {}
        for ov in officer_votes:
            for v in (ov.get("votes") or []):
                tid = v.get("task_id", "?")
                task_votes.setdefault(tid, []).append(bool(v.get("simple_enough", True)))

        # Majority rule: a task fails consensus only if more than half of
        # the officers marked it too complex. This tolerates one dissenting
        # officer without forcing Wiggam to re-break an already-fine task.
        failing = [
            tid for tid, votes in task_votes.items()
            if votes.count(False) > len(votes) / 2
        ]
        return len(failing) == 0, failing

    def _build_spec_md(self, plan: dict) -> str:
        """Convert the agreed plan into a PROMPT.md for Ralph."""
        lines = [
            "# Task Specification",
            "",
            f"**Goal:** {self.goal}",
            "",
            "## Tasks",
            "",
        ]
        for t in plan.get("tasks", []):
            lines += [
                f"### {t.get('task_id', '?')}: {t.get('title', '')}",
                "",
                t.get("description", ""),
                "",
                f"**Files:** {', '.join(t.get('files_affected', ['(see description)']))}",
                f"**Done when:** {t.get('acceptance_criterion', '')}",
                "",
            ]
        lines += [
            "## Constraints",
            "- Complete tasks in order",
            "- Do not modify files outside the listed paths unless strictly necessary",
            "- Each task should be completable and testable independently",
            "",
            f"## Verification",
            f"Run `{plan.get('test_cmd', 'make test')}` — all tests must pass.",
        ]
        return "\n".join(lines)

    async def run(self) -> AsyncGenerator[dict, None]:
        """Main planning loop. Yields SSE-compatible event dicts."""
        yield _ev(
            "start",
            run_id=self.run_id,
            goal=self.goal,
            wiggam_model=self.wiggam_model,
            officer_models=self.officer_models,
            max_rounds=self.max_rounds,
        )

        prev_feedback: list[dict] = []
        last_plan: dict = {}

        for round_ in range(1, self.max_rounds + 1):
            # ── Wiggam proposes / refines ─────────────────────────────────────
            plan = await self._wiggam_plan(round_, prev_feedback)
            if not plan:
                yield _ev("error", message=f"Wiggam failed to produce a plan (round {round_})")
                return

            last_plan = plan
            yield _ev(
                "wiggam_plan",
                round=round_,
                reasoning=plan.get("reasoning", ""),
                test_cmd=plan.get("test_cmd", ""),
                tasks=plan.get("tasks", []),
                plan_md=self._build_spec_md(plan),
            )

            # ── Officers vote ─────────────────────────────────────────────────
            # All officer votes are dispatched in parallel — same round, same plan.
            # return_exceptions=True means a single failed officer doesn't abort
            # the round; that officer's vote is simply excluded from consensus.
            vote_tasks = [
                self._officer_vote(m, plan, round_)
                for m in self.officer_models
            ]
            officer_results = await asyncio.gather(*vote_tasks, return_exceptions=True)

            prev_feedback = []
            all_votes: list[dict] = []

            for model, result in zip(self.officer_models, officer_results):
                if isinstance(result, Exception) or result is None:
                    continue
                prev_feedback.append({"officer_model": model, **result})
                all_votes.append(result)
                yield _ev(
                    "officer_vote",
                    round=round_,
                    officer_model=model,
                    votes=result.get("votes", []),
                    overall_feedback=result.get("overall_feedback", ""),
                    test_cmd_ok=result.get("test_cmd_ok", True),
                    test_cmd_feedback=result.get("test_cmd_feedback", ""),
                )

            # ── Consensus check ───────────────────────────────────────────────
            consensus, failing = self._check_consensus(all_votes)
            yield _ev(
                "consensus",
                round=round_,
                passed=consensus,
                failing_tasks=failing,
                task_count=len(plan.get("tasks", [])),
            )

            if consensus:
                spec_md = self._build_spec_md(last_plan)
                yield _ev(
                    "finish",
                    spec_md=spec_md,
                    test_cmd=last_plan.get("test_cmd", ""),
                    tasks=last_plan.get("tasks", []),
                    rounds=round_,
                    consensus=True,
                )
                return

        # Hit round cap without full consensus. Emit the last plan anyway with
        # consensus=False so the caller can decide whether to proceed with Ralph
        # or surface the partial plan to the user for manual review.
        spec_md = self._build_spec_md(last_plan)
        yield _ev(
            "finish",
            spec_md=spec_md,
            test_cmd=last_plan.get("test_cmd", ""),
            tasks=last_plan.get("tasks", []),
            rounds=self.max_rounds,
            consensus=False,
        )
