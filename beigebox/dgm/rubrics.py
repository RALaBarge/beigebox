"""
DGM Rubric Bank — Goodhart's Law mitigation via rotating evaluation criteria.

The core problem: if you optimise toward a single metric, the system learns to
game that metric rather than improve the underlying quality it's meant to measure.

Mitigation: maintain a bank of N rubrics that all describe the same underlying
outcome ("good response") but from different angles. Rotate the active rubric
every ROTATION_INTERVAL iterations. Since the system can't predict which rubric
is next, it can't specialise toward any single one — it has to improve across
all of them.

Each rubric is:
  - A short name used in logging
  - A description passed verbatim to the judge LLM
  - A focus area so we can track which dimension was active

All rubrics are pairwise: "which of A or B is better?" is easier for a small
model to answer reliably than absolute scoring.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Rubric:
    """A single evaluation rubric."""

    name: str
    focus: str          # one-word category for logging
    description: str    # verbatim prompt fragment given to the judge


# ── Rubric bank ────────────────────────────────────────────────────────────
# These all describe "good response quality" from different angles.
# The judge is given exactly one of these per evaluation run.

RUBRIC_BANK: list[Rubric] = [
    Rubric(
        name="helpfulness",
        focus="helpfulness",
        description=(
            "Judge which response is more genuinely helpful for the user's actual goal. "
            "A helpful response solves the problem, answers the question, or moves the "
            "user forward — not one that sounds helpful but leaves them stuck."
        ),
    ),
    Rubric(
        name="accuracy",
        focus="accuracy",
        description=(
            "Judge which response is more factually accurate and technically correct. "
            "Prefer the response with fewer errors, false claims, or misleading statements. "
            "If both are equally accurate, choose the one with higher confidence calibration."
        ),
    ),
    Rubric(
        name="conciseness",
        focus="conciseness",
        description=(
            "Judge which response is more concise without sacrificing substance. "
            "Penalise unnecessary preamble, filler phrases like 'Certainly!', restating "
            "the question, and padding. Reward getting to the point efficiently."
        ),
    ),
    Rubric(
        name="directness",
        focus="directness",
        description=(
            "Judge which response more directly addresses the specific question asked. "
            "The best response opens with the answer or action, not with context-setting "
            "or hedging. It does not make the user read past filler to find the substance."
        ),
    ),
    Rubric(
        name="completeness",
        focus="completeness",
        description=(
            "Judge which response more completely covers all important aspects of the "
            "request. A complete response does not leave obvious follow-up questions "
            "unanswered, omit important caveats, or miss key parts of a multi-part query."
        ),
    ),
    Rubric(
        name="expert_quality",
        focus="expert",
        description=(
            "Judge which response a domain expert would rate higher. Consider: depth of "
            "understanding shown, appropriate use of terminology, awareness of edge cases, "
            "and whether the response reflects genuine knowledge rather than surface-level "
            "pattern matching."
        ),
    ),
    Rubric(
        name="clarity",
        focus="clarity",
        description=(
            "Judge which response is clearer and easier to understand for the target "
            "audience implied by the question. Consider: logical flow, appropriate "
            "structure (lists vs prose), vocabulary matching the user's apparent level, "
            "and avoidance of unexplained jargon."
        ),
    ),
    Rubric(
        name="actionability",
        focus="action",
        description=(
            "Judge which response gives the user something concrete they can act on. "
            "Prefer responses with specific steps, commands, code, or decisions over "
            "vague recommendations. A response that ends with 'it depends' without "
            "explaining when and how is less actionable."
        ),
    ),
]

# Default rotation: change rubric every N iterations
DEFAULT_ROTATION_INTERVAL = 5


class RubricRotator:
    """
    Manages the active rubric and rotation schedule.

    Keeps a cursor into RUBRIC_BANK that advances every rotation_interval
    iterations. The rotation is deterministic given a starting iteration count,
    so runs can be replayed and compared.

    Usage:
        rotator = RubricRotator(rotation_interval=5)
        rubric = rotator.current()           # active rubric
        rotator.tick()                        # increment iteration count
        rotator.should_rotate(iteration=10)  # True if it's time to rotate
    """

    def __init__(
        self,
        rotation_interval: int = DEFAULT_ROTATION_INTERVAL,
        start_index: int = 0,
    ) -> None:
        """
        Args:
            rotation_interval: Number of iterations between rubric changes.
            start_index: Which rubric to start from (0-based index into RUBRIC_BANK).
        """
        self._rotation_interval = max(1, rotation_interval)
        self._index = start_index % len(RUBRIC_BANK)
        self._iteration = 0

    @property
    def rotation_interval(self) -> int:
        return self._rotation_interval

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def iteration(self) -> int:
        return self._iteration

    def current(self) -> Rubric:
        """Return the active rubric."""
        return RUBRIC_BANK[self._index]

    def tick(self) -> bool:
        """
        Advance the iteration counter. Rotate the rubric if the interval is reached.

        Returns:
            True if a rotation occurred this tick, False otherwise.
        """
        self._iteration += 1
        if self._iteration % self._rotation_interval == 0:
            old = self._index
            self._index = (self._index + 1) % len(RUBRIC_BANK)
            logger.info(
                "dgm.rubric_rotated iteration=%d old=%s new=%s",
                self._iteration,
                RUBRIC_BANK[old].name,
                RUBRIC_BANK[self._index].name,
            )
            return True
        return False

    def to_dict(self) -> dict:
        """Serialise rotator state for logging and persistence."""
        rubric = self.current()
        return {
            "rubric_name": rubric.name,
            "rubric_focus": rubric.focus,
            "rubric_index": self._index,
            "iteration": self._iteration,
            "rotation_interval": self._rotation_interval,
            # Iterations remaining until the next rotation (relative, not absolute)
            "next_rotation_at": (
                self._rotation_interval - self._iteration % self._rotation_interval
            ),
        }
