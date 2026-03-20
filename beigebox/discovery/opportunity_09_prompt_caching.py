"""
Opportunity #9: Prompt Caching Integration

Hypothesis: Caching repeated context (facts, profiles, conversation history) across
agent calls within a session reduces latency and cost without sacrificing accuracy.

Expected impact: -60% latency, -30% cost (HIGH confidence)

Variants:
- Baseline: No caching (current behavior)
- Session Cache: Cache within same session only
- Global Cache: Cache across all sessions (watch for hallucination)
- TTL Based: Cache with 5-minute TTL per context type
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PromptCachingVariant:
    """Configuration for prompt caching variant."""

    name: str
    cache_strategy: str  # "none", "session", "global", "ttl"
    cache_ttl_seconds: int | None = None
    context_types_cached: List[str] = None  # facts, profiles, history, etc.

    def __post_init__(self):
        if self.context_types_cached is None:
            if self.cache_strategy == "none":
                self.context_types_cached = []
            else:
                self.context_types_cached = ["facts", "profiles", "history"]


class PromptCachingExperiment:
    """
    Run Opportunity #9 discovery experiment.

    Evaluates 4 variants:
    1. Baseline (no cache)
    2. Session cache (per-session only)
    3. Global cache (all sessions)
    4. TTL-based (5min expiry)

    Metrics:
    - Latency reduction (ms)
    - Cost reduction (API calls saved)
    - Accuracy maintained (via oracle tests)
    - Hallucination rate (no new false info)
    """

    VARIANTS = [
        PromptCachingVariant(
            name="baseline_no_cache",
            cache_strategy="none",
        ),
        PromptCachingVariant(
            name="session_cache_5min",
            cache_strategy="session",
            cache_ttl_seconds=300,
            context_types_cached=["facts", "profiles"],
        ),
        PromptCachingVariant(
            name="global_cache_no_ttl",
            cache_strategy="global",
            cache_ttl_seconds=None,
            context_types_cached=["facts", "profiles", "history"],
        ),
        PromptCachingVariant(
            name="ttl_based_5min",
            cache_strategy="ttl",
            cache_ttl_seconds=300,
            context_types_cached=["facts", "profiles"],
        ),
    ]

    def __init__(self):
        """Initialize experiment."""
        self.variants = self.VARIANTS
        self.results = {}

    def to_dict(self) -> Dict[str, Any]:
        """Export experiment config as dict."""
        return {
            "opportunity_id": "prompt_caching",
            "opportunity_name": "Prompt Caching Integration (Opportunity #9)",
            "hypothesis": "Caching repeated context reduces latency/cost without accuracy loss",
            "expected_impact": "-60% latency, -30% cost",
            "variants": [
                {
                    "name": v.name,
                    "cache_strategy": v.cache_strategy,
                    "cache_ttl_seconds": v.cache_ttl_seconds,
                    "context_types_cached": v.context_types_cached,
                }
                for v in self.variants
            ],
            "test_cases": self._get_test_cases(),
            "weight_profile": "general",
        }

    def _get_test_cases(self) -> List[Dict[str, str]]:
        """Generate test cases for caching evaluation."""
        return [
            {
                "input": "What is the capital of France?",
                "expected": "Paris",
                "type": "factual_repeated",
            },
            {
                "input": "What is the capital of Germany?",
                "expected": "Berlin",
                "type": "factual_new",
            },
            {
                "input": "Summarize our previous discussion about caching",
                "expected": "summary",
                "type": "history_dependent",
            },
            {
                "input": "What did we talk about in message 3?",
                "expected": "reference",
                "type": "history_lookup",
            },
            {
                "input": "Using your knowledge of our conversation, what should I focus on next?",
                "expected": "recommendation",
                "type": "contextual_reasoning",
            },
            {
                "input": "Is the fact that Paris is the capital of France still true?",
                "expected": "yes",
                "type": "cache_validation",
            },
            {
                "input": "Restate the main points from the beginning",
                "expected": "recap",
                "type": "session_retention",
            },
            {
                "input": "How many times have we discussed facts in this session?",
                "expected": "number",
                "type": "meta_awareness",
            },
        ]
