"""
Opportunity #1: Composition Strategy Variance per Worker Type

Hypothesis: Different agent worker roles need different context budgets.
Operator: 600 tokens (full tool calls). Researcher: 200 tokens + sources.
Judge: 100 tokens + options only.

Expected impact: +8-15% per worker type (MEDIUM confidence)

Variants
--------
- baseline_uniform:   Same context for all workers (current)
- operator_heavy:     600-token budget, include tool call history
- researcher_lean:    200-token budget, source citations only
- judge_minimal:      100-token budget, options + question only
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "Tool call: web_search('FastAPI deployment AWS') → 3 results returned.",
    "Source [1]: AWS documentation on ECS Fargate deployment.",
    "Source [2]: FastAPI official deployment guide.",
    "Tool call: calculator('320 / 200 * 100') → 160% of target latency.",
    "Option A: Horizontal scaling — add 2 more instances ($800/mo).",
    "Option B: Caching layer — Redis TTL 5min ($200/mo).",
    "Option C: Query optimization — 2 weeks engineering effort.",
    "Research finding: Caching reduces P95 by ~40% for read-heavy workloads.",
    "Research finding: Horizontal scaling has linear cost but linear capacity gain.",
    "Judge context: Three options evaluated. Budget constraint: $500/mo max.",
]


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


def _approx_token_count(text: str) -> int:
    return len(text) // 4  # rough 4-chars-per-token approximation


class CompositionStrategyExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "composition_strategy"
    OPPORTUNITY_NAME = "Composition Strategy per Worker Type (#1)"
    HYPOTHESIS = "Worker-specific context budgets improve relevance and accuracy"
    EXPECTED_IMPACT = "+8-15% per worker type"
    WEIGHT_PROFILE = "reasoning"

    VARIANTS = [
        {"name": "baseline_uniform",  "worker": "uniform",    "budget_tokens": None},
        {"name": "operator_heavy",    "worker": "operator",   "budget_tokens": 600},
        {"name": "researcher_lean",   "worker": "researcher", "budget_tokens": 200},
        {"name": "judge_minimal",     "worker": "judge",      "budget_tokens": 100},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        worker = variant_config.get("worker", "uniform")
        budget = variant_config.get("budget_tokens")
        system, context, question = _get_body_turns(messages)

        if worker == "uniform" or not context:
            return messages

        if worker == "operator":
            # Keep tool call turns (lines with "Tool call:")
            tool_turns = [m for m in context if "Tool call:" in m.get("content", "")]
            other_turns = [m for m in context if "Tool call:" not in m.get("content", "")]
            # Fill budget: tool calls first, then other turns
            selected = tool_turns + other_turns
            return system + selected + [question]

        if worker == "researcher":
            # Keep source citation turns only
            source_turns = [m for m in context if "Source [" in m.get("content", "") or "finding:" in m.get("content", "")]
            return system + source_turns + [question]

        if worker == "judge":
            # Keep only option turns
            option_turns = [m for m in context if m.get("content", "").startswith(("Noted: Option", "Remember this: Option", "Option"))]
            # Also keep budget constraint
            budget_turns = [m for m in context if "Budget" in m.get("content", "") or "constraint" in m.get("content", "")]
            return system + budget_turns + option_turns + [question]

        # Budget trimming fallback
        if budget:
            trimmed = []
            tokens_used = _approx_token_count(question.get("content", ""))
            for msg in reversed(context):
                cost = _approx_token_count(msg.get("content", ""))
                if tokens_used + cost <= budget:
                    trimmed.insert(0, msg)
                    tokens_used += cost
                else:
                    break
            return system + trimmed + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Which tool calls were made and what did they return?",
                expected="web_search",
                task_type="operator_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What sources were consulted?",
                expected="AWS documentation",
                task_type="researcher_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What are the options being evaluated?",
                expected="Option A",
                task_type="judge_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the budget constraint?",
                expected="$500",
                task_type="judge_constraint",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Based on the research, which option best fits the budget?",
                expected="Caching",
                task_type="reasoning",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What did the calculator tool compute?",
                expected="160%",
                task_type="operator_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Summarize the research findings.",
                expected="40%",
                task_type="researcher_synthesis",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Which option is most cost effective given the budget?",
                expected="Option B",
                task_type="judge_decision",
            ),
        ]
