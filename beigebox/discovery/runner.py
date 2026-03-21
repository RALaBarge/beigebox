"""
Discovery Experiment Runner

Executes a discovery opportunity experiment:
1. Load opportunity config (variants, test cases)
2. Run each variant on test cases via LLM backend
3. Collect JudgeRubric scores per variant
4. Identify Pareto front
5. Select champion
6. Persist to SQLite
7. Return results
"""

from __future__ import annotations

import httpx
import json
import logging
import uuid
from typing import Any, Dict, List

from beigebox.config import get_config
from beigebox.eval.judge import JudgeRubric, DimensionScore
from beigebox.eval.oracle import OracleRegistry
from beigebox.orchestration.optimizer import PromptOptimizer, ScoreCard
from beigebox.orchestration.pareto import ParetoOptimizer, ScoredVariant

logger = logging.getLogger(__name__)


def _emit_tap(event_type: str, content: str, run_id: str = "", meta: dict | None = None):
    """Emit structured event to Tap (wiretap) for observability."""
    try:
        from beigebox.main import get_state
        state = get_state()
        if state.proxy and state.proxy.wire:
            state.proxy.wire.log(
                direction="inbound",
                role="discovery",
                content=content,
                event_type=event_type,
                source="discovery",
                run_id=run_id,
                meta=meta or {},
            )
    except Exception as e:
        logger.debug(f"Failed to emit Tap event: {e}")


class DiscoveryRunner:
    """Execute a discovery opportunity experiment."""

    def __init__(
        self,
        sqlite_store=None,
        judge_model: str = "claude-opus",
        backend_url: str | None = None,
        candidate_model: str = "llama2",
    ):
        """Initialize runner."""
        self.sqlite_store = sqlite_store
        self.judge_model = judge_model
        self.optimizer = PromptOptimizer(judge_model=judge_model)
        self.pareto = ParetoOptimizer()

        # Get backend URL from config or use provided
        cfg = get_config()
        self.backend_url = backend_url or cfg.get("backend", {}).get("url", "http://localhost:11434")
        self.candidate_model = candidate_model

    async def _run_candidate_on_tests(
        self,
        variant_name: str,
        config: Dict[str, Any],
        test_cases: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Run a candidate on all test cases via LLM backend.

        Returns list of responses (one per test case).
        """
        responses = []

        # Build system prompt from config
        system_prompt = f"You are evaluating a {variant_name} approach. Answer concisely."
        if "description" in config:
            system_prompt = f"{system_prompt}\n\nApproach: {config['description']}"

        # Apply config parameters to request
        temperature = config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)

        async with httpx.AsyncClient(timeout=30) as client:
            for test_case in test_cases:
                try:
                    prompt = test_case.get("input", "")

                    # Call LLM backend
                    body = {
                        "model": self.candidate_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                        "top_p": top_p,
                        "stream": False,
                    }

                    # Try BeigeBox first (port 1337), then Ollama (11434)
                    for url in [
                        "http://127.0.0.1:1337/v1/chat/completions",
                        "http://127.0.0.1:11434/v1/chat/completions",
                    ]:
                        try:
                            resp = await client.post(url, json=body, timeout=30)
                            if resp.status_code == 200:
                                result = resp.json()
                                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                                responses.append(content)
                                break
                        except Exception:
                            continue
                    else:
                        # Fallback if no backend responded
                        responses.append(f"(no response from {self.candidate_model})")

                except Exception as e:
                    logger.warning(f"Failed to run test case: {e}")
                    responses.append(f"(error: {str(e)[:50]})")

        return responses

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

        # Emit discovery start event to Tap
        _emit_tap(
            "discovery_start",
            f"Discovery run {run_id}: {opportunity_name} ({len(variants)} variants, {len(test_cases)} cases)",
            run_id=run_id,
            meta={
                "opportunity_id": opportunity_id,
                "opportunity_name": opportunity_name,
                "num_variants": len(variants),
                "num_test_cases": len(test_cases),
                "weight_profile": weight_profile,
            }
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

                # Emit variant start event
                _emit_tap(
                    "discovery_variant_start",
                    f"Scoring variant: {variant_name}",
                    run_id=run_id,
                    meta={"variant_name": variant_name}
                )

                # Run candidate on test cases
                logger.info(f"    Running {len(test_cases)} test cases...")
                responses = await self._run_candidate_on_tests(variant_name, config, test_cases)

                # Run oracle tests
                oracle_pass_rate = oracle.run_all(lambda inp: responses[min(0, len(responses) - 1)] if responses else "test")
                oracle_passed = oracle_pass_rate >= 0.8

                # Score with JudgeRubric on actual responses
                dim_scores = {
                    "accuracy": [],
                    "efficiency": [],
                    "clarity": [],
                    "hallucination": [],
                    "safety": [],
                }

                for i, test_case in enumerate(test_cases[:5]):  # Limit to first 5 for speed
                    try:
                        prompt = test_case.get("input", "")
                        expected = test_case.get("expected", "")
                        response = responses[i] if i < len(responses) else "(no response)"

                        dim_score = await judge.score(
                            prompt=prompt,
                            response=response,
                            context=f"Expected: {expected}",
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

                # Emit variant complete event
                _emit_tap(
                    "discovery_variant_complete",
                    f"Variant {variant_name}: score={overall:.3f}",
                    run_id=run_id,
                    meta={
                        "variant_name": variant_name,
                        "overall_score": overall,
                        "accuracy": avg_dims["accuracy"],
                        "efficiency": avg_dims["efficiency"],
                        "clarity": avg_dims["clarity"],
                        "hallucination": avg_dims["hallucination"],
                        "safety": avg_dims["safety"],
                    }
                )

            except Exception as e:
                logger.exception(f"Failed to score variant {variant_name}: {e}")
                _emit_tap(
                    "discovery_variant_error",
                    f"Failed to score {variant_name}: {str(e)[:100]}",
                    run_id=run_id,
                    meta={"variant_name": variant_name, "error": str(e)[:100]}
                )

        # Find Pareto front
        pareto_front = self.pareto.find_pareto_front(scored_variants)

        # Select champion
        champion = self.pareto.select_champion(scored_variants, weight_profile)

        logger.info(
            f"Discovery complete: {len(pareto_front)} on Pareto front, "
            f"champion={champion.name if champion else None}"
        )

        # Emit discovery complete event
        _emit_tap(
            "discovery_complete",
            f"Discovery {opportunity_id}: {len(pareto_front)} Pareto variants, champion={champion.name if champion else 'none'}",
            run_id=run_id,
            meta={
                "opportunity_id": opportunity_id,
                "num_variants_scored": len(scored_variants),
                "pareto_size": len(pareto_front),
                "champion": champion.name if champion else None,
                "champion_score": champion.weighted if champion else None,
            }
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
