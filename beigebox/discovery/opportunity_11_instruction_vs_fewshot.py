"""
Opportunity #11: Instruction Evolution (Instruction vs. Few-Shot Trade-off)

Hypothesis: Analytical/code tasks are better served by explicit instructions;
creative/open-ended tasks are better served by few-shot examples.
An adaptive classifier selects the right mode per request.

Expected impact: +5-10% per task type (MEDIUM confidence)

Variants
--------
- baseline_examples:     Always use few-shot examples (current implicit behavior)
- instructions_only:     Explicit step-by-step instructions, no examples
- adaptive_classifier:   Classify task type → pick instruction or example mode
- hybrid:                Short instruction + 1 example
"""
from __future__ import annotations

import re
from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_ANALYTICAL_INSTRUCTIONS = """\
Approach this analytically:
1. Break the problem into components.
2. Apply systematic reasoning to each component.
3. Combine findings into a clear conclusion.
4. Show your work if calculations are involved."""

_CODE_INSTRUCTIONS = """\
For code questions:
1. Identify the language and paradigm.
2. Provide the minimal correct implementation.
3. Note edge cases or error conditions.
4. Keep it concise — no boilerplate."""

_CREATIVE_EXAMPLES = """\
Example 1:
Q: Write a haiku about debugging.
A: Stack trace haunts me / Null pointer in the dark / Coffee, then the fix.

Example 2:
Q: Explain recursion like I'm five.
A: Imagine you're looking for your toy. You ask your friend. Your friend asks their friend. Until someone finds it."""

_ANALYTICAL_EXAMPLE = """\
Example:
Q: If x+y=10 and x-y=4, what is x?
A: Add the equations: 2x=14, so x=7."""


def _classify_task(question: str) -> str:
    """Rough task classifier: analytical | code | creative | factual."""
    q = question.lower()
    if any(w in q for w in ("write", "create", "imagine", "story", "poem", "haiku", "explain like")):
        return "creative"
    if any(w in q for w in ("def ", "function", "python", "code", "implement", "algorithm", "syntax")):
        return "code"
    if any(w in q for w in ("analyze", "calculate", "prove", "derive", "if x", "what is x", "solve")):
        return "analytical"
    return "factual"


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class InstructionVsFewShotExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "instruction_vs_fewshot"
    OPPORTUNITY_NAME = "Instruction vs. Few-Shot Trade-off (#11)"
    HYPOTHESIS = "Adaptive instruction/example selection improves accuracy per task type"
    EXPECTED_IMPACT = "+5-10% per task type"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_examples",       "mode": "examples"},
        {"name": "instructions_only",       "mode": "instructions"},
        {"name": "adaptive_classifier",     "mode": "adaptive"},
        {"name": "hybrid",                  "mode": "hybrid"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        mode = variant_config.get("mode", "examples")
        system, context, question = _get_body_turns(messages)

        q_text = question.get("content", "")

        if mode == "examples":
            suffix = _CREATIVE_EXAMPLES
        elif mode == "instructions":
            task = _classify_task(q_text)
            suffix = _CODE_INSTRUCTIONS if task == "code" else _ANALYTICAL_INSTRUCTIONS
        elif mode == "adaptive":
            task = _classify_task(q_text)
            if task in ("creative",):
                suffix = _CREATIVE_EXAMPLES
            elif task == "code":
                suffix = _CODE_INSTRUCTIONS
            else:
                suffix = _ANALYTICAL_INSTRUCTIONS
        elif mode == "hybrid":
            task = _classify_task(q_text)
            suffix = _ANALYTICAL_INSTRUCTIONS + "\n\n" + _ANALYTICAL_EXAMPLE
        else:
            return messages

        if system:
            new_sys = dict(system[0])
            new_sys["content"] = system[0]["content"] + f"\n\n{suffix}"
            return [new_sys] + context + [question]
        return [{"role": "system", "content": suffix}] + context + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # Analytical tasks — instruction mode should win
            DiscoveryTestCase(
                context_facts=[],
                question="If 3x + 2 = 14, what is x?",
                expected="4",
                task_type="analytical",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="Prove that the sum of first N natural numbers is N(N+1)/2.",
                expected="induction",
                task_type="analytical",
            ),
            # Code tasks — instruction mode should win
            DiscoveryTestCase(
                context_facts=[],
                question="Write a Python function to check if a string is a palindrome.",
                expected="def ",
                task_type="code",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is the time complexity of binary search?",
                expected="O(log n)",
                task_type="code",
            ),
            # Creative tasks — example mode should win
            DiscoveryTestCase(
                context_facts=[],
                question="Explain recursion using a real-world analogy.",
                expected="example",
                task_type="creative",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="Write a haiku about machine learning.",
                expected="syllable",
                task_type="creative",
            ),
            # Factual — both modes should work equally
            DiscoveryTestCase(
                context_facts=[],
                question="What does HTTP stand for?",
                expected="HyperText Transfer Protocol",
                task_type="factual",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is the default port for HTTPS?",
                expected="443",
                task_type="factual",
            ),
        ]
