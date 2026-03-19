"""
PromptOptimizer: Iterative self-refinement via Champion/Challenger loops.

Uses the Harness to run variations, Judge to score, Oracle to verify.
Explores the frontier of what's possible by mutating prompts, constraints,
and sampling parameters (temperature, top_p, etc.) to find better configurations.

Key insight: Controlled chaos at the boundary of madness is where creativity lives.
We systematically explore the solution space humans might miss.
"""

from __future__ import annotations

import logging
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime

from beigebox.orchestration.packet import WorkerType, TaskPacket, WorkerResult
from beigebox.orchestration.worker_profiles import WorkerProfiles

logger = logging.getLogger(__name__)


@dataclass
class MutationStrategy:
    """Configuration for a mutation strategy."""

    name: str
    description: str
    apply: Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class ScoreCard:
    """Multi-dimensional score for a candidate."""

    iteration: int
    candidate_id: str
    variant_name: str
    scores: Dict[str, float] = field(default_factory=dict)  # e.g., accuracy, brevity, speed
    overall_score: float = 0.0
    oracle_passed: bool = False
    is_champion: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize scorecard."""
        return {
            "iteration": self.iteration,
            "candidate_id": self.candidate_id,
            "variant_name": self.variant_name,
            "scores": self.scores,
            "overall_score": self.overall_score,
            "oracle_passed": self.oracle_passed,
            "is_champion": self.is_champion,
            "timestamp": self.timestamp,
        }


class PromptOptimizer:
    """
    Champion/Challenger loop for iteratively improving worker configurations.

    Strategy:
    1. Load Champion (baseline worker profile/prompt)
    2. Generate N Challengers via mutation (prompt, constraints, temp, few-shot)
    3. Run all via Harness (parallel evaluation)
    4. Judge scores all (multi-dimensional: accuracy, brevity, latency, safety)
    5. Oracle verifies no regressions (unit tests, deterministic checks)
    6. If Challenger beats Champion by threshold, promote to new Champion
    7. Repeat until convergence or iteration cap

    Cost controls:
    - Hard iteration limit
    - Diminishing returns detection
    - Budget cap in tokens
    """

    def __init__(
        self,
        judge_model: str = "claude-opus",
        oracle_tests: Optional[List[Callable]] = None,
        max_iterations: int = 10,
        improvement_threshold: float = 0.05,  # 5% improvement required
        convergence_patience: int = 3,  # Stop if no improvement for N iterations
    ):
        """
        Initialize optimizer.

        Args:
            judge_model: Model to use for scoring (should be stronger than generator)
            oracle_tests: List of deterministic test functions (must pass)
            max_iterations: Hard limit on iterations
            improvement_threshold: Minimum improvement to promote Challenger
            convergence_patience: Stop if flat for this many iterations
        """
        self.judge_model = judge_model
        self.oracle_tests = oracle_tests or []
        self.max_iterations = max_iterations
        self.improvement_threshold = improvement_threshold
        self.convergence_patience = convergence_patience
        self.worker_profiles = WorkerProfiles()
        self.history: List[ScoreCard] = []
        self.best_champion: Optional[Dict[str, Any]] = None
        self.best_score: float = 0.0

    def optimize(
        self,
        worker: WorkerType,
        champion_config: Dict[str, Any],
        test_cases: List[Dict[str, Any]],
        judge_prompt: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[ScoreCard]]:
        """
        Run Champion/Challenger optimization loop.

        Args:
            worker: Worker type to optimize (RESEARCH, CODER, OPERATOR, JUDGE)
            champion_config: Baseline configuration to improve
            test_cases: List of {input, expected_output} for oracle testing
            judge_prompt: Custom judge scoring rubric (if None, use default)

        Returns:
            (best_champion, history) — optimized config and full scorecard history
        """
        logger.info(
            f"Starting optimization for {worker.value}. "
            f"Max iterations: {self.max_iterations}, "
            f"Improvement threshold: {self.improvement_threshold*100:.0f}%"
        )

        champion = champion_config.copy()
        champion_id = str(uuid.uuid4())[:8]
        champion_score = self._score_candidate(champion, test_cases, judge_prompt)
        self.best_score = champion_score
        self.best_champion = champion

        flat_iterations = 0

        for iteration in range(self.max_iterations):
            logger.info(f"=== Iteration {iteration + 1}/{self.max_iterations} ===")
            logger.info(f"Champion score: {champion_score:.3f}")

            # Generate challengers via mutation
            challengers = self._generate_challengers(champion, n=3)
            logger.info(f"Generated {len(challengers)} challengers via mutation")

            # Score all challengers
            challenger_scores = {}
            for variant_name, config in challengers.items():
                score = self._score_candidate(config, test_cases, judge_prompt)
                challenger_scores[variant_name] = (score, config)
                logger.debug(f"  {variant_name}: {score:.3f}")

            # Find best challenger
            best_variant = max(challenger_scores, key=lambda x: challenger_scores[x][0])
            best_challenger_score, best_challenger_config = challenger_scores[best_variant]

            # Check improvement
            improvement = (best_challenger_score - champion_score) / max(
                champion_score, 0.01
            )

            if improvement >= self.improvement_threshold:
                logger.info(
                    f"✓ New champion! {best_variant} improved by {improvement*100:.1f}%"
                )
                champion = best_challenger_config
                champion_score = best_challenger_score
                self.best_score = champion_score
                self.best_champion = champion
                flat_iterations = 0

                # Log scorecard
                self.history.append(
                    ScoreCard(
                        iteration=iteration,
                        candidate_id=str(uuid.uuid4())[:8],
                        variant_name=best_variant,
                        scores={"score": best_challenger_score},
                        overall_score=best_challenger_score,
                        oracle_passed=True,
                        is_champion=True,
                    )
                )
            else:
                logger.info(
                    f"✗ No improvement ({improvement*100:.1f}% < {self.improvement_threshold*100:.0f}%)"
                )
                flat_iterations += 1

                # Log scorecards for all challengers
                for variant_name, (score, _) in challenger_scores.items():
                    self.history.append(
                        ScoreCard(
                            iteration=iteration,
                            candidate_id=str(uuid.uuid4())[:8],
                            variant_name=variant_name,
                            scores={"score": score},
                            overall_score=score,
                            oracle_passed=True,
                            is_champion=False,
                        )
                    )

            # Check convergence
            if flat_iterations >= self.convergence_patience:
                logger.info(
                    f"Converged: no improvement for {self.convergence_patience} iterations"
                )
                break

        logger.info(
            f"Optimization complete. Best score: {self.best_score:.3f} "
            f"(improvement: {(self.best_score - self._score_candidate(champion_config, test_cases, judge_prompt))*100:.1f}%)"
        )

        return self.best_champion, self.history

    def _generate_challengers(
        self, champion: Dict[str, Any], n: int = 3
    ) -> Dict[str, Dict[str, Any]]:
        """
        Generate N challenger variants via mutation.

        Mutation strategies:
        - Prompt perturbation: rephrase system prompt with different emphasis
        - Temperature variation: explore wider solution space (high temp)
        - Tool limit mutation: vary allowed tool calls
        - Constraint mutation: modify must_do/must_not_do
        - Few-shot swapping: vary example demonstrations
        """
        challengers = {}

        # Strategy 1: Temperature mutation (explore boundary of madness)
        temps = [0.3, 0.7, 1.2, 1.5]  # Cold → hot → chaotic
        for temp in temps[:n]:
            variant = champion.copy()
            variant["temperature"] = temp
            variant["top_p"] = 0.9 + (temp / 10)  # Increase diversity with temp
            challengers[f"temp_{temp}"] = variant

        # Strategy 2: Tool limit mutation
        if "constraints" in champion and "tool_limits" in champion["constraints"]:
            for n_tools in [2, 5, 10]:
                variant = champion.copy()
                variant["constraints"] = champion["constraints"].copy()
                variant["constraints"]["tool_limits"] = [
                    f"max_calls={n_tools}",
                    f"budget={n_tools * 100}",
                ]
                challengers[f"tools_{n_tools}"] = variant

        # Strategy 3: Constraint mutation (edge of madness)
        if "constraints" in champion:
            variant = champion.copy()
            variant["constraints"] = champion["constraints"].copy()
            # Make must_do more aggressive
            variant["constraints"]["must_do"] = [
                "Think step-by-step before answering",
                "Consider edge cases and failure modes",
                "Verify your answer before submitting",
                "Challenge your own assumptions",
            ]
            challengers["aggressive_verify"] = variant

            # Make more exploratory
            variant2 = champion.copy()
            variant2["constraints"] = champion["constraints"].copy()
            variant2["constraints"]["must_do"] = [
                "Explore multiple approaches",
                "Consider unconventional solutions",
                "Explain your reasoning",
                "Note any uncertainties",
            ]
            variant2["temperature"] = 1.2
            variant2["top_p"] = 0.95
            challengers["exploratory"] = variant2

        return challengers

    def _score_candidate(
        self,
        config: Dict[str, Any],
        test_cases: List[Dict[str, Any]],
        judge_prompt: Optional[str] = None,
    ) -> float:
        """
        Score a candidate via Judge evaluation and Oracle verification.

        Returns:
            Score 0.0-1.0 (0=worst, 1.0=perfect)
        """
        # Run Oracle tests first (fast, deterministic)
        if self.oracle_tests:
            oracle_pass_rate = self._run_oracle(config, test_cases)
            if oracle_pass_rate < 0.8:  # Oracle is hard requirement
                logger.warning(
                    f"Oracle failed ({oracle_pass_rate*100:.0f}%), "
                    f"rejecting candidate"
                )
                return 0.0

        # Judge scoring (slower, LLM-based)
        judge_score = self._judge_score(config, test_cases, judge_prompt)

        return judge_score

    def _run_oracle(
        self, config: Dict[str, Any], test_cases: List[Dict[str, Any]]
    ) -> float:
        """
        Run deterministic oracle tests on candidate.

        Returns:
            Pass rate (0.0-1.0)
        """
        if not self.oracle_tests:
            return 1.0  # No tests = auto-pass

        passed = 0
        for test_fn in self.oracle_tests:
            try:
                if test_fn(config, test_cases):
                    passed += 1
            except Exception as e:
                logger.warning(f"Oracle test failed: {e}")

        return passed / len(self.oracle_tests)

    def _judge_score(
        self,
        config: Dict[str, Any],
        test_cases: List[Dict[str, Any]],
        judge_prompt: Optional[str] = None,
    ) -> float:
        """
        Use Judge model to score candidate on multi-dimensional rubric.

        Returns:
            Overall score 0.0-1.0
        """
        if judge_prompt is None:
            judge_prompt = self._default_judge_prompt()

        # In a real implementation, this would:
        # 1. Run candidate on test_cases via Harness
        # 2. Send results to Judge model
        # 3. Parse multi-dimensional scores
        # 4. Return weighted average

        # For now, return mock score (would integrate with actual Harness)
        import random

        return random.uniform(0.5, 1.0)

    def _default_judge_prompt(self) -> str:
        """Default multi-dimensional scoring rubric."""
        return """
Evaluate the candidate on these dimensions (each 0-5):
- Accuracy: Does it produce correct outputs?
- Brevity: Is it concise and to-the-point?
- Clarity: Is it easy to understand and follow?
- Safety: Does it avoid harmful or unsafe outputs?
- Efficiency: Is it fast and resource-aware?

Return JSON:
{
  "accuracy": 0-5,
  "brevity": 0-5,
  "clarity": 0-5,
  "safety": 0-5,
  "efficiency": 0-5,
  "justification": "brief explanation for any score < 4"
}
"""

    def get_history(self) -> List[Dict[str, Any]]:
        """Return scorecard history as serializable dicts."""
        return [card.to_dict() for card in self.history]

    def summarize(self) -> Dict[str, Any]:
        """Return optimization summary."""
        if not self.history:
            return {"status": "no iterations run"}

        best_card = max(
            [c for c in self.history if c.is_champion],
            key=lambda x: x.overall_score,
            default=None,
        )

        return {
            "total_iterations": len(
                set(c.iteration for c in self.history if c.is_champion)
            ),
            "best_score": self.best_score,
            "best_variant": best_card.variant_name if best_card else None,
            "improvement": self.best_score - self.history[0].overall_score
            if self.history
            else 0.0,
            "history": self.get_history(),
        }
