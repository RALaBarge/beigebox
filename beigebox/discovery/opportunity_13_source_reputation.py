"""
Opportunity #13: Source/Domain Reputation Weighting

Hypothesis: Weighting retrieved facts by source credibility (Wikipedia/StackOverflow=1.0x,
unknown blog=0.5x) improves decision correctness by +5-12%.

Variants
--------
- baseline_equal:     All sources treated equally (current)
- credibility_first:  High-credibility sources moved to context front
- credibility_filter: Only include sources above credibility threshold
- annotated:          Keep all, annotate credibility score for LLM to use
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "[source=stackoverflow credibility=1.0] Python's GIL prevents true multi-threading for CPU-bound tasks.",
    "[source=unknown_blog credibility=0.3] You can bypass the GIL with a simple monkey-patch trick.",
    "[source=python_docs credibility=1.0] Use multiprocessing for CPU-bound parallelism; threading for I/O.",
    "[source=reddit credibility=0.4] Someone claimed asyncio is always faster than threading.",
    "[source=arxiv credibility=0.9] Study: async I/O reduces latency by 30-60% vs blocking I/O at >100 concurrent connections.",
    "[source=unknown_blog credibility=0.2] Just use threads for everything, the GIL doesn't matter.",
    "[source=python_docs credibility=1.0] asyncio is single-threaded; use ThreadPoolExecutor for blocking calls.",
    "[source=stackoverflow credibility=1.0] concurrent.futures is the recommended high-level API for both threading and multiprocessing.",
]

_HIGH_CRED_THRESHOLD = 0.7


def _parse_source_meta(content: str) -> dict:
    import re
    meta = {}
    m = re.search(r'\[source=(\S+)\s+credibility=([\d.]+)\]', content)
    if m:
        meta["source"] = m.group(1)
        meta["credibility"] = float(m.group(2))
    return meta


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class SourceReputationExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "source_reputation"
    OPPORTUNITY_NAME = "Source/Domain Reputation Weighting (#13)"
    HYPOTHESIS = "Credibility-weighted context improves accuracy by surfacing reliable sources"
    EXPECTED_IMPACT = "+5-12% decision correctness"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_equal",       "reputation_strategy": "equal"},
        {"name": "credibility_first",    "reputation_strategy": "sort_desc"},
        {"name": "credibility_filter",   "reputation_strategy": "filter", "min_credibility": _HIGH_CRED_THRESHOLD},
        {"name": "annotated",            "reputation_strategy": "annotate"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("reputation_strategy", "equal")
        system, context, question = _get_body_turns(messages)

        if strategy == "equal" or not context:
            return messages

        if strategy == "sort_desc":
            # Sort by credibility descending — high-cred sources at top
            sorted_ctx = sorted(
                context,
                key=lambda m: _parse_source_meta(m.get("content", "")).get("credibility", 0.5),
                reverse=True,
            )
            return system + sorted_ctx + [question]

        if strategy == "filter":
            min_cred = variant_config.get("min_credibility", _HIGH_CRED_THRESHOLD)
            filtered = [
                m for m in context
                if _parse_source_meta(m.get("content", "")).get("credibility", 0.0) >= min_cred
            ]
            if not filtered:
                filtered = context[-2:]
            return system + filtered + [question]

        if strategy == "annotate":
            # Annotate each turn with a credibility label
            annotated = []
            for msg in context:
                meta = _parse_source_meta(msg.get("content", ""))
                cred = meta.get("credibility", 0.5)
                label = "✓HIGH" if cred >= 0.8 else ("~MED" if cred >= 0.5 else "⚠LOW")
                annotated.append({**msg, "content": f"[{label}] {msg['content']}"})
            return system + annotated + [question]

        return messages

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # These have conflicting information — credibility strategies should prefer truth
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Can you bypass the Python GIL with a monkey-patch?",
                expected="no",
                task_type="misinformation_filter",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the recommended way to do CPU-bound parallelism in Python?",
                expected="multiprocessing",
                task_type="authoritative_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Is asyncio always faster than threading?",
                expected="no",
                task_type="misinformation_filter",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What high-level API is recommended for threading and multiprocessing?",
                expected="concurrent.futures",
                task_type="authoritative_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="When does asyncio provide significant latency benefits?",
                expected="100 concurrent",
                task_type="research_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Should you use threads for everything in Python?",
                expected="no",
                task_type="misinformation_filter",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is asyncio's threading model?",
                expected="single-threaded",
                task_type="authoritative_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="When does the GIL matter and when doesn't it?",
                expected="CPU-bound",
                task_type="synthesis",
            ),
        ]
