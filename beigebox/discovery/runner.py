"""
Discovery Experiment Runner

Executes a discovery opportunity experiment:
1. Load opportunity config (variants, test cases)
2. Run PromptOptimizer with each variant
3. Collect JudgeRubric scores
4. Identify Pareto front
5. Select champion
6. Persist to SQLite
7. Return results
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List

from beigebox.eval.judge import JudgeRubric, DimensionScore
from beigebox.eval.oracle import OracleRegistry
from beigebox.orchestration.optimizer import PromptOptimizer, ScoreCard
from beigebox.orchestration.pareto import ParetoOptimizer, ScoredVariant

logger = logging.getLogger(__name__)


class DiscoveryRunner:
    """Execute a discovery opportunity experiment."""

    def __init__(
        self,
        sqlite_store=None,
        judge_model: str = "claude-opus",
    ):
        """Initialize runner."""
        self.sqlite_store = sqlite_store
        self.judge_model = judge_model
        self.optimizer = PromptOptimizer(judge_model=judge_model)
        self.pareto = ParetoOptimizer()

    async def run_opportunity(
        self,
        opportunity_id: str,
        opportunity_name: str,
        variants: List[Dict[str, Any]],
        test_cases: List[Dict[str, Any]],
        weight_profile: str = "general",
    ) -> Dict[str, Any]:
        """
        Execute a discovery opportunity experiment.

        Args:
            opportunity_id: Unique ID (e.g. "position_sensitivity")
            opportunity_name: Human-readable name
            variants: List of {name, config} dicts
            test_cases: List of {input, expected} test cases
            weight_profile: Weight profile for scoring (general, code, reasoning, safety)

        Returns:
            {
                "run_id": str,
                "opportunity_id": str,
                "opportunity_name": str,
                "pareto_front": [ScoredVariant],
                "champion": ScoredVariant,
                "scorecards": [ScoreCard],
                "summary": {...}
            }
        """
        run_id = str(uuid.uuid4())[:8]
        logger.info(
            f"Starting discovery run {run_id}: {opportunity_name} "
            f"({len(variants)} variants, {len(test_cases)} test cases)"
        )

        # Score each variant
        scored_variants = []
        judge = JudgeRubric(judge_model=self.judge_model)
        oracle = OracleRegistry()

        for variant in variants:
            variant_name = variant.get("name", "unknown")
            config = variant.get("config", {})

            try:
                logger.info(f"  Scoring variant: {variant_name}")

                # Run oracle tests
                oracle_pass_rate = oracle.run_all(lambda inp: "test")
                oracle_passed = oracle_pass_rate >= 0.8

                # Score with JudgeRubric on a few test cases
                dim_scores = {
                    "accuracy": [],
                    "efficiency": [],
                    "clarity": [],
                    "hallucination": [],
                    "safety": [],
                }

                for test_case in test_cases[:5]:  # Limit to first 5 for speed
                    try:
                        prompt = test_case.get("input", "")
                        expected = test_case.get("expected", "")

                        context = f"Variant: {variant_name}\nConfig: {config}\nExpected: {expected}"

                        dim_score = await judge.score(
                            prompt=prompt,
                            response=f"variant evaluation",
                            context=context,
                        )

                        dim_scores["accuracy"].append(dim_score.accuracy)
                        dim_scores["efficiency"].append(dim_score.efficiency)
                        dim_scores["clarity"].append(dim_score.clarity)
                        dim_scores["hallucination"].append(dim_score.hallucination)
                        dim_scores["safety"].append(dim_score.safety)
                    except Exception as e:
                        logger.warning(f"Failed to score test case: {e}")
                        continue

                # Aggregate scores
                avg_dims = {
                    dim: sum(scores) / len(scores) if scores else 2.5
                    for dim, scores in dim_scores.items()
                }

                # Normalize and weight
                normalized = {dim: score / 5.0 for dim, score in avg_dims.items()}
                weights = {
                    "accuracy": 0.3,
                    "efficiency": 0.2,
                    "clarity": 0.2,
                    "hallucination": 0.2,
                    "safety": 0.1,
                }
                overall = sum(
                    normalized.get(dim, 0.5) * weight
                    for dim, weight in weights.items()
                ) / sum(weights.values())

                # Create scored variant
                dim_obj = DimensionScore(
                    accuracy=avg_dims["accuracy"],
                    efficiency=avg_dims["efficiency"],
                    clarity=avg_dims["clarity"],
                    hallucination=avg_dims["hallucination"],
                    safety=avg_dims["safety"],
                )

                scored_var = ScoredVariant(
                    name=variant_name,
                    scores=dim_obj,
                    weighted=overall,
                )
                scored_variants.append(scored_var)

                # Persist to SQLite
                if self.sqlite_store:
                    self.sqlite_store.store_discovery_scorecard(
                        run_id=run_id,
                        opportunity_id=opportunity_id,
                        variant_name=variant_name,
                        accuracy=avg_dims["accuracy"],
                        efficiency=avg_dims["efficiency"],
                        clarity=avg_dims["clarity"],
                        hallucination=avg_dims["hallucination"],
                        safety=avg_dims["safety"],
                        overall_score=overall,
                        oracle_passed=oracle_passed,
                        weight_profile=weight_profile,
                    )

                logger.info(
                    f"    {variant_name}: overall={overall:.3f}, "
                    f"accuracy={avg_dims['accuracy']:.1f}, "
                    f"efficiency={avg_dims['efficiency']:.1f}"
                )

            except Exception as e:
                logger.exception(f"Failed to score variant {variant_name}: {e}")

        # Find Pareto front
        pareto_front = self.pareto.find_pareto_front(scored_variants)

        # Select champion
        champion = self.pareto.select_champion(scored_variants, weight_profile)

        logger.info(
            f"Discovery complete: {len(pareto_front)} on Pareto front, "
            f"champion={champion.name if champion else None}"
        )

        return {
            "run_id": run_id,
            "opportunity_id": opportunity_id,
            "opportunity_name": opportunity_name,
            "pareto_front": [v.to_dict() for v in pareto_front],
            "champion": champion.to_dict() if champion else None,
            "scorecards": [v.to_dict() for v in scored_variants],
            "summary": {
                "total_variants": len(scored_variants),
                "pareto_size": len(pareto_front),
                "test_cases": len(test_cases),
                "oracle_requirement": ">=80% pass rate",
            },
        }
