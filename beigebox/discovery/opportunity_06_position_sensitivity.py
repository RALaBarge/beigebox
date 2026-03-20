"""
Opportunity #6: Position Sensitivity (Needle-in-Haystack)

Hypothesis: Critical facts placed at context extremes (position 0 or -1) are
retrieved more reliably than facts in the middle (lost-in-the-middle effect).

Expected impact: +20-40% fact recall (HIGH confidence)

Research: VLDB 2026, PMC 2026 studies show severe degradation for middle positions.

Variants:
- Baseline: Facts in middle of context (current behavior)
- Facts First: Critical facts at position 0
- Facts Last: Critical facts at position -1
- Alternating: Alternate important/supporting facts
"""

from __future__ import annotations

from typing import Any, Dict, List


class PositionSensitivityExperiment:
    """Opportunity #6: Position Sensitivity experiment."""

    VARIANTS = [
        {
            "name": "baseline_middle",
            "position_strategy": "middle",
            "description": "Critical facts in middle of context (current behavior)",
        },
        {
            "name": "facts_first",
            "position_strategy": "start",
            "description": "Critical facts at beginning (position 0)",
        },
        {
            "name": "facts_last",
            "position_strategy": "end",
            "description": "Critical facts at end (position -1)",
        },
        {
            "name": "alternating_important",
            "position_strategy": "alternating",
            "description": "Alternate important/supporting facts throughout",
        },
    ]

    TEST_CASES = [
        {
            "input": "Recall the first fact I mentioned.",
            "expected": "first",
            "type": "recall_first",
        },
        {
            "input": "Recall the middle fact.",
            "expected": "middle",
            "type": "recall_middle",
        },
        {
            "input": "Recall the last fact.",
            "expected": "last",
            "type": "recall_last",
        },
        {
            "input": "What was the most important fact?",
            "expected": "important",
            "type": "importance_ranking",
        },
        {
            "input": "List all facts in order.",
            "expected": "order",
            "type": "sequence_recall",
        },
        {
            "input": "Which fact was hardest to remember?",
            "expected": "difficult",
            "type": "meta_recall",
        },
        {
            "input": "Did I mention a fact about X?",
            "expected": "yes/no",
            "type": "fact_presence",
        },
        {
            "input": "Compare the facts I mentioned.",
            "expected": "comparison",
            "type": "relational",
        },
    ]

    def to_dict(self) -> Dict[str, Any]:
        """Export experiment config."""
        return {
            "opportunity_id": "position_sensitivity",
            "opportunity_name": "Position Sensitivity / Needle-in-Haystack (Opportunity #6)",
            "hypothesis": "Critical facts at extremes (0 or -1) retrieved better than middle",
            "expected_impact": "+20-40% fact recall",
            "variants": self.VARIANTS,
            "test_cases": self.TEST_CASES,
            "weight_profile": "general",
        }
