"""
Discovery experiments for context optimization opportunities.

Each opportunity is a self-contained experiment module that can be run
via the discovery framework to evaluate variants and find the Pareto frontier.
"""

from beigebox.discovery.opportunity_09_prompt_caching import (
    PromptCachingExperiment,
    PromptCachingVariant,
)

__all__ = [
    "PromptCachingExperiment",
    "PromptCachingVariant",
]
