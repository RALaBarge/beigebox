"""
Opportunity #8: Context Depth vs. Breadth Strategy

Hypothesis: Task type determines optimal context distribution:
- Simple tasks: Broad context (many topics, recent) — +2-5%
- Complex tasks: Deep context (few topics, detailed) — +5-12%

Expected impact: +5-12% accuracy (MEDIUM confidence)

Strategy: Adaptive selection based on task classification.

Variants:
- Baseline: Fixed distribution (current)
- Broad First: Prioritize recent across topics
- Deep First: Prioritize detail within topics
- Adaptive: Classify task then select
"""

from __future__ import annotations

from typing import Any, Dict, List


class DepthVsBreadthExperiment:
    """Opportunity #8: Context Depth vs. Breadth experiment."""

    VARIANTS = [
        {
            "name": "baseline_balanced",
            "strategy": "balanced",
            "depth_ratio": 0.5,
            "breadth_ratio": 0.5,
            "description": "Fixed 50/50 depth/breadth (current)",
        },
        {
            "name": "broad_first_many_topics",
            "strategy": "broad",
            "depth_ratio": 0.3,
            "breadth_ratio": 0.7,
            "description": "Prioritize many topics, recent only",
        },
        {
            "name": "deep_first_detailed",
            "strategy": "deep",
            "depth_ratio": 0.7,
            "breadth_ratio": 0.3,
            "description": "Prioritize detailed info within topics",
        },
        {
            "name": "adaptive_task_aware",
            "strategy": "adaptive",
            "depth_ratio": None,  # Determined by task type
            "breadth_ratio": None,
            "description": "Classify task then select depth/breadth",
        },
    ]

    TEST_CASES = [
        {
            "input": "What is the capital of France?",
            "expected": "Paris",
            "type": "simple_factual",
        },
        {
            "input": "Analyze the trade-offs between X and Y given our context.",
            "expected": "analysis",
            "type": "complex_reasoning",
        },
        {
            "input": "Quick lookup: What time is it in Tokyo?",
            "expected": "time",
            "type": "simple_lookup",
        },
        {
            "input": "Deep dive: Synthesize everything we discussed about topic X.",
            "expected": "synthesis",
            "type": "complex_synthesis",
        },
        {
            "input": "Compare X, Y, Z across topics we covered.",
            "expected": "comparison",
            "type": "multi_topic",
        },
        {
            "input": "Detail the nuances of our discussion on topic A.",
            "expected": "nuances",
            "type": "single_topic_deep",
        },
        {
            "input": "What topics have we covered?",
            "expected": "topics",
            "type": "breadth_awareness",
        },
        {
            "input": "Explain the depth of our analysis on topic B.",
            "expected": "depth",
            "type": "depth_awareness",
        },
    ]

    def to_dict(self) -> Dict[str, Any]:
        """Export experiment config."""
        return {
            "opportunity_id": "context_depth_vs_breadth",
            "opportunity_name": "Context Depth vs. Breadth Strategy (Opportunity #8)",
            "hypothesis": "Task type determines optimal depth/breadth; adaptive selection improves accuracy",
            "expected_impact": "+5-12% accuracy",
            "variants": self.VARIANTS,
            "test_cases": self.TEST_CASES,
            "weight_profile": "general",
        }
