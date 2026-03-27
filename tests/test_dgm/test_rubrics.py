"""
Tests for DGM rubric rotation (Goodhart's Law mitigation).

These tests verify:
- Rubrics rotate at the correct interval
- Rotation is deterministic
- The rubric bank covers distinct focus areas (so rotating actually changes what's measured)
- State serialisation round-trips correctly
"""
import pytest
from beigebox.dgm.rubrics import (
    RUBRIC_BANK,
    RubricRotator,
    DEFAULT_ROTATION_INTERVAL,
)


class TestRubricBank:
    def test_bank_has_enough_rubrics(self):
        """Need at least 4 rubrics for rotation to be meaningful."""
        assert len(RUBRIC_BANK) >= 4

    def test_all_rubrics_have_required_fields(self):
        for rubric in RUBRIC_BANK:
            assert rubric.name, "rubric.name must not be empty"
            assert rubric.focus, "rubric.focus must not be empty"
            assert len(rubric.description) > 20, "rubric.description too short"

    def test_rubric_names_unique(self):
        names = [r.name for r in RUBRIC_BANK]
        assert len(names) == len(set(names)), "Rubric names must be unique"

    def test_rubric_focuses_distinct(self):
        """If two rubrics have the same focus, rotation doesn't help much."""
        focuses = [r.focus for r in RUBRIC_BANK]
        unique_focuses = set(focuses)
        # Allow some overlap but not all the same
        assert len(unique_focuses) >= len(RUBRIC_BANK) * 0.6, (
            "Too many rubrics share the same focus — rotation won't diversify evaluation"
        )


class TestRubricRotator:
    def test_initial_state(self):
        rotator = RubricRotator(rotation_interval=5)
        assert rotator.iteration == 0
        assert rotator.current_index == 0
        assert rotator.current() == RUBRIC_BANK[0]

    def test_no_rotation_before_interval(self):
        rotator = RubricRotator(rotation_interval=5)
        for _ in range(4):
            rotated = rotator.tick()
            assert not rotated, "Should not rotate before interval"
        assert rotator.current_index == 0

    def test_rotates_at_interval(self):
        rotator = RubricRotator(rotation_interval=5)
        for _ in range(5):
            rotator.tick()
        assert rotator.current_index == 1
        assert rotator.current() == RUBRIC_BANK[1]

    def test_wraps_around_bank(self):
        """After going through all rubrics, should wrap to index 0."""
        n = len(RUBRIC_BANK)
        rotator = RubricRotator(rotation_interval=1)
        for _ in range(n):
            rotator.tick()
        assert rotator.current_index == 0

    def test_tick_returns_true_only_on_rotation(self):
        rotator = RubricRotator(rotation_interval=3)
        results = [rotator.tick() for _ in range(9)]
        # Should rotate at ticks 3, 6, 9 (indices 2, 5, 8)
        assert results == [False, False, True, False, False, True, False, False, True]

    def test_custom_start_index(self):
        rotator = RubricRotator(rotation_interval=5, start_index=2)
        assert rotator.current_index == 2
        assert rotator.current() == RUBRIC_BANK[2]

    def test_start_index_wraps(self):
        n = len(RUBRIC_BANK)
        rotator = RubricRotator(start_index=n + 1)
        assert rotator.current_index == 1

    def test_to_dict_has_required_keys(self):
        rotator = RubricRotator(rotation_interval=5)
        d = rotator.to_dict()
        for key in ("rubric_name", "rubric_focus", "rubric_index", "iteration",
                    "rotation_interval", "next_rotation_at"):
            assert key in d, f"Missing key: {key}"

    def test_next_rotation_at_correct(self):
        rotator = RubricRotator(rotation_interval=5)
        # At iteration 0: next rotation at 5
        assert rotator.to_dict()["next_rotation_at"] == 5
        rotator.tick()
        rotator.tick()
        # At iteration 2: next rotation at 5
        assert rotator.to_dict()["next_rotation_at"] == 3

    def test_minimum_interval_is_1(self):
        """Rotation interval of 0 or negative should be clamped to 1."""
        rotator = RubricRotator(rotation_interval=0)
        assert rotator.rotation_interval == 1

    def test_iteration_counter_increments(self):
        rotator = RubricRotator(rotation_interval=5)
        for i in range(7):
            rotator.tick()
        assert rotator.iteration == 7

    def test_deterministic_given_same_start(self):
        """Two rotators with same params should produce identical sequences."""
        r1 = RubricRotator(rotation_interval=3, start_index=1)
        r2 = RubricRotator(rotation_interval=3, start_index=1)
        for _ in range(20):
            r1.tick()
            r2.tick()
        assert r1.current_index == r2.current_index
        assert r1.iteration == r2.iteration
