"""
Discovery experiments for context optimization opportunities.

Each opportunity is a self-contained experiment module that can be run
via the discovery framework to evaluate variants and find the Pareto frontier.

Opportunities implemented:
- #6: Position Sensitivity (Needle-in-Haystack)
- #7: Context Compression Strategy
- #8: Context Depth vs. Breadth
- #9: Prompt Caching Integration
"""

from beigebox.discovery.runner import DiscoveryRunner
from beigebox.discovery.opportunity_06_position_sensitivity import (
    PositionSensitivityExperiment,
)
from beigebox.discovery.opportunity_07_context_compression import (
    ContextCompressionExperiment,
)
from beigebox.discovery.opportunity_08_depth_vs_breadth import (
    DepthVsBreadthExperiment,
)
from beigebox.discovery.opportunity_09_prompt_caching import (
    PromptCachingExperiment,
    PromptCachingVariant,
)

__all__ = [
    "DiscoveryRunner",
    "PositionSensitivityExperiment",
    "ContextCompressionExperiment",
    "DepthVsBreadthExperiment",
    "PromptCachingExperiment",
    "PromptCachingVariant",
]
