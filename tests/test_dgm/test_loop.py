"""
Tests for the DGM main loop.

These tests mock out all I/O (LLM calls, filesystem, HTTP) and verify:
- Improvements are kept
- Regressions are reverted
- Early stopping on patience exhaustion
- Rubric rotation occurs at correct intervals
- Tap events are emitted at the right moments
- History is correctly recorded
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from beigebox.dgm.loop import DGMLoop, DGMRunResult, Probe, _FALLBACK_PROBES
from beigebox.dgm.judge import DGMJudge, JudgeVerdict
from beigebox.dgm.patcher import ConfigPatcher, PatchResult, Patch
from beigebox.dgm.proposer import DGMProposer, Proposal
from beigebox.dgm.rubrics import RubricRotator, RUBRIC_BANK


# ── Helpers ────────────────────────────────────────────────────────────────

def make_verdict(winner: str, confidence: float = 0.8) -> JudgeVerdict:
    return JudgeVerdict(
        winner=winner,
        confidence=confidence,
        reasoning="test",
        rubric_name="helpfulness",
        latency_ms=10.0,
    )


def make_proposal(key: str = "models.default", value: str = "qwen3:4b") -> Proposal:
    return Proposal(
        patch=Patch(key=key, value=value, reasoning="test reason"),
        file_patch=None,
        raw_response='{"key": "models.default", "value": "qwen3:4b"}',
        latency_ms=50.0,
    )


def make_loop(
    verdict: JudgeVerdict | None = None,
    proposal: Proposal | None = None,
    patch_ok: bool = True,
    n_probes: int = 1,
    confidence_threshold: float = 0.65,
    patience: int = 3,
    rotation_interval: int = 5,
) -> DGMLoop:
    """Build a DGMLoop with mocked judge, proposer, and patcher."""
    judge = AsyncMock(spec=DGMJudge)
    judge.compare.return_value = verdict or make_verdict("B", 0.9)

    proposer = AsyncMock(spec=DGMProposer)
    proposer.propose.return_value = proposal or make_proposal()

    patcher = MagicMock(spec=ConfigPatcher)
    patcher.apply.return_value = PatchResult(ok=patch_ok, original="old_model")
    patcher.revert.return_value = True

    rotator = RubricRotator(rotation_interval=rotation_interval)

    loop = DGMLoop(
        judge=judge,
        proposer=proposer,
        patcher=patcher,
        rotator=rotator,
        n_probes=n_probes,
        confidence_threshold=confidence_threshold,
        patience=patience,
    )

    # Stub out HTTP probe calls
    loop._run_probes = AsyncMock(return_value=["new response"] * n_probes)
    loop._sample_probes = AsyncMock(return_value=_FALLBACK_PROBES[:n_probes])

    return loop


# ── Tests ──────────────────────────────────────────────────────────────────

class TestKeepImprovement:
    @pytest.mark.asyncio
    async def test_b_wins_change_is_kept(self):
        loop = make_loop(verdict=make_verdict("B", 0.9))
        result = await loop.run(n_iterations=1)

        assert result.iterations_kept == 1
        assert result.history[0].kept is True
        loop._patcher.revert.assert_not_called()

    @pytest.mark.asyncio
    async def test_kept_change_in_history(self):
        loop = make_loop(verdict=make_verdict("B", 0.9))
        result = await loop.run(n_iterations=1)

        h = result.history[0]
        assert h.kept is True
        assert h.key == "models.default"
        assert h.new_value == "qwen3:4b"
        assert h.old_value == "old_model"


class TestRevertRegression:
    @pytest.mark.asyncio
    async def test_a_wins_change_is_reverted(self):
        loop = make_loop(verdict=make_verdict("A", 0.9))
        result = await loop.run(n_iterations=1)

        assert result.iterations_kept == 0
        loop._patcher.revert.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_confidence_b_win_is_reverted(self):
        """B wins but confidence below threshold — should revert."""
        loop = make_loop(
            verdict=make_verdict("B", 0.4),
            confidence_threshold=0.65,
        )
        result = await loop.run(n_iterations=1)

        assert result.iterations_kept == 0
        loop._patcher.revert.assert_called_once()

    @pytest.mark.asyncio
    async def test_tie_is_reverted(self):
        loop = make_loop(verdict=make_verdict("tie", 0.5))
        result = await loop.run(n_iterations=1)

        assert result.iterations_kept == 0
        loop._patcher.revert.assert_called_once()


class TestPatchFailure:
    @pytest.mark.asyncio
    async def test_failed_patch_not_counted(self):
        """If patch.apply() fails, iteration is skipped cleanly."""
        loop = make_loop(patch_ok=False)
        result = await loop.run(n_iterations=1)

        assert result.iterations_kept == 0
        # Should not try to run probes since patch failed
        loop._run_probes.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_revert_on_failed_apply(self):
        """If apply() fails, we should NOT try to revert."""
        loop = make_loop(patch_ok=False)
        await loop.run(n_iterations=1)
        loop._patcher.revert.assert_not_called()


class TestEarlyStopping:
    @pytest.mark.asyncio
    async def test_stops_after_patience_exhausted(self):
        """Should stop early after N consecutive non-improvements."""
        loop = make_loop(
            verdict=make_verdict("A", 0.9),  # always reverts
            patience=3,
        )
        result = await loop.run(n_iterations=20)

        # Should stop after 3 consecutive regressions
        assert result.stopped_early is True
        assert "patience" in result.stop_reason
        assert result.iterations_run <= 4  # tolerance: the actual stop is after the 3rd failure

    @pytest.mark.asyncio
    async def test_patience_resets_on_improvement(self):
        """Streak should reset when an improvement is found."""
        call_count = 0

        async def alternating_compare(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Regress twice, then improve
            if call_count % 3 == 0:
                return make_verdict("B", 0.9)
            return make_verdict("A", 0.9)

        loop = make_loop(patience=3)
        loop._judge.compare.side_effect = alternating_compare

        result = await loop.run(n_iterations=9)

        # Should not stop early: each 3rd iteration resets the patience counter
        assert not result.stopped_early

    @pytest.mark.asyncio
    async def test_no_proposals_count_toward_patience(self):
        """Failed proposals (proposer returns None) increment the no-improvement streak."""
        loop = make_loop(patience=3)
        loop._proposer.propose.return_value = None  # proposer always fails

        result = await loop.run(n_iterations=10)

        assert result.stopped_early is True
        # No history since no proposals succeeded
        assert result.iterations_run == 0


class TestRubricRotation:
    @pytest.mark.asyncio
    async def test_rotation_happens_at_interval(self):
        """Check that the rubric changes after rotation_interval iterations."""
        loop = make_loop(
            verdict=make_verdict("B", 0.9),
            rotation_interval=3,
            patience=100,
        )
        await loop.run(n_iterations=3)

        # After 3 iterations, should have rotated once
        assert loop._rotator.current_index == 1

    @pytest.mark.asyncio
    async def test_rubric_name_recorded_in_history(self):
        loop = make_loop(verdict=make_verdict("B", 0.9))
        result = await loop.run(n_iterations=2)

        for h in result.history:
            assert h.rubric_name, "rubric_name should be non-empty in history"


class TestRunResult:
    @pytest.mark.asyncio
    async def test_run_result_fields(self):
        loop = make_loop(verdict=make_verdict("B", 0.9))
        result = await loop.run(n_iterations=3)

        assert result.run_id
        assert result.iterations_run == 3
        assert result.iterations_kept == 3
        assert result.keep_rate == pytest.approx(1.0)
        assert result.total_ms > 0

    @pytest.mark.asyncio
    async def test_to_dict_serialisable(self):
        import json
        loop = make_loop(verdict=make_verdict("B", 0.9))
        result = await loop.run(n_iterations=1)

        # Should not raise
        d = result.to_dict()
        json.dumps(d)  # Must be JSON-serialisable

    @pytest.mark.asyncio
    async def test_zero_iterations_ok(self):
        loop = make_loop()
        result = await loop.run(n_iterations=0)

        assert result.iterations_run == 0
        assert result.iterations_kept == 0
        assert not result.stopped_early


class TestProbeHandling:
    @pytest.mark.asyncio
    async def test_uses_fallback_probes_when_no_history(self):
        loop = make_loop(n_probes=2)
        loop._sample_probes.return_value = _FALLBACK_PROBES[:2]

        await loop.run(n_iterations=1)

        loop._run_probes.assert_called_once()
        probes_used = loop._run_probes.call_args[0][0]
        assert len(probes_used) == 2

    @pytest.mark.asyncio
    async def test_skips_iteration_when_no_probes(self):
        """If no probes available, iteration should be skipped cleanly."""
        loop = make_loop()
        loop._sample_probes.return_value = []

        result = await loop.run(n_iterations=3)

        # No iterations completed since no probes
        assert result.iterations_run == 0
