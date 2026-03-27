"""
Base class / protocol for discovery opportunity modules.

Every opportunity implements:
  - OPPORTUNITY_ID   — stable slug used in API and SQLite
  - VARIANTS         — list of variant configs
  - transform()      — modify a message list to apply the variant's strategy
  - test_cases()     — return list[DiscoveryTestCase] for this opportunity

A DiscoveryTestCase carries:
  - context_facts    — sentences/facts to inject as prior conversation turns
  - question         — user turn to ask after context injection
  - expected         — substring that should appear in a correct answer
  - task_type        — simple | complex | recall | synthesis | etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiscoveryTestCase:
    """
    A single test case for a discovery experiment.

    context_facts are injected as synthetic prior conversation turns before the
    question is asked.  The variant's transform() rearranges / compresses /
    otherwise modifies those turns.
    """

    question: str
    expected: str                           # substring that must appear in a correct response
    context_facts: list[str] = field(default_factory=list)
    task_type: str = "general"              # simple | complex | recall | synthesis | ...
    meta: dict[str, Any] = field(default_factory=dict)


class DiscoveryOpportunity:
    """
    Abstract base for all discovery experiments.

    Subclasses set class attributes and override transform() + test_cases().
    """

    OPPORTUNITY_ID: str = "unknown"
    OPPORTUNITY_NAME: str = "Unknown opportunity"
    HYPOTHESIS: str = ""
    EXPECTED_IMPACT: str = ""
    WEIGHT_PROFILE: str = "general"         # general | code | reasoning | safety

    # Variant configs — list of dicts with at minimum {"name": str}
    VARIANTS: list[dict[str, Any]] = []

    # ── Subclass interface ─────────────────────────────────────────────────

    def transform(
        self,
        messages: list[dict[str, str]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, str]]:
        """
        Apply this variant's context transformation to an OpenAI-style message list.

        Parameters
        ----------
        messages:
            The message list built by the runner from context_facts + question.
            Format: [{"role": "system"|"user"|"assistant", "content": str}, ...]
        variant_config:
            The variant's config dict (one entry from VARIANTS).

        Returns
        -------
        Transformed message list (may reorder, compress, or modify entries).
        The last message is always the user question — do not remove it.
        """
        return messages  # baseline: identity transform

    def test_cases(self) -> list[DiscoveryTestCase]:
        """Return test cases for this opportunity."""
        return []

    # ── Framework helpers (do not override) ───────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Export experiment metadata for API / logging."""
        return {
            "opportunity_id": self.OPPORTUNITY_ID,
            "opportunity_name": self.OPPORTUNITY_NAME,
            "hypothesis": self.HYPOTHESIS,
            "expected_impact": self.EXPECTED_IMPACT,
            "variants": self.VARIANTS,
            "weight_profile": self.WEIGHT_PROFILE,
            "n_test_cases": len(self.test_cases()),
        }
