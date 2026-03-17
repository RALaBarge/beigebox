"""
Eval data models — EvalCase, EvalResult, EvalSuite.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    input: str                              # User message sent to the proxy
    scorer: str = "contains"               # contains | exact | regex | not_contains | llm_judge
    expect: dict = field(default_factory=dict)  # scorer-specific criteria
    model: str = ""                         # Override model for this case (falls back to suite default)
    system: str = ""                        # Optional system prompt
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    case_id: str
    input: str
    output: str
    passed: bool
    score: float                            # 0.0–1.0
    scorer: str
    model: str
    latency_ms: float
    run_id: str
    reason: str = ""                        # Why it passed/failed
    error: str = ""                         # Exception message if the call itself failed


@dataclass
class EvalSuite:
    name: str
    cases: list[EvalCase]
    model: str = ""                         # Default model for all cases
    base_url: str = ""                      # Override proxy URL (defaults to config server.port)
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
