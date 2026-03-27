"""
Opportunity #3: Dialogue Relevance Threshold

Hypothesis: Filtering context turns by keyword overlap with the current question
improves relevance. Adaptive threshold (strict 3+ for focused tasks; loose 1+
for exploratory tasks) beats fixed threshold.

Expected impact: +2-5% precision (MEDIUM confidence)

Variants
--------
- baseline_all:     All context turns included (current)
- strict_overlap:   Only turns with 3+ keyword overlap with question
- loose_overlap:    Only turns with 1+ keyword overlap
- adaptive:         Strict for focused questions, loose for open-ended
"""
from __future__ import annotations

import re
from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "The database is PostgreSQL 15 with connection pooling via PgBouncer.",
    "Cache layer: Redis 7.2, 8GB allocated, TTL default 300 seconds.",
    "The frontend is React 18 with TypeScript. Bundle size: 420KB.",
    "API authentication uses JWT with 1-hour expiry and refresh tokens.",
    "Database connection pool size: 20 per instance. Max connections: 100.",
    "Cache hit rate: 34%. Miss penalty averages 45ms additional latency.",
    "Frontend build time: 2.3 minutes. Lighthouse score: 89.",
    "JWT secret rotated monthly. Refresh tokens expire after 30 days.",
]


def _keyword_overlap(question: str, content: str, min_len: int = 4) -> int:
    """Count significant word overlap between question and content."""
    q_words = set(w.lower() for w in re.findall(r'\b\w+\b', question) if len(w) >= min_len)
    c_words = set(w.lower() for w in re.findall(r'\b\w+\b', content) if len(w) >= min_len)
    return len(q_words & c_words)


def _is_open_ended(question: str) -> bool:
    open_words = {"summarize", "describe", "list", "tell", "explain", "overview", "all"}
    return any(w in question.lower() for w in open_words)


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class DialogueRelevanceExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "dialogue_relevance"
    OPPORTUNITY_NAME = "Dialogue Relevance Threshold (#3)"
    HYPOTHESIS = "Keyword-overlap filtering improves context relevance vs. all-inclusive context"
    EXPECTED_IMPACT = "+2-5% precision"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_all",     "threshold": None,  "adaptive": False},
        {"name": "strict_overlap",   "threshold": 3,     "adaptive": False},
        {"name": "loose_overlap",    "threshold": 1,     "adaptive": False},
        {"name": "adaptive",         "threshold": None,  "adaptive": True},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        threshold = variant_config.get("threshold")
        adaptive = variant_config.get("adaptive", False)
        system, context, question = _get_body_turns(messages)

        if (threshold is None and not adaptive) or not context:
            return messages

        q_text = question.get("content", "")

        if adaptive:
            threshold = 1 if _is_open_ended(q_text) else 3

        # Filter turns by overlap; always keep at least 2 turns
        relevant = [
            m for m in context
            if _keyword_overlap(q_text, m.get("content", "")) >= threshold
        ]
        if len(relevant) < 2:
            relevant = context[-2:]

        return system + relevant + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the database connection pool size?",
                expected="20",
                task_type="focused_db",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the cache TTL?",
                expected="300",
                task_type="focused_cache",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="How often is the JWT secret rotated?",
                expected="monthly",
                task_type="focused_auth",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the frontend bundle size?",
                expected="420KB",
                task_type="focused_frontend",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Summarize the caching and database configuration.",
                expected="Redis",
                task_type="open_ended",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="List all the latency-related facts.",
                expected="45ms",
                task_type="open_ended",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the cache hit rate and its miss penalty?",
                expected="34%",
                task_type="focused_cache",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Describe the authentication setup.",
                expected="JWT",
                task_type="open_ended",
            ),
        ]
