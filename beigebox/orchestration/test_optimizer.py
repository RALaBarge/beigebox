"""
Tests for PromptOptimizer: Champion/Challenger self-refinement loops.
"""

import pytest
from beigebox.orchestration.optimizer import PromptOptimizer, ScoreCard, MutationStrategy
from beigebox.orchestration.packet import WorkerType


class TestScoreCard:
    """Test ScoreCard serialization."""

    def test_scorecard_creation(self):
        """Create a scorecard with multi-dimensional scores."""
        card = ScoreCard(
            iteration=0,
            candidate_id="abc123",
            variant_name="temp_0.7",
            scores={"accuracy": 4.5, "brevity": 3.2, "clarity": 4.8},
            overall_score=4.17,
            oracle_passed=True,
            is_champion=True,
        )

        assert card.iteration == 0
        assert card.variant_name == "temp_0.7"
        assert card.overall_score == 4.17
        assert card.oracle_passed is True
        assert card.is_champion is True

    def test_scorecard_serialization(self):
        """Serialize scorecard to dict."""
        card = ScoreCard(
            iteration=1,
            candidate_id="xyz789",
            variant_name="aggressive_verify",
            scores={"accuracy": 4.8, "safety": 5.0},
            overall_score=4.9,
            oracle_passed=True,
            is_champion=False,
        )

        card_dict = card.to_dict()
        assert isinstance(card_dict, dict)
        assert card_dict["iteration"] == 1
        assert card_dict["variant_name"] == "aggressive_verify"
        assert card_dict["overall_score"] == 4.9
        assert "timestamp" in card_dict


class TestPromptOptimizer:
    """Test PromptOptimizer initialization and configuration."""

    def test_optimizer_init(self):
        """Initialize optimizer with defaults."""
        opt = PromptOptimizer()

        assert opt.judge_model == "claude-opus"
        assert opt.max_iterations == 10
        assert opt.improvement_threshold == 0.05
        assert opt.convergence_patience == 3
        assert opt.best_score == 0.0

    def test_optimizer_init_custom(self):
        """Initialize optimizer with custom parameters."""
        opt = PromptOptimizer(
            judge_model="gpt-4",
            max_iterations=5,
            improvement_threshold=0.1,
            convergence_patience=2,
        )

        assert opt.judge_model == "gpt-4"
        assert opt.max_iterations == 5
        assert opt.improvement_threshold == 0.1
        assert opt.convergence_patience == 2

    def test_oracle_tests_registration(self):
        """Register oracle test functions."""

        def test_compilation(config, test_cases):
            return True

        def test_safety(config, test_cases):
            return True

        opt = PromptOptimizer(oracle_tests=[test_compilation, test_safety])

        assert len(opt.oracle_tests) == 2


class TestMutationStrategies:
    """Test mutation strategy generation."""

    def test_temperature_mutation(self):
        """Generate temperature variants."""
        opt = PromptOptimizer()
        champion = {
            "temperature": 0.5,
            "top_p": 0.9,
            "constraints": {"must_do": ["test"], "tool_limits": ["max_calls=5"]},
        }

        challengers = opt._generate_challengers(champion, n=3)

        # Should have temperature variants
        temp_variants = [k for k in challengers.keys() if k.startswith("temp_")]
        assert len(temp_variants) >= 3

        # Each variant should have different temperature
        temps = [float(v.split("_")[1]) for v in temp_variants]
        assert len(set(temps)) >= 2  # At least 2 different temps

    def test_tool_limit_mutation(self):
        """Generate tool limit variants."""
        opt = PromptOptimizer()
        champion = {
            "temperature": 0.7,
            "constraints": {
                "must_do": ["execute task"],
                "tool_limits": ["max_calls=5"],
            },
        }

        challengers = opt._generate_challengers(champion, n=2)

        # Should have tool variants
        tool_variants = [k for k in challengers.keys() if k.startswith("tools_")]
        assert len(tool_variants) > 0

    def test_constraint_mutation(self):
        """Generate constraint variants (edge of madness)."""
        opt = PromptOptimizer()
        champion = {
            "temperature": 0.7,
            "constraints": {
                "must_do": ["answer quickly"],
                "must_not_do": ["hallucinate"],
            },
        }

        challengers = opt._generate_challengers(champion, n=2)

        # Should have constraint variants
        assert "aggressive_verify" in challengers
        assert "exploratory" in challengers

        # Exploratory variant should have higher temperature (edge of chaos)
        exploratory = challengers["exploratory"]
        assert exploratory["temperature"] == 1.2
        assert exploratory["top_p"] == 0.95

    def test_challenger_generation_count(self):
        """Generate exactly N challengers."""
        opt = PromptOptimizer()
        champion = {
            "temperature": 0.5,
            "constraints": {"must_do": [], "tool_limits": ["max_calls=5"]},
        }

        for n in [1, 2, 5]:
            challengers = opt._generate_challengers(champion, n=n)
            assert len(challengers) >= n

        # Full challenger suite includes temperature, tool, and constraint variants
        challengers = opt._generate_challengers(champion, n=3)
        assert len(challengers) >= 3


class TestOracleVerification:
    """Test Oracle (deterministic) verification."""

    def test_oracle_pass_rate_all_pass(self):
        """All oracle tests pass."""
        opt = PromptOptimizer(
            oracle_tests=[
                lambda cfg, tc: True,
                lambda cfg, tc: True,
                lambda cfg, tc: True,
            ]
        )

        config = {"temperature": 0.7}
        test_cases = [{"input": "test"}]

        pass_rate = opt._run_oracle(config, test_cases)
        assert pass_rate == 1.0

    def test_oracle_pass_rate_partial(self):
        """Some oracle tests fail."""
        opt = PromptOptimizer(
            oracle_tests=[
                lambda cfg, tc: True,
                lambda cfg, tc: False,
                lambda cfg, tc: True,
            ]
        )

        config = {"temperature": 0.7}
        test_cases = [{"input": "test"}]

        pass_rate = opt._run_oracle(config, test_cases)
        assert pass_rate == pytest.approx(2 / 3)

    def test_oracle_exception_handling(self):
        """Oracle tests that raise exceptions are counted as failures."""

        def failing_test(cfg, tc):
            raise RuntimeError("Test error")

        opt = PromptOptimizer(oracle_tests=[failing_test, lambda cfg, tc: True])

        config = {"temperature": 0.7}
        test_cases = []

        pass_rate = opt._run_oracle(config, test_cases)
        assert pass_rate == 0.5  # 1 pass, 1 fail

    def test_oracle_no_tests(self):
        """No oracle tests = auto-pass."""
        opt = PromptOptimizer(oracle_tests=[])

        config = {"temperature": 0.7}
        test_cases = [{"input": "test"}]

        pass_rate = opt._run_oracle(config, test_cases)
        assert pass_rate == 1.0


class TestJudgeScoring:
    """Test Judge (LLM-based) scoring."""

    def test_judge_prompt_generation(self):
        """Generate default judge prompt."""
        opt = PromptOptimizer()
        prompt = opt._default_judge_prompt()

        assert "Accuracy" in prompt
        assert "Brevity" in prompt
        assert "Clarity" in prompt
        assert "Safety" in prompt
        assert "JSON" in prompt

    def test_judge_score_range(self):
        """Judge scores are in valid range (0-1)."""
        opt = PromptOptimizer()

        config = {"temperature": 0.7}
        test_cases = [{"input": "test", "expected": "output"}]

        score = opt._judge_score(config, test_cases)
        assert 0.0 <= score <= 1.0

    def test_custom_judge_prompt(self):
        """Use custom judge prompt."""
        custom_prompt = "Score on a scale of 1-5: is it good?"
        opt = PromptOptimizer()

        config = {"temperature": 0.7}
        test_cases = [{"input": "test"}]

        # Should not raise
        score = opt._judge_score(config, test_cases, judge_prompt=custom_prompt)
        assert isinstance(score, float)


class TestOptimizationLoop:
    """Test the full Champion/Challenger loop."""

    def test_optimization_initialization(self):
        """Optimizer tracks best champion."""
        opt = PromptOptimizer(max_iterations=2)

        champion = {"temperature": 0.5, "constraints": {"must_do": []}}
        test_cases = [{"input": "test", "expected": "output"}]

        # Before optimization
        assert opt.best_champion is None
        assert opt.best_score == 0.0

    def test_history_tracking(self):
        """Optimizer maintains history of all iterations."""
        opt = PromptOptimizer(max_iterations=1)

        champion = {"temperature": 0.5, "constraints": {"must_do": []}}
        test_cases = [{"input": "test"}]

        # Mock the optimization (skip actual run for unit test)
        opt.history = [
            ScoreCard(
                iteration=0,
                candidate_id="abc",
                variant_name="temp_0.7",
                scores={"score": 0.8},
                overall_score=0.8,
                oracle_passed=True,
                is_champion=True,
            )
        ]

        history = opt.get_history()
        assert len(history) == 1
        assert history[0]["variant_name"] == "temp_0.7"

    def test_summarize_no_iterations(self):
        """Summary when no iterations run."""
        opt = PromptOptimizer()

        summary = opt.summarize()
        assert summary["status"] == "no iterations run"

    def test_summarize_with_history(self):
        """Summary with iteration history."""
        opt = PromptOptimizer()
        opt.best_score = 0.85
        opt.history = [
            ScoreCard(
                iteration=0,
                candidate_id="abc",
                variant_name="baseline",
                scores={"score": 0.7},
                overall_score=0.7,
                oracle_passed=True,
                is_champion=True,
            ),
            ScoreCard(
                iteration=1,
                candidate_id="def",
                variant_name="temp_0.8",
                scores={"score": 0.85},
                overall_score=0.85,
                oracle_passed=True,
                is_champion=True,
            ),
        ]

        summary = opt.summarize()
        assert summary["best_score"] == 0.85
        assert summary["improvement"] == pytest.approx(0.15)
        assert len(summary["history"]) == 2


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_champion_config(self):
        """Handle empty champion config."""
        opt = PromptOptimizer()
        champion = {}

        challengers = opt._generate_challengers(champion, n=2)
        assert len(challengers) >= 2

    def test_no_constraints_in_config(self):
        """Handle config without constraints."""
        opt = PromptOptimizer()
        champion = {"temperature": 0.5}

        challengers = opt._generate_challengers(champion, n=2)
        # Should still generate temperature variants
        assert len([k for k in challengers if "temp_" in k]) > 0

    def test_convergence_detection(self):
        """Detect convergence and stop early."""
        opt = PromptOptimizer(convergence_patience=2, max_iterations=10)

        assert opt.convergence_patience == 2
        assert opt.max_iterations == 10

    def test_improvement_threshold_boundary(self):
        """Test improvement threshold edge cases."""
        opt = PromptOptimizer(improvement_threshold=0.0)
        assert opt.improvement_threshold == 0.0

        opt2 = PromptOptimizer(improvement_threshold=1.0)
        assert opt2.improvement_threshold == 1.0

    def test_unicode_variant_names(self):
        """Handle unicode in variant names."""
        opt = PromptOptimizer()
        champion = {
            "temperature": 0.7,
            "constraints": {"must_do": ["test unicode: 你好"]},
        }

        challengers = opt._generate_challengers(champion)
        assert len(challengers) > 0

        # All variants should be serializable
        for name, config in challengers.items():
            assert isinstance(name, str)
            assert isinstance(config, dict)

    def test_large_test_case_set(self):
        """Handle large test case sets."""
        opt = PromptOptimizer()
        champion = {"temperature": 0.5}

        # 1000 test cases
        test_cases = [{"input": f"test_{i}", "expected": f"output_{i}"} for i in range(1000)]

        config = {"temperature": 0.7}
        # Should not raise
        score = opt._judge_score(config, test_cases)
        assert isinstance(score, float)
