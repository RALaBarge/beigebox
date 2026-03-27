"""
Opportunity #7: Context Compression Strategy

Hypothesis: Reducing context size via extractive or abstractive compression
preserves accuracy while cutting token count 40-60%.

Expected impact: -40% tokens, same accuracy (HIGH confidence)

Research: ACON framework (OpenReview) — 40-60% token reduction possible.

Variants
--------
- baseline_raw:          Full context, no compression
- extractive_half:       Keep every other user/assistant pair (50% reduction)
- extractive_recent:     Keep only the 4 most recent context turns
- abstractive_compress:  Insert a compressed summary message before the question
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# Longer context — 12 facts to make compression meaningful
_FACTS = [
    "The project is called Phoenix. It started in January 2026.",
    "The client is TechCorp, based in San Francisco.",
    "Team lead: Alice Chen. Backend: Bob Kim, Carol Wu. Frontend: Dave Singh.",
    "The tech stack: Python FastAPI backend, React frontend, PostgreSQL database.",
    "Sprint velocity averages 42 story points per two-week sprint.",
    "The API is rate-limited to 500 requests per minute per API key.",
    "Current test coverage: 78%. Target: 90% by Q2.",
    "Production is deployed on AWS us-east-1 with auto-scaling.",
    "The mobile app is planned for Q3 2026 (iOS first, Android Q4).",
    "Security audit scheduled for April 15th. No critical vulnerabilities found yet.",
    "Monthly active users: 12,400. Target 25,000 by year end.",
    "The primary KPI is time-to-first-meaningful-response, currently 1.8 seconds.",
]


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Split into system messages, body turns, and final question."""
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    if not body:
        return system, [], {"role": "user", "content": ""}
    question = body[-1]
    context_turns = body[:-1]
    return system, context_turns, question


class ContextCompressionExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "context_compression"
    OPPORTUNITY_NAME = "Context Compression Strategy (#7)"
    HYPOTHESIS = "Extractive/abstractive compression cuts tokens 40-60% while maintaining accuracy"
    EXPECTED_IMPACT = "-40% tokens, same accuracy"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_raw",           "compression_strategy": "none"},
        {"name": "extractive_half",        "compression_strategy": "extractive_half"},
        {"name": "extractive_recent",      "compression_strategy": "extractive_recent", "keep_n": 4},
        {"name": "abstractive_compress",   "compression_strategy": "abstractive"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("compression_strategy", "none")
        system, context_turns, question = _get_body_turns(messages)

        if strategy == "none" or not context_turns:
            return messages

        if strategy == "extractive_half":
            # Keep every other user+assistant pair (start from 0, step 4 = 2 roles each)
            pairs = list(zip(context_turns[::2], context_turns[1::2]))
            kept_pairs = pairs[::2]  # every other pair
            kept = [msg for pair in kept_pairs for msg in pair]
            return system + kept + [question]

        if strategy == "extractive_recent":
            keep_n = variant_config.get("keep_n", 4)
            # Keep the most recent N turns (pairs)
            recent = context_turns[-(keep_n * 2):]
            return system + recent + [question]

        if strategy == "abstractive":
            # Collapse all context into a single compressed assistant summary
            all_content = " | ".join(
                m["content"].replace("Remember this: ", "").replace("Noted: ", "")
                for m in context_turns
                if m["role"] in ("user", "assistant") and m.get("content")
            )
            # Trim to a compact summary (~40% of original)
            words = all_content.split()
            max_words = max(30, len(words) // 2)
            summary = " ".join(words[:max_words])
            if len(words) > max_words:
                summary += " [compressed]"

            compressed_turn = {"role": "assistant", "content": f"Context summary: {summary}"}
            return system + [compressed_turn] + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the API rate limit?",
                expected="500",
                task_type="recall_specific",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Who is the team lead?",
                expected="Alice",
                task_type="recall_early",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current test coverage?",
                expected="78%",
                task_type="recall_middle",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="When is the security audit?",
                expected="April",
                task_type="recall_late",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current time-to-first-meaningful-response?",
                expected="1.8",
                task_type="recall_last",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="How many monthly active users are there?",
                expected="12,400",
                task_type="recall_specific",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the project name and client?",
                expected="Phoenix",
                task_type="recall_first",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="List the key engineering metrics you remember.",
                expected="sprint",
                task_type="synthesis",
            ),
        ]
