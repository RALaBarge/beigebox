"""
Opportunity #10: Few-Shot Example Selection Strategy

Hypothesis: Diverse few-shot examples (selected via k-means clustering /
max embedding distance) improve edge case accuracy vs. random selection.

Expected impact: +3-7% edge case accuracy (MEDIUM confidence)

Variants
--------
- baseline_first3:    First 3 examples from pool (current)
- random_sample:      Random 3 examples
- diverse_spread:     Examples from different task clusters (manual proxy)
- hard_cases:         Examples that include boundary conditions
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# A pool of 9 few-shot examples across 3 clusters: factual, code, edge
_EXAMPLE_POOL = [
    # Cluster A: Factual
    {"input": "What is the capital of France?", "output": "Paris", "cluster": "factual"},
    {"input": "What year was Python created?", "output": "1991", "cluster": "factual"},
    {"input": "What is the speed of light?", "output": "299,792,458 m/s", "cluster": "factual"},
    # Cluster B: Code
    {"input": "How do you reverse a list in Python?", "output": "lst[::-1]", "cluster": "code"},
    {"input": "What does len([]) return?", "output": "0", "cluster": "code"},
    {"input": "How do you open a file in Python?", "output": "open('file.txt')", "cluster": "code"},
    # Cluster C: Edge cases
    {"input": "What is 0/0?", "output": "undefined / ZeroDivisionError", "cluster": "edge"},
    {"input": "What is the square root of -1?", "output": "i (imaginary unit)", "cluster": "edge"},
    {"input": "What is infinity + 1?", "output": "still infinity", "cluster": "edge"},
]

_FIRST_3 = _EXAMPLE_POOL[:3]
_DIVERSE = [_EXAMPLE_POOL[0], _EXAMPLE_POOL[3], _EXAMPLE_POOL[6]]  # one per cluster
_HARD = _EXAMPLE_POOL[6:]  # edge cases


def _format_examples(examples: list[dict]) -> str:
    lines = []
    for ex in examples:
        lines.append(f"Q: {ex['input']}\nA: {ex['output']}")
    return "\n\n".join(lines)


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class FewShotDiversityExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "fewshot_diversity"
    OPPORTUNITY_NAME = "Few-Shot Example Selection Strategy (#10)"
    HYPOTHESIS = "Diverse few-shot examples improve edge case accuracy vs. first-N selection"
    EXPECTED_IMPACT = "+3-7% edge case accuracy"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_first3",  "fewshot_strategy": "first3"},
        {"name": "random_sample",    "fewshot_strategy": "random"},
        {"name": "diverse_spread",   "fewshot_strategy": "diverse"},
        {"name": "hard_cases",       "fewshot_strategy": "hard"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("fewshot_strategy", "first3")
        system, context, question = _get_body_turns(messages)

        if strategy == "first3":
            examples = _FIRST_3
        elif strategy == "random":
            import random
            examples = random.sample(_EXAMPLE_POOL, 3)
        elif strategy == "diverse":
            examples = _DIVERSE
        elif strategy == "hard":
            examples = _HARD
        else:
            return messages

        # Inject examples as a system prompt suffix
        ex_text = _format_examples(examples)
        if system:
            new_sys = dict(system[0])
            new_sys["content"] = system[0]["content"] + f"\n\nExamples:\n{ex_text}"
            return [new_sys] + context + [question]
        else:
            new_sys = {"role": "system", "content": f"Examples:\n{ex_text}"}
            return [new_sys] + context + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # Standard factual — all strategies should pass
            DiscoveryTestCase(
                context_facts=[],
                question="What is the capital of Germany?",
                expected="Berlin",
                task_type="standard_factual",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="How do you square a number in Python?",
                expected="**",
                task_type="standard_code",
            ),
            # Edge cases — diverse and hard strategies should win
            DiscoveryTestCase(
                context_facts=[],
                question="What happens when you divide by zero in Python?",
                expected="ZeroDivisionError",
                task_type="edge_case",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is the result of float('inf') - float('inf')?",
                expected="nan",
                task_type="edge_case",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is len(None)?",
                expected="TypeError",
                task_type="edge_case",
            ),
            # Boundary conditions
            DiscoveryTestCase(
                context_facts=[],
                question="What does [][::-1] return?",
                expected="[]",
                task_type="boundary",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is the result of 2**1000 in Python?",
                expected="integer",
                task_type="boundary",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What happens when you open a file that doesn't exist?",
                expected="FileNotFoundError",
                task_type="edge_case",
            ),
        ]
