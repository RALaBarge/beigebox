"""
Pareto frontier discovery for multi-objective optimization.

Given a set of variants each scored on 5 independent dimensions,
identify the Pareto-optimal set (non-dominated solutions).

Pareto dominance: A dominates B if:
- A is >= B on all dimensions
- A is > B on at least one dimension

Use cases:
- Context optimization: code vs. reasoning trade-offs
- Safety vs. efficiency: where to invest
- Selecting champion from population of candidates
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from beigebox.eval.judge import DimensionScore

logger = logging.getLogger(__name__)


@dataclass
class ScoredVariant:
    """A variant with its multi-dimensional scores."""

    name: str
    scores: DimensionScore
    weighted: float  # Overall score from apply_weights()

    def to_dict(self) -> dict:
        """Export as dict for serialization."""
        return {
            "name": self.name,
            "scores": self.scores.to_dict(),
            "weighted": self.weighted,
        }


class ParetoOptimizer:
    """
    Multi-objective optimization via Pareto frontier.

    Predefined weight profiles for common scenarios.
    """

    WEIGHT_PROFILES = {
        "code": {
            "accuracy": 0.5,
            "efficiency": 0.3,
            "clarity": 0.1,
            "hallucination": 0.1,
            "safety": 0.0,
        },
        "reasoning": {
            "accuracy": 0.4,
            "efficiency": 0.2,
            "clarity": 0.2,
            "hallucination": 0.2,
            "safety": 0.0,
        },
        "general": {
            "accuracy": 0.3,
            "efficiency": 0.2,
            "clarity": 0.2,
            "hallucination": 0.2,
            "safety": 0.1,
        },
        "safety": {
            "accuracy": 0.2,
            "efficiency": 0.1,
            "clarity": 0.1,
            "hallucination": 0.3,
            "safety": 0.3,
        },
    }

    def __init__(self):
        """Initialize Pareto optimizer."""
        pass

    def find_pareto_front(
        self,
        variants: list[ScoredVariant],
    ) -> list[ScoredVariant]:
        """
        Identify all non-dominated variants.

        A variant is on the Pareto front if no other variant
        dominates it (is better on all dimensions).

        Args:
            variants: List of scored variants

        Returns:
            Pareto-optimal subset (may include input variants)
        """
        if not variants:
            return []

        front = []
        for candidate in variants:
            # Check if any existing front member dominates this candidate
            dominated = False
            for front_member in front:
                if self._dominates(front_member, candidate):
                    dominated = True
                    break

            if not dominated:
                # Remove any front members that this candidate dominates
                front = [
                    m for m in front if not self._dominates(candidate, m)
                ]
                front.append(candidate)

        return front

    def select_champion(
        self,
        variants: list[ScoredVariant],
        weight_profile: str = "general",
    ) -> Optional[ScoredVariant]:
        """
        Select best variant given a weight profile.

        Args:
            variants: List of scored variants
            weight_profile: One of code|reasoning|general|safety

        Returns:
            Best variant under the given weights, or None if empty
        """
        if not variants:
            return None

        weights = self.WEIGHT_PROFILES.get(weight_profile)
        if not weights:
            logger.warning(f"Unknown weight profile: {weight_profile}")
            weights = self.WEIGHT_PROFILES["general"]

        # Apply weights and find max
        best = None
        best_score = -1.0

        for variant in variants:
            # Compute weighted score (normalized 0-1)
            normalized = variant.scores.to_normalized()
            total_weight = sum(weights.values())
            weighted = sum(
                normalized.get(dim, 0.5) * weight
                for dim, weight in weights.items()
            ) / total_weight if total_weight > 0 else 0.5

            if weighted > best_score:
                best_score = weighted
                best = ScoredVariant(
                    name=variant.name,
                    scores=variant.scores,
                    weighted=weighted,
                )

        return best

    def _dominates(
        self,
        a: ScoredVariant,
        b: ScoredVariant,
    ) -> bool:
        """
        Check if variant A dominates variant B.

        Dominance: A >= B on all dimensions AND A > B on at least one.

        Args:
            a: Candidate dominating variant
            b: Candidate being dominated

        Returns:
            True if A strictly dominates B
        """
        a_scores = a.scores.to_normalized()
        b_scores = b.scores.to_normalized()

        # Check >= on all dimensions
        all_gte = all(
            a_scores.get(dim, 0) >= b_scores.get(dim, 0)
            for dim in ["accuracy", "efficiency", "clarity", "hallucination", "safety"]
        )

        if not all_gte:
            return False

        # Check > on at least one
        any_gt = any(
            a_scores.get(dim, 0) > b_scores.get(dim, 0)
            for dim in ["accuracy", "efficiency", "clarity", "hallucination", "safety"]
        )

        return any_gt

    def analyze_tradeoffs(
        self,
        variants: list[ScoredVariant],
    ) -> dict:
        """
        Analyze dimension trade-offs in the population.

        Returns:
            Summary of Pareto front and trade-offs
        """
        front = self.find_pareto_front(variants)

        if not front:
            return {"status": "no variants"}

        # Compute dimension ranges
        dims = ["accuracy", "efficiency", "clarity", "hallucination", "safety"]
        ranges = {}

        for dim in dims:
            values = [v.scores.to_normalized()[dim] for v in front]
            ranges[dim] = {
                "min": min(values),
                "max": max(values),
                "spread": max(values) - min(values),
            }

        return {
            "front_size": len(front),
            "total_variants": len(variants),
            "dimension_ranges": ranges,
            "front": [v.to_dict() for v in front],
        }
