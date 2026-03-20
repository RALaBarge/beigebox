"""
Opportunity #7: Context Compression Strategy

Hypothesis: Abstractive summarization of context preserves information while
reducing tokens by 40-60%, maintaining accuracy.

Expected impact: -40% tokens, same accuracy (HIGH confidence)

Research: ACON framework (OpenReview) shows 40-60% token reduction possible.

Variants:
- Baseline: Raw context (no compression)
- Extractive: Select top K sentences
- Abstractive: LLM-generated summaries
- Hybrid: Mix extracted facts + LLM summary
"""

from __future__ import annotations

from typing import Any, Dict, List


class ContextCompressionExperiment:
    """Opportunity #7: Context Compression experiment."""

    VARIANTS = [
        {
            "name": "baseline_no_compression",
            "compression_strategy": "none",
            "compression_ratio": 1.0,
            "description": "Raw context (current behavior)",
        },
        {
            "name": "extractive_top_k",
            "compression_strategy": "extractive",
            "compression_ratio": 0.6,
            "description": "Extract top K sentences (40% reduction)",
        },
        {
            "name": "abstractive_summary",
            "compression_strategy": "abstractive",
            "compression_ratio": 0.4,
            "description": "LLM-generated summary (60% reduction)",
        },
        {
            "name": "hybrid_facts_plus_summary",
            "compression_strategy": "hybrid",
            "compression_ratio": 0.45,
            "description": "Key facts + LLM summary (55% reduction)",
        },
    ]

    TEST_CASES = [
        {
            "input": "Summarize the context in one sentence.",
            "expected": "summary",
            "type": "summary_quality",
        },
        {
            "input": "What was the main point?",
            "expected": "main",
            "type": "main_idea",
        },
        {
            "input": "List the key facts.",
            "expected": "facts",
            "type": "fact_extraction",
        },
        {
            "input": "Did we cover topic X?",
            "expected": "yes/no",
            "type": "coverage",
        },
        {
            "input": "What details did we skip?",
            "expected": "details",
            "type": "information_loss",
        },
        {
            "input": "Rate the completeness of this summary.",
            "expected": "rating",
            "type": "completeness",
        },
        {
            "input": "Any important nuances lost?",
            "expected": "nuances",
            "type": "nuance_preservation",
        },
        {
            "input": "Compare original vs. compressed quality.",
            "expected": "comparison",
            "type": "quality_delta",
        },
    ]

    def to_dict(self) -> Dict[str, Any]:
        """Export experiment config."""
        return {
            "opportunity_id": "context_compression",
            "opportunity_name": "Context Compression Strategy (Opportunity #7)",
            "hypothesis": "Abstractive summarization reduces tokens 40-60% while preserving accuracy",
            "expected_impact": "-40% tokens, same accuracy",
            "variants": self.VARIANTS,
            "test_cases": self.TEST_CASES,
            "weight_profile": "general",
        }
