"""
Opportunity #9: Prompt Caching Integration

Hypothesis: Caching repeated context prefixes across agent turns within a session
reduces latency and backend cost without sacrificing accuracy.

Expected impact: -60% latency, -30% cost (HIGH confidence, infrastructure-level)

Note: This opportunity tests the EFFECT of context repetition on LLM behaviour,
not actual cache infrastructure.  The transform simulates what a cache would do:
- Baseline: repeat full context on every turn (no cache)
- Session cache: deduplicate identical prefix turns
- Prefix only: send only non-duplicate suffix
- Minimal: send only the question with a one-line summary of prior context

Real latency gains require Anthropic prompt caching / prefix KV-cache in vLLM.
Measure this experiment for accuracy preservation; latency measurement
requires live infrastructure timing (see `_latency_ms` in scorecards).
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "User profile: Alice Chen, Senior Engineer, timezone UTC+8.",
    "Project: Phoenix API gateway, Python FastAPI, deployed on AWS.",
    "Constraints: API rate limit 500 rpm. Budget $120k. Deadline March 31.",
    "Current issue: P95 latency is 320ms, target <200ms.",
    "Prior decision: We chose abstractive compression over extractive (session 3).",
]


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class PromptCachingExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "prompt_caching"
    OPPORTUNITY_NAME = "Prompt Caching Integration (#9)"
    HYPOTHESIS = "Deduplicating repeated context prefixes preserves accuracy while reducing tokens"
    EXPECTED_IMPACT = "-60% latency (with KV cache), same accuracy"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_full_repeat",  "cache_strategy": "none"},
        {"name": "session_dedup",         "cache_strategy": "session_dedup"},
        {"name": "prefix_only",           "cache_strategy": "prefix_suffix"},
        {"name": "minimal_summary",       "cache_strategy": "minimal"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("cache_strategy", "none")
        system, context, question = _get_body_turns(messages)

        if strategy == "none" or not context:
            return messages

        if strategy == "session_dedup":
            # Remove duplicate content turns (simulate cache dedup)
            seen = set()
            deduped = []
            for msg in context:
                key = msg.get("content", "")[:80]  # fingerprint on first 80 chars
                if key not in seen:
                    seen.add(key)
                    deduped.append(msg)
            return system + deduped + [question]

        if strategy == "prefix_suffix":
            # Keep only the last 2 context turns (suffix) — simulates sending only the delta
            suffix = context[-4:] if len(context) >= 4 else context
            return system + suffix + [question]

        if strategy == "minimal":
            # Collapse all context into one compact summary line in system prompt
            facts_text = " | ".join(
                m["content"].replace("Remember this: ", "").replace("Noted: ", "")
                for m in context if m["role"] == "user"
            )[:300]
            cached_system = dict(system[0]) if system else {"role": "system", "content": ""}
            cached_system["content"] += f"\n\nCached context: {facts_text}"
            return [cached_system] + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the API rate limit?",
                expected="500",
                task_type="cached_fact_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What decision did we make about compression in session 3?",
                expected="abstractive",
                task_type="prior_decision_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Who is the user we are working with?",
                expected="Alice",
                task_type="profile_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the latency target?",
                expected="200ms",
                task_type="constraint_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the project deadline?",
                expected="March 31",
                task_type="constraint_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Summarize the key constraints for this project.",
                expected="rate limit",
                task_type="synthesis",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What cloud provider are we using?",
                expected="AWS",
                task_type="cached_fact_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the current P95 latency and how far are we from target?",
                expected="320ms",
                task_type="delta_calculation",
            ),
        ]


# Keep old dataclass name for backwards-compat with existing imports
class PromptCachingVariant:
    """Legacy shim — use PromptCachingExperiment.VARIANTS instead."""
    pass
