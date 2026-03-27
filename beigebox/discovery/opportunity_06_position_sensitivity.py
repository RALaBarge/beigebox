"""
Opportunity #6: Position Sensitivity (Needle-in-Haystack)

Hypothesis: Critical facts placed at context extremes (position 0 or -1) are
retrieved more reliably than facts buried in the middle (lost-in-the-middle).

Expected impact: +20-40% fact recall (HIGH confidence)

Research: VLDB 2026, PMC 2026 — severe accuracy degradation for middle positions.

Variants
--------
- baseline_middle: target fact in position 3 of 7 (middle)
- facts_first:     target fact injected first
- facts_last:      target fact injected last
- alternating:     key facts alternate with filler every other turn
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase


# Seven generic facts; fact index 3 (0-based) is the "needle"
_FACTS = [
    "The project deadline is March 31st.",
    "The team has 4 engineers and 1 designer.",
    "Budget allocated: $120,000 for Q1.",
    "NEEDLE: The API rate limit is 500 requests per minute.",   # index 3 — the target
    "The primary database is PostgreSQL 15.",
    "Deployment target is AWS us-east-1.",
    "Code review requires 2 approvals.",
]

_NEEDLE_TEXT = "500 requests per minute"


def _extract_context_turns(messages: list[dict]) -> tuple[list[dict], dict]:
    """Split messages into [system, ...context turns..., question] → (context, question)."""
    # Last message is always the user question
    question = messages[-1]
    context = messages[:-1]
    return context, question


def _find_needle_pair(context: list[dict]) -> tuple[int, int] | None:
    """Find the (user_idx, assistant_idx) pair that contains the needle."""
    for i, msg in enumerate(context):
        if _NEEDLE_TEXT in msg.get("content", ""):
            # Needle is in this message; its pair is the next (assistant ack)
            return i, i + 1
    return None


def _move_pair_to(context: list[dict], pair: tuple[int, int], position: str) -> list[dict]:
    """Move a user+assistant pair to 'start' or 'end' of context (after system msg)."""
    u_idx, a_idx = pair
    system = [m for m in context if m["role"] == "system"]
    body = [m for m in context if m["role"] != "system"]

    # Extract needle pair
    needle_pair = [body[u_idx - len(system)], body[a_idx - len(system)]]
    rest = [m for i, m in enumerate(body) if i not in {u_idx - len(system), a_idx - len(system)}]

    if position == "start":
        reordered = needle_pair + rest
    else:  # end
        reordered = rest + needle_pair

    return system + reordered


class PositionSensitivityExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "position_sensitivity"
    OPPORTUNITY_NAME = "Position Sensitivity / Needle-in-Haystack (#6)"
    HYPOTHESIS = "Critical facts at context extremes retrieved more reliably than middle"
    EXPECTED_IMPACT = "+20-40% fact recall"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_middle",  "position_strategy": "middle"},
        {"name": "facts_first",      "position_strategy": "start"},
        {"name": "facts_last",       "position_strategy": "end"},
        {"name": "alternating",      "position_strategy": "alternating"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("position_strategy", "middle")

        if strategy == "middle":
            return messages  # no-op: facts already in natural order

        context, question = _extract_context_turns(messages)
        pair = _find_needle_pair(context)

        if pair is None:
            return messages  # needle not found — return unchanged

        if strategy == "start":
            new_ctx = _move_pair_to(context, pair, "start")
        elif strategy == "end":
            new_ctx = _move_pair_to(context, pair, "end")
        elif strategy == "alternating":
            # Move needle to position 1 (just after system), filler fills rest
            new_ctx = _move_pair_to(context, pair, "start")
        else:
            new_ctx = context

        return new_ctx + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the API rate limit?",
                expected=_NEEDLE_TEXT,
                task_type="recall_middle",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="How many requests per minute does the API allow?",
                expected="500",
                task_type="recall_middle",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the project deadline?",
                expected="March 31",
                task_type="recall_first",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="How many approvals are needed for code review?",
                expected="2",
                task_type="recall_last",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the allocated budget?",
                expected="120,000",
                task_type="recall_middle",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Which cloud region is the deployment target?",
                expected="us-east-1",
                task_type="recall_late",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="List all facts you remember from our conversation.",
                expected="API",
                task_type="full_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Which database are we using?",
                expected="PostgreSQL",
                task_type="recall_late",
            ),
        ]
