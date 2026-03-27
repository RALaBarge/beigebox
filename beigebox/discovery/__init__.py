"""
Discovery experiments for context optimization opportunities.

All 15 opportunities are implemented as DiscoveryOpportunity subclasses.
Each provides:
  - VARIANTS: list of variant configs
  - transform(messages, variant_config): context manipulation function
  - test_cases(): list[DiscoveryTestCase]

Run via API: POST /api/v1/discovery/run
Run via CLI: beigebox experiment --opportunity <id>
"""

from beigebox.discovery.runner import DiscoveryRunner
from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

from beigebox.discovery.opportunity_01_composition_strategy import CompositionStrategyExperiment
from beigebox.discovery.opportunity_02_fact_freshness import FactFreshnessExperiment
from beigebox.discovery.opportunity_03_dialogue_relevance import DialogueRelevanceExperiment
from beigebox.discovery.opportunity_04_artifact_inclusion import ArtifactInclusionExperiment
from beigebox.discovery.opportunity_05_context_rot import ContextRotExperiment
from beigebox.discovery.opportunity_06_position_sensitivity import PositionSensitivityExperiment
from beigebox.discovery.opportunity_07_context_compression import ContextCompressionExperiment
from beigebox.discovery.opportunity_08_depth_vs_breadth import DepthVsBreadthExperiment
from beigebox.discovery.opportunity_09_prompt_caching import PromptCachingExperiment
from beigebox.discovery.opportunity_10_fewshot_diversity import FewShotDiversityExperiment
from beigebox.discovery.opportunity_11_instruction_vs_fewshot import InstructionVsFewShotExperiment
from beigebox.discovery.opportunity_12_preference_learning import PreferenceLearningExperiment
from beigebox.discovery.opportunity_13_source_reputation import SourceReputationExperiment
from beigebox.discovery.opportunity_14_interleaving import InterleavingExperiment
from beigebox.discovery.opportunity_15_needle_length import NeedleLengthExperiment

# Registry: opportunity_id → class
OPPORTUNITY_REGISTRY: dict[str, type[DiscoveryOpportunity]] = {
    cls.OPPORTUNITY_ID: cls
    for cls in [
        CompositionStrategyExperiment,
        FactFreshnessExperiment,
        DialogueRelevanceExperiment,
        ArtifactInclusionExperiment,
        ContextRotExperiment,
        PositionSensitivityExperiment,
        ContextCompressionExperiment,
        DepthVsBreadthExperiment,
        PromptCachingExperiment,
        FewShotDiversityExperiment,
        InstructionVsFewShotExperiment,
        PreferenceLearningExperiment,
        SourceReputationExperiment,
        InterleavingExperiment,
        NeedleLengthExperiment,
    ]
}


def get_opportunity(opportunity_id: str) -> DiscoveryOpportunity | None:
    """Instantiate an opportunity by ID. Returns None if not found."""
    cls = OPPORTUNITY_REGISTRY.get(opportunity_id)
    return cls() if cls else None


def list_opportunities() -> list[dict]:
    """Return metadata for all registered opportunities."""
    return [cls().to_dict() for cls in OPPORTUNITY_REGISTRY.values()]


__all__ = [
    "DiscoveryRunner",
    "DiscoveryOpportunity",
    "DiscoveryTestCase",
    "OPPORTUNITY_REGISTRY",
    "get_opportunity",
    "list_opportunities",
    # Individual experiments
    "CompositionStrategyExperiment",
    "FactFreshnessExperiment",
    "DialogueRelevanceExperiment",
    "ArtifactInclusionExperiment",
    "ContextRotExperiment",
    "PositionSensitivityExperiment",
    "ContextCompressionExperiment",
    "DepthVsBreadthExperiment",
    "PromptCachingExperiment",
    "FewShotDiversityExperiment",
    "InstructionVsFewShotExperiment",
    "PreferenceLearningExperiment",
    "SourceReputationExperiment",
    "InterleavingExperiment",
    "NeedleLengthExperiment",
]
