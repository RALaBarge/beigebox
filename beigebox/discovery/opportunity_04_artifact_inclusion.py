"""
Opportunity #4: Artifact Inclusion & Decay

Hypothesis: Recent code/text artifacts boost context quality; old artifacts and
binary/screenshot references create noise. Type-selective inclusion
(code yes, screenshots no) improves accuracy.

Expected impact: +5-10% (MEDIUM confidence)

Variants
--------
- baseline_all:       All artifacts included (current)
- code_only:          Include only code/text artifacts, skip screenshots/binaries
- recent_only:        Include only artifacts from the last 15 minutes
- typed_decay:        Code artifacts: full; screenshots: summarise; old: drop
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

_FACTS = [
    "[type=code age=2min] def forward_request(req): return proxy.route(req)",
    "[type=screenshot age=1min] Screenshot: dashboard showing P95=245ms",
    "[type=code age=5min] class Proxy: def __init__(self, cfg): self.cfg = cfg",
    "[type=screenshot age=60min] Screenshot: old Grafana graph from last week",
    "[type=text age=3min] Error log: KeyError 'model' in proxy.py line 312",
    "[type=binary age=30min] Binary: compiled wasm module opener_strip.wasm (140KB)",
    "[type=code age=1min] FIXED: Added model fallback at proxy.py:312",
    "[type=text age=45min] Meeting notes: decided to use browserless/chrome for CDP",
]


def _parse_artifact_meta(content: str) -> dict:
    import re
    meta = {}
    m = re.search(r'\[type=(\w+)\s+age=(\d+)min\]', content)
    if m:
        meta["type"] = m.group(1)
        meta["age_min"] = int(m.group(2))
    return meta


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


class ArtifactInclusionExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "artifact_inclusion"
    OPPORTUNITY_NAME = "Artifact Inclusion & Decay (#4)"
    HYPOTHESIS = "Type-selective and recency-filtered artifact inclusion improves context quality"
    EXPECTED_IMPACT = "+5-10% accuracy"
    WEIGHT_PROFILE = "code"

    VARIANTS = [
        {"name": "baseline_all",     "artifact_strategy": "all"},
        {"name": "code_only",        "artifact_strategy": "code_only"},
        {"name": "recent_only",      "artifact_strategy": "recent_only", "max_age_min": 15},
        {"name": "typed_decay",      "artifact_strategy": "typed_decay"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("artifact_strategy", "all")
        system, context, question = _get_body_turns(messages)

        if strategy == "all" or not context:
            return messages

        filtered = []
        for msg in context:
            content = msg.get("content", "")
            meta = _parse_artifact_meta(content)
            art_type = meta.get("type", "text")
            age = meta.get("age_min", 0)

            if strategy == "code_only":
                if art_type in ("code", "text"):
                    filtered.append(msg)

            elif strategy == "recent_only":
                max_age = variant_config.get("max_age_min", 15)
                if age <= max_age:
                    filtered.append(msg)

            elif strategy == "typed_decay":
                if art_type == "code":
                    filtered.append(msg)  # always include
                elif art_type == "text" and age <= 30:
                    filtered.append(msg)  # include recent text
                elif art_type == "screenshot" and age <= 5:
                    # Summarize: strip screenshot blob, keep caption only
                    short = content.split("]", 1)[-1].strip()[:80]
                    filtered.append({**msg, "content": f"[screenshot summary] {short}"})
                # binary and old screenshots dropped

        if not filtered:
            filtered = context[-2:]

        return system + filtered + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        return [
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What was the bug fix made recently?",
                expected="KeyError",
                task_type="recent_code_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What is the Proxy class constructor signature?",
                expected="cfg",
                task_type="code_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What does the forward_request function do?",
                expected="proxy.route",
                task_type="code_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What error was reported in the error log?",
                expected="KeyError",
                task_type="text_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What does the dashboard screenshot show?",
                expected="245ms",
                task_type="screenshot_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What was decided in the meeting notes?",
                expected="CDP",
                task_type="old_text_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="What line was the bug at?",
                expected="312",
                task_type="code_text_recall",
            ),
            DiscoveryTestCase(
                context_facts=_FACTS,
                question="Summarize the recent code changes.",
                expected="fixed",
                task_type="synthesis",
            ),
        ]
