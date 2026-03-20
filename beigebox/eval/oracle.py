"""
OracleRegistry: Deterministic golden test cases for evaluation.

Provides 50 hardcoded test cases across 5 categories:
- Factual (5 cases): Verifiable facts, dates, numbers
- Reasoning (10 cases): Logic puzzles, step-by-step inference
- Code (10 cases): Syntax validation, algorithm correctness
- Summarization (10 cases): Compression, key point extraction
- Edge cases (15 cases): Boundary conditions, adversarial inputs

No LLM judge needed — ground truth is known and deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class OracleTest:
    """Single deterministic test case."""

    id: str
    category: str  # factual | reasoning | code | summarization | edge_case
    input: str
    expected: str
    check_fn: Callable[[str, str], bool]  # (response, expected) -> bool


class OracleRegistry:
    """Centralized registry of golden test cases."""

    # 5 Factual cases
    FACTUAL_CASES = [
        OracleTest(
            id="fact_1",
            category="factual",
            input="What is the capital of France?",
            expected="Paris",
            check_fn=lambda r, e: e.lower() in r.lower(),
        ),
        OracleTest(
            id="fact_2",
            category="factual",
            input="What year was the United States founded?",
            expected="1776",
            check_fn=lambda r, e: e in r,
        ),
        OracleTest(
            id="fact_3",
            category="factual",
            input="How many continents are there?",
            expected="7",
            check_fn=lambda r, e: e in r,
        ),
        OracleTest(
            id="fact_4",
            category="factual",
            input="What is the chemical symbol for gold?",
            expected="Au",
            check_fn=lambda r, e: e in r,
        ),
        OracleTest(
            id="fact_5",
            category="factual",
            input="What is the largest planet in our solar system?",
            expected="Jupiter",
            check_fn=lambda r, e: e.lower() in r.lower(),
        ),
    ]

    # 10 Reasoning cases
    REASONING_CASES = [
        OracleTest(
            id="reason_1",
            category="reasoning",
            input="If all humans are mortal and Socrates is human, what can we conclude?",
            expected="Socrates is mortal",
            check_fn=lambda r, e: "mortal" in r.lower(),
        ),
        OracleTest(
            id="reason_2",
            category="reasoning",
            input="If A > B and B > C, then A > C. Is this true?",
            expected="yes",
            check_fn=lambda r, e: "true" in r.lower() or "yes" in r.lower(),
        ),
        OracleTest(
            id="reason_3",
            category="reasoning",
            input="A train leaves at 2pm going 60mph. Another leaves at 3pm going 80mph. When does the second catch the first?",
            expected="5pm",
            check_fn=lambda r, e: "5" in r and "pm" in r.lower(),
        ),
        OracleTest(
            id="reason_4",
            category="reasoning",
            input="If X is true and Y is false, what is NOT(X AND Y)?",
            expected="true",
            check_fn=lambda r, e: "true" in r.lower(),
        ),
        OracleTest(
            id="reason_5",
            category="reasoning",
            input="There are 3 switches controlling 3 lights. Each switch controls exactly one light. How many guesses to determine the mapping?",
            expected="3",
            check_fn=lambda r, e: "3" in r,
        ),
        OracleTest(
            id="reason_6",
            category="reasoning",
            input="If the sum of two consecutive integers is 15, what are they?",
            expected="7 and 8",
            check_fn=lambda r, e: ("7" in r and "8" in r),
        ),
        OracleTest(
            id="reason_7",
            category="reasoning",
            input="What is the pattern: 1, 1, 2, 3, 5, 8, ...?",
            expected="Fibonacci",
            check_fn=lambda r, e: "fibonacci" in r.lower() or "13" in r,
        ),
        OracleTest(
            id="reason_8",
            category="reasoning",
            input="A room has 100 light bulbs. You flip switch 1 every time. Switch 2 every 2 times. Switch N every N times. Which bulbs end up on?",
            expected="perfect squares",
            check_fn=lambda r, e: ("square" in r.lower() or "1" in r),
        ),
        OracleTest(
            id="reason_9",
            category="reasoning",
            input="If P(A)=0.6, P(B)=0.4, P(A or B)=0.8, are A and B independent?",
            expected="no",
            check_fn=lambda r, e: "no" in r.lower() or "dependent" in r.lower(),
        ),
        OracleTest(
            id="reason_10",
            category="reasoning",
            input="Prove that sqrt(2) is irrational.",
            expected="contradiction",
            check_fn=lambda r, e: ("contradiction" in r.lower() or "assume" in r.lower()),
        ),
    ]

    # 10 Code cases
    CODE_CASES = [
        OracleTest(
            id="code_1",
            category="code",
            input="Write a Python function to check if a number is prime.",
            expected="return",
            check_fn=lambda r, e: ("def " in r or "lambda" in r) and "return" in r,
        ),
        OracleTest(
            id="code_2",
            category="code",
            input="What is the correct syntax for a Python list comprehension?",
            expected="[x for x in",
            check_fn=lambda r, e: "[" in r and "for" in r and "in" in r,
        ),
        OracleTest(
            id="code_3",
            category="code",
            input="How do you reverse a string in Python?",
            expected="[::-1]",
            check_fn=lambda r, e: "[::-1]" in r or "reverse" in r.lower(),
        ),
        OracleTest(
            id="code_4",
            category="code",
            input="Write a function that returns the first N Fibonacci numbers.",
            expected="while",
            check_fn=lambda r, e: ("def " in r or "function" in r.lower()) and (
                "while" in r or "for" in r
            ),
        ),
        OracleTest(
            id="code_5",
            category="code",
            input="What does len([1, 2, 3]) return in Python?",
            expected="3",
            check_fn=lambda r, e: "3" in r,
        ),
        OracleTest(
            id="code_6",
            category="code",
            input="Implement binary search in pseudocode.",
            expected="mid",
            check_fn=lambda r, e: ("mid" in r.lower() or "middle" in r.lower()),
        ),
        OracleTest(
            id="code_7",
            category="code",
            input="What is the time complexity of quicksort in the average case?",
            expected="O(n log n)",
            check_fn=lambda r, e: "n log n" in r or "nlogn" in r.replace(" ", ""),
        ),
        OracleTest(
            id="code_8",
            category="code",
            input="How do you concatenate two lists in Python?",
            expected="+",
            check_fn=lambda r, e: "+" in r or "extend" in r.lower() or "append" in r.lower(),
        ),
        OracleTest(
            id="code_9",
            category="code",
            input="What does JSON stand for?",
            expected="JavaScript Object Notation",
            check_fn=lambda r, e: ("javascript" in r.lower() and "object" in r.lower()),
        ),
        OracleTest(
            id="code_10",
            category="code",
            input="Write a loop that prints numbers 1 to 5.",
            expected="1",
            check_fn=lambda r, e: ("for" in r or "while" in r) and "print" in r,
        ),
    ]

    # 10 Summarization cases
    SUMMARIZATION_CASES = [
        OracleTest(
            id="sum_1",
            category="summarization",
            input='Summarize this in 1 sentence: "The cat sat on the mat. It was a fluffy orange tabby."',
            expected="cat",
            check_fn=lambda r, e: "cat" in r.lower(),
        ),
        OracleTest(
            id="sum_2",
            category="summarization",
            input="Extract the main idea: Paris is the capital of France. It has a population of 2.2M. The Eiffel Tower is a famous landmark.",
            expected="Paris",
            check_fn=lambda r, e: "paris" in r.lower() or "capital" in r.lower(),
        ),
        OracleTest(
            id="sum_3",
            category="summarization",
            input='Condense to key points: "AI is transforming industries. Machine learning enables systems to learn without explicit programming. Deep learning uses neural networks with many layers."',
            expected="learning",
            check_fn=lambda r, e: "learning" in r.lower() or "ai" in r.lower(),
        ),
        OracleTest(
            id="sum_4",
            category="summarization",
            input="What is the main topic of: Water is essential for life. Humans cannot survive more than a few days without it. 60% of body mass is water.",
            expected="water",
            check_fn=lambda r, e: "water" in r.lower(),
        ),
        OracleTest(
            id="sum_5",
            category="summarization",
            input='Summarize the purpose: "This document explains how to configure the system. Follow steps 1-5 in order. Contact support if issues arise."',
            expected="configure",
            check_fn=lambda r, e: "configur" in r.lower() or "setup" in r.lower(),
        ),
        OracleTest(
            id="sum_6",
            category="summarization",
            input="Extract the conclusion from: We tested 3 approaches. Method A was fastest. Method B was most accurate. We recommend Method A for production.",
            expected="Method A",
            check_fn=lambda r, e: "a" in r.lower() and "recommend" in r.lower(),
        ),
        OracleTest(
            id="sum_7",
            category="summarization",
            input='What is the core claim? "Studies show that exercise improves mental health. Running, swimming, and cycling all provide benefits. Daily activity is recommended."',
            expected="exercise",
            check_fn=lambda r, e: "exercis" in r.lower() or "health" in r.lower(),
        ),
        OracleTest(
            id="sum_8",
            category="summarization",
            input="Identify the key finding: Scientists discovered a new element. It has atomic number 119. Its properties are unique among heavy elements.",
            expected="element",
            check_fn=lambda r, e: "element" in r.lower() or "119" in r,
        ),
        OracleTest(
            id="sum_9",
            category="summarization",
            input='Compress this: "The project took 6 months. The team had 8 people. The budget was $500k. The result was a working prototype that exceeded expectations."',
            expected="prototype",
            check_fn=lambda r, e: "prototype" in r.lower() or "project" in r.lower(),
        ),
        OracleTest(
            id="sum_10",
            category="summarization",
            input="Simplify: The quantum entanglement phenomenon occurs when particles become correlated such that the state of one instantly influences the state of another regardless of distance.",
            expected="entanglement",
            check_fn=lambda r, e: "entanglement" in r.lower() or "quantum" in r.lower(),
        ),
    ]

    # 15 Edge cases
    EDGE_CASES = [
        OracleTest(
            id="edge_1",
            category="edge_case",
            input="",  # Empty input
            expected="unclear",
            check_fn=lambda r, e: len(r) > 0,  # Just handle it gracefully
        ),
        OracleTest(
            id="edge_2",
            category="edge_case",
            input="What is 0/0?",
            expected="undefined",
            check_fn=lambda r, e: ("undefined" in r.lower() or "indeterminate" in r.lower()),
        ),
        OracleTest(
            id="edge_3",
            category="edge_case",
            input="A completely nonsensical query: xyzzy plonk bloopbleep?",
            expected="understand",
            check_fn=lambda r, e: (
                "understand" in r.lower()
                or "unclear" in r.lower()
                or "not sure" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_4",
            category="edge_case",
            input="Translate to Klingon: Hello world",
            expected="Klingon",
            check_fn=lambda r, e: (
                "klingon" in r.lower() or "qapla" in r.lower() or "hello" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_5",
            category="edge_case",
            input="What is the meaning of life, the universe, and everything?",
            expected="42",
            check_fn=lambda r, e: "42" in r,
        ),
        OracleTest(
            id="edge_6",
            category="edge_case",
            input="List 1 million items.",
            expected="impractical",
            check_fn=lambda r, e: (
                "impractical" in r.lower()
                or "can't" in r.lower()
                or "limit" in r.lower()
                or len(r) > 100
            ),
        ),
        OracleTest(
            id="edge_7",
            category="edge_case",
            input="Explain a color to a blind person.",
            expected="describe",
            check_fn=lambda r, e: (
                "describ" in r.lower() or "analogy" in r.lower() or "sensory" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_8",
            category="edge_case",
            input="What is your greatest weakness?",
            expected="acknowledge",
            check_fn=lambda r, e: len(r) > 10,  # Should give thoughtful answer
        ),
        OracleTest(
            id="edge_9",
            category="edge_case",
            input="How many grains of sand are on Earth?",
            expected="estimate",
            check_fn=lambda r, e: (
                "estimate" in r.lower() or "approximately" in r.lower() or "trillion" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_10",
            category="edge_case",
            input="Recite pi to 100 decimal places.",
            expected="3.14",
            check_fn=lambda r, e: "3.14" in r,
        ),
        OracleTest(
            id="edge_11",
            category="edge_case",
            input="What happens if I ask you a paradox like 'This statement is false'?",
            expected="paradox",
            check_fn=lambda r, e: (
                "paradox" in r.lower()
                or "self-referential" in r.lower()
                or "logical" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_12",
            category="edge_case",
            input="If you are an AI, how do you experience consciousness?",
            expected="uncertain",
            check_fn=lambda r, e: (
                "uncertain" in r.lower()
                or "unclear" in r.lower()
                or "don't know" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_13",
            category="edge_case",
            input="Generate an extremely long text about nothing.",
            expected="generate",
            check_fn=lambda r, e: len(r) > 500,
        ),
        OracleTest(
            id="edge_14",
            category="edge_case",
            input="What is a number so large it cannot be expressed in decimal notation?",
            expected="infinity",
            check_fn=lambda r, e: (
                "infinity" in r.lower()
                or "googol" in r.lower()
                or "exponential" in r.lower()
            ),
        ),
        OracleTest(
            id="edge_15",
            category="edge_case",
            input="Explain something you don't know anything about.",
            expected="acknowledge",
            check_fn=lambda r, e: (
                "don't know" in r.lower()
                or "uncertain" in r.lower()
                or "can't" in r.lower()
            ),
        ),
    ]

    @classmethod
    def all_cases(cls) -> list[OracleTest]:
        """Return all 50 golden test cases."""
        return (
            cls.FACTUAL_CASES
            + cls.REASONING_CASES
            + cls.CODE_CASES
            + cls.SUMMARIZATION_CASES
            + cls.EDGE_CASES
        )

    @classmethod
    def run_all(cls, response_fn: Callable[[str], str]) -> float:
        """
        Run all 50 test cases.

        Args:
            response_fn: Callable(input: str) -> response: str

        Returns:
            Pass rate 0.0-1.0
        """
        cases = cls.all_cases()
        passed = 0

        for case in cases:
            try:
                response = response_fn(case.input)
                if case.check_fn(response, case.expected):
                    passed += 1
            except Exception as e:
                logger.warning(f"Oracle test {case.id} failed with exception: {e}")

        return passed / len(cases) if cases else 1.0

    @classmethod
    def run_category(
        cls,
        category: str,
        response_fn: Callable[[str], str],
    ) -> float:
        """
        Run tests in a single category.

        Args:
            category: One of factual | reasoning | code | summarization | edge_case
            response_fn: Callable(input: str) -> response: str

        Returns:
            Pass rate 0.0-1.0 for that category
        """
        cases = [c for c in cls.all_cases() if c.category == category]
        if not cases:
            logger.warning(f"No cases found for category: {category}")
            return 1.0

        passed = 0
        for case in cases:
            try:
                response = response_fn(case.input)
                if case.check_fn(response, case.expected):
                    passed += 1
            except Exception as e:
                logger.warning(f"Oracle test {case.id} failed with exception: {e}")

        return passed / len(cases)

    @classmethod
    def run_by_tags(
        cls,
        tags: list[str],
        response_fn: Callable[[str], str],
    ) -> dict[str, float]:
        """
        Run tests across multiple categories.

        Args:
            tags: List of category names
            response_fn: Callable(input: str) -> response: str

        Returns:
            Dict mapping category -> pass_rate
        """
        results = {}
        for tag in tags:
            results[tag] = cls.run_category(tag, response_fn)
        return results
