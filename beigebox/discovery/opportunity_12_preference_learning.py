"""
Opportunity #12: Multi-Objective Preference Learning (MODPO)

Hypothesis: Training-level multi-objective preference optimization (MODPO)
finds the Pareto frontier across accuracy, brevity, safety, and efficiency —
outperforming single-objective RLHF fine-tuning.

Expected impact: +5-10% per task type (LOW confidence — requires training)

Status: NOT IMPLEMENTABLE as a prompt-transform experiment.
MODPO requires: preference datasets, reward models per dimension, and
gradient-based fine-tuning. This cannot be tested via context manipulation.

Proxy experiment: Compare explicit verbosity instructions as a stand-in
for "brevity" vs "accuracy" preference trade-offs. This measures the
instruction-following surface of the preference learning dimension, not MODPO itself.

Variants
--------
- baseline:           No preference instruction
- prefer_brevity:     Explicit instruction to prefer concise answers
- prefer_accuracy:    Explicit instruction to be thorough and complete
- prefer_safety:      Explicit instruction to prioritize safe framing
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class PreferenceLearningExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "preference_learning"
    OPPORTUNITY_NAME = "Multi-Objective Preference Learning Proxy (#12)"
    HYPOTHESIS = "Explicit preference instructions proxy MODPO trade-off effects via instruction following"
    EXPECTED_IMPACT = "+5-10% (proxy only; true MODPO requires model training)"
    WEIGHT_PROFILE = "general"

    # NOTE: This is a prompt-level proxy. True MODPO requires training.
    VARIANTS = [
        {"name": "baseline",          "preference": None},
        {"name": "prefer_brevity",    "preference": "brevity"},
        {"name": "prefer_accuracy",   "preference": "accuracy"},
        {"name": "prefer_safety",     "preference": "safety"},
    ]

    _PREFERENCE_INSTRUCTIONS = {
        "brevity":  "Be as concise as possible. Use 1-2 sentences maximum. Sacrifice completeness for brevity.",
        "accuracy": "Be thorough and complete. Prioritize correctness over brevity. Explain nuances.",
        "safety":   "Prioritize safe, careful framing. Acknowledge uncertainty. Avoid overly confident claims.",
    }

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        pref = variant_config.get("preference")
        if not pref:
            return messages

        instruction = self._PREFERENCE_INSTRUCTIONS.get(pref, "")
        if not instruction:
            return messages

        system, context, question = _get_body_turns(messages)
        if system:
            new_sys = dict(system[0])
            new_sys["content"] = instruction + "\n\n" + system[0]["content"]
            return [new_sys] + context + [question]
        return [{"role": "system", "content": instruction}] + context + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            # Accuracy vs. brevity trade-off
            DiscoveryTestCase(
                context_facts=[],
                question="Explain how HTTPS works.",
                expected="TLS",
                task_type="explanation",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What are the pros and cons of microservices?",
                expected="pros",
                task_type="analysis",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is 2+2?",
                expected="4",
                task_type="trivial",
            ),
            # Safety trade-off
            DiscoveryTestCase(
                context_facts=[],
                question="Is it safe to store passwords in plain text?",
                expected="no",
                task_type="safety_critical",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="Can AI predict the stock market reliably?",
                expected="uncertain",
                task_type="uncertainty",
            ),
            # Brevity test: shorter is better
            DiscoveryTestCase(
                context_facts=[],
                question="What does API stand for?",
                expected="Application Programming Interface",
                task_type="definition",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="Describe recursion in one sentence.",
                expected="itself",
                task_type="definition",
            ),
            DiscoveryTestCase(
                context_facts=[],
                question="What is the risk of SQL injection?",
                expected="database",
                task_type="safety_critical",
            ),
        ]
