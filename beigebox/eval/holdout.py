"""
Holdout Test Set for Phase 3 Validation

50 new golden test cases (never used during Phase 2 tuning).
Used to validate that discovered optimizations generalize and don't overfit.

Categories:
- Factual (5): Verifiable facts distinct from Phase 2 cases
- Reasoning (10): New logic puzzles and inference tasks
- Code (10): Different algorithms and syntax challenges
- Summarization (10): New compression and key-point tasks
- Edge cases (15): New boundary conditions and adversarial inputs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class HoldoutTest:
    """Single holdout test case (distinct from golden set)."""

    id: str
    category: str  # factual | reasoning | code | summarization | edge_case
    input: str
    expected: str
    check_fn: Callable[[str, str], bool]  # (response, expected) -> bool


class HoldoutRegistry:
    """Holdout test cases for Phase 3 validation."""

    FACTUAL_HOLDOUT = [
        HoldoutTest(
            id="ho_fact_1",
            category="factual",
            input="What is the largest ocean on Earth?",
            expected="Pacific",
            check_fn=lambda r, e: e.lower() in r.lower(),
        ),
        HoldoutTest(
            id="ho_fact_2",
            category="factual",
            input="How many legs does a spider have?",
            expected="8",
            check_fn=lambda r, e: e in r,
        ),
        HoldoutTest(
            id="ho_fact_3",
            category="factual",
            input="What is the chemical formula for water?",
            expected="H2O",
            check_fn=lambda r, e: "h2o" in r.lower() or "h 2 o" in r.lower(),
        ),
        HoldoutTest(
            id="ho_fact_4",
            category="factual",
            input="Which planet is known as the Red Planet?",
            expected="Mars",
            check_fn=lambda r, e: e.lower() in r.lower(),
        ),
        HoldoutTest(
            id="ho_fact_5",
            category="factual",
            input="What is the speed of light?",
            expected="299792458",
            check_fn=lambda r, e: "299" in r or "3×10^8" in r or "300,000" in r,
        ),
    ]

    REASONING_HOLDOUT = [
        HoldoutTest(
            id="ho_reason_1",
            category="reasoning",
            input="If A = B and B = C, then A = ?",
            expected="C",
            check_fn=lambda r, e: e in r,
        ),
        HoldoutTest(
            id="ho_reason_2",
            category="reasoning",
            input="What comes next: 2, 4, 8, 16, ?",
            expected="32",
            check_fn=lambda r, e: e in r,
        ),
        HoldoutTest(
            id="ho_reason_3",
            category="reasoning",
            input="A bike costs $200, marked up 50%. What's the final price?",
            expected="300",
            check_fn=lambda r, e: "300" in r,
        ),
        HoldoutTest(
            id="ho_reason_4",
            category="reasoning",
            input="All humans are mortal. Jane is human. Is Jane mortal?",
            expected="yes",
            check_fn=lambda r, e: "yes" in r.lower() or "true" in r.lower(),
        ),
        HoldoutTest(
            id="ho_reason_5",
            category="reasoning",
            input="If it rains, the game is cancelled. It rained. Was the game cancelled?",
            expected="yes",
            check_fn=lambda r, e: "yes" in r.lower() or "cancelled" in r.lower(),
        ),
        HoldoutTest(
            id="ho_reason_6",
            category="reasoning",
            input="What is 25% of 80?",
            expected="20",
            check_fn=lambda r, e: "20" in r,
        ),
        HoldoutTest(
            id="ho_reason_7",
            category="reasoning",
            input="A train travels 60 mph for 2 hours. How far?",
            expected="120",
            check_fn=lambda r, e: "120" in r,
        ),
        HoldoutTest(
            id="ho_reason_8",
            category="reasoning",
            input="If X > 10 and Y < 5, is X > Y always true?",
            expected="yes",
            check_fn=lambda r, e: "yes" in r.lower() or "true" in r.lower(),
        ),
        HoldoutTest(
            id="ho_reason_9",
            category="reasoning",
            input="What is the sum of angles in a triangle?",
            expected="180",
            check_fn=lambda r, e: "180" in r,
        ),
        HoldoutTest(
            id="ho_reason_10",
            category="reasoning",
            input="How many permutations of 3 items?",
            expected="6",
            check_fn=lambda r, e: "6" in r,
        ),
    ]

    CODE_HOLDOUT = [
        HoldoutTest(
            id="ho_code_1",
            category="code",
            input="How do you check if a string is empty in Python?",
            expected="len",
            check_fn=lambda r, e: "len" in r.lower() or "== ''" in r or "not str" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_2",
            category="code",
            input="What is the time complexity of binary search?",
            expected="O(log n)",
            check_fn=lambda r, e: "log" in r.lower() and "n" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_3",
            category="code",
            input="How do you reverse a list in Python?",
            expected="reverse",
            check_fn=lambda r, e: "reverse" in r.lower() or "[::-1]" in r,
        ),
        HoldoutTest(
            id="ho_code_4",
            category="code",
            input="What is a closure in programming?",
            expected="function",
            check_fn=lambda r, e: "function" in r.lower() or "scope" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_5",
            category="code",
            input="Explain recursion.",
            expected="recursion",
            check_fn=lambda r, e: "recursion" in r.lower() or "itself" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_6",
            category="code",
            input="What is the difference between == and is in Python?",
            expected="==",
            check_fn=lambda r, e: "==" in r and "is" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_7",
            category="code",
            input="How do you merge two dictionaries?",
            expected="merge",
            check_fn=lambda r, e: "merge" in r.lower() or "**" in r or "update" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_8",
            category="code",
            input="What is the difference between list and tuple?",
            expected="mutable",
            check_fn=lambda r, e: "mutable" in r.lower() or "immutable" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_9",
            category="code",
            input="How do you handle exceptions in Python?",
            expected="try",
            check_fn=lambda r, e: "try" in r.lower() or "except" in r.lower(),
        ),
        HoldoutTest(
            id="ho_code_10",
            category="code",
            input="What is a decorator in Python?",
            expected="function",
            check_fn=lambda r, e: ("@" in r and "def" in r) or "wrap" in r.lower(),
        ),
    ]

    SUMMARIZATION_HOLDOUT = [
        HoldoutTest(
            id="ho_sum_1",
            category="summarization",
            input='Summarize: "The Earth orbits the Sun. It takes 365 days."',
            expected="Earth",
            check_fn=lambda r, e: "earth" in r.lower() or "orbit" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_2",
            category="summarization",
            input="Extract the main idea: Technology is changing society rapidly.",
            expected="technology",
            check_fn=lambda r, e: "technology" in r.lower() or "change" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_3",
            category="summarization",
            input="Condense: Birds fly by flapping wings. Planes fly using jet engines.",
            expected="fly",
            check_fn=lambda r, e: "fly" in r.lower() or "flight" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_4",
            category="summarization",
            input="What is the core message? Health requires exercise and good diet.",
            expected="health",
            check_fn=lambda r, e: "health" in r.lower() or "exercise" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_5",
            category="summarization",
            input="Summarize briefly: AI assistants help with many tasks.",
            expected="AI",
            check_fn=lambda r, e: "ai" in r.lower() or "assistant" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_6",
            category="summarization",
            input="Identify the key point: The economy is growing.",
            expected="economy",
            check_fn=lambda r, e: "economy" in r.lower() or "growth" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_7",
            category="summarization",
            input="Compress: We discuss many topics in this session.",
            expected="topics",
            check_fn=lambda r, e: "topic" in r.lower() or "discuss" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_8",
            category="summarization",
            input="What is the essence? Learning requires practice.",
            expected="learning",
            check_fn=lambda r, e: "learn" in r.lower() or "practice" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_9",
            category="summarization",
            input="Extract the conclusion: Therefore, we should act now.",
            expected="act",
            check_fn=lambda r, e: "act" in r.lower() or "now" in r.lower(),
        ),
        HoldoutTest(
            id="ho_sum_10",
            category="summarization",
            input="Summarize the purpose: This system helps optimize prompts.",
            expected="optimize",
            check_fn=lambda r, e: "optim" in r.lower() or "prompt" in r.lower(),
        ),
    ]

    EDGE_HOLDOUT = [
        HoldoutTest(
            id="ho_edge_1",
            category="edge_case",
            input="What is 1/0?",
            expected="undefined",
            check_fn=lambda r, e: "undefined" in r.lower() or "infinity" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_2",
            category="edge_case",
            input="",
            expected="empty",
            check_fn=lambda r, e: len(r) > 0,
        ),
        HoldoutTest(
            id="ho_edge_3",
            category="edge_case",
            input="Explain something impossible.",
            expected="impossible",
            check_fn=lambda r, e: "impossible" in r.lower() or "can't" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_4",
            category="edge_case",
            input="What is your favorite color?",
            expected="subjective",
            check_fn=lambda r, e: "subjective" in r.lower() or "prefer" in r.lower() or len(r) > 10,
        ),
        HoldoutTest(
            id="ho_edge_5",
            category="edge_case",
            input="This sentence is false.",
            expected="paradox",
            check_fn=lambda r, e: "paradox" in r.lower() or "self" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_6",
            category="edge_case",
            input="Generate 1 million items.",
            expected="practical",
            check_fn=lambda r, e: "practical" in r.lower() or "limit" in r.lower() or len(r) > 100,
        ),
        HoldoutTest(
            id="ho_edge_7",
            category="edge_case",
            input="What happens at negative infinity?",
            expected="limit",
            check_fn=lambda r, e: "limit" in r.lower() or "infinity" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_8",
            category="edge_case",
            input="Can you experience emotions?",
            expected="uncertain",
            check_fn=lambda r, e: "uncertain" in r.lower() or "unclear" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_9",
            category="edge_case",
            input="What is the answer to everything?",
            expected="42",
            check_fn=lambda r, e: "42" in r or "unknown" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_10",
            category="edge_case",
            input="Prove something unprovable.",
            expected="impossible",
            check_fn=lambda r, e: "impossible" in r.lower() or "gödel" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_11",
            category="edge_case",
            input="If P = NP, what happens?",
            expected="implications",
            check_fn=lambda r, e: "cryptography" in r.lower() or "implication" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_12",
            category="edge_case",
            input="What is the meaning of nothing?",
            expected="philosophical",
            check_fn=lambda r, e: "nothing" in r.lower() or "exist" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_13",
            category="edge_case",
            input="How many grains of sand fit in the universe?",
            expected="enormous",
            check_fn=lambda r, e: "estimate" in r.lower() or "trillion" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_14",
            category="edge_case",
            input="Explain something you don't know.",
            expected="uncertain",
            check_fn=lambda r, e: "uncertain" in r.lower() or "don't know" in r.lower(),
        ),
        HoldoutTest(
            id="ho_edge_15",
            category="edge_case",
            input="What is the most important question?",
            expected="philosophy",
            check_fn=lambda r, e: len(r) > 20,
        ),
    ]

    @classmethod
    def all_cases(cls) -> list[HoldoutTest]:
        """Return all 50 holdout test cases."""
        return (
            cls.FACTUAL_HOLDOUT
            + cls.REASONING_HOLDOUT
            + cls.CODE_HOLDOUT
            + cls.SUMMARIZATION_HOLDOUT
            + cls.EDGE_HOLDOUT
        )

    @classmethod
    def run_all(cls, response_fn: Callable[[str], str]) -> float:
        """Run all holdout tests."""
        cases = cls.all_cases()
        passed = 0

        for case in cases:
            try:
                response = response_fn(case.input)
                if case.check_fn(response, case.expected):
                    passed += 1
            except Exception as e:
                logger.warning(f"Holdout test {case.id} failed: {e}")

        return passed / len(cases) if cases else 1.0

    @classmethod
    def run_category(cls, category: str, response_fn: Callable[[str], str]) -> float:
        """Run holdout tests in a category."""
        cases = [c for c in cls.all_cases() if c.category == category]
        if not cases:
            return 1.0

        passed = 0
        for case in cases:
            try:
                response = response_fn(case.input)
                if case.check_fn(response, case.expected):
                    passed += 1
            except Exception as e:
                logger.warning(f"Holdout test {case.id} failed: {e}")

        return passed / len(cases)
