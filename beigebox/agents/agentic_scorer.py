"""
Agentic Scorer — regex-based pre-filter for tool-calling / agentic intent.
Scores a prompt on [0.0, 1.0] based on pattern matches that suggest the
user wants the model to take action (tool use, web lookup, multi-step
planning, etc.) rather than just generate text.
Designed to run BEFORE the embedding classifier as a near-zero-cost
pre-filter. A high score can force a tool-capable route or skip the
classifier entirely.
Usage:
    from beigebox.agents.agentic_scorer import score_agentic_intent
    result = score_agentic_intent("Search the web for today's AI news")
    if result.score >= 0.6:
        # escalate to tool-capable route
"""
import re
import logging
from dataclasses import dataclass, field
logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Pattern registry
# Each entry: (compiled_pattern, weight, label)
# Weights are additive; final score is clamped to [0.0, 1.0].
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    # --- Tool-calling verbs ---
    (re.compile(r"\\b(search|look up|find|fetch|retrieve|get me)\\b", re.I), 0.25, "tool_verb"),
    (re.compile(r"\\b(browse|scrape|visit|open|navigate to)\\b", re.I),      0.25, "browse_verb"),
    (re.compile(r"\\b(calculate|compute|evaluate|solve)\\b", re.I),          0.20, "math_verb"),
    (re.compile(r"\\b(run|execute|call|invoke|trigger)\\b", re.I),           0.20, "exec_verb"),
    # --- Multi-step / planning language ---
    (re.compile(r"\\b(step by step|then|after that|finally|first .* then)\\b", re.I), 0.15, "multistep"),
    (re.compile(r"\\b(plan|outline|workflow|pipeline|sequence of)\\b", re.I), 0.15, "planning"),
    # --- Explicit tool references ---
    (re.compile(r"\\b(web search|wikipedia|google|news|weather|stock price)\\b", re.I), 0.30, "tool_ref"),
    (re.compile(r"\\b(current|latest|real-?time|today|right now|as of)\\b", re.I),      0.20, "recency"),
    # --- Agentic output expectations ---
    (re.compile(r"\\b(for me|on my behalf|automatically|go ahead and)\\b", re.I), 0.20, "delegation"),
    (re.compile(r"\\b(save|store|write to|create a file|update)\\b", re.I),       0.15, "persistence"),
    # --- Question forms that almost always need a tool ---
    (re.compile(r"\\bwhat('s| is) (the (current|latest|price|weather|time|date))\\b", re.I), 0.30, "factual_now"),
    (re.compile(r"\\bhow (much|many|long|far|fast) (is|are|does|do)\\b", re.I), 0.10, "quantitative"),
]
@dataclass
class AgenticScore:
    """Result of an agentic intent scoring pass."""
    score: float                        # 0.0 = pure generation, 1.0 = strongly agentic
    matched: list[str] = field(default_factory=list)   # labels of patterns that fired
    is_agentic: bool = False            # convenience: score >= threshold
def score_agentic_intent(text: str, threshold: float = 0.5) -> AgenticScore:
    """
    Score a prompt for agentic / tool-calling intent.
    Args:
        text:      The raw user message (before z: stripping).
        threshold: Score at or above which is_agentic=True. Default 0.5.
    Returns:
        AgenticScore with .score, .matched labels, and .is_agentic flag.
    """
    raw_score = 0.0
    matched: list[str] = []
    for pattern, weight, label in _PATTERNS:
        if pattern.search(text):
            raw_score += weight
            matched.append(label)
    score = min(raw_score, 1.0)
    result = AgenticScore(
        score=score,
        matched=matched,
        is_agentic=score >= threshold,
    )
    if matched:
        logger.debug("agentic_scorer: score=%.2f matched=%s", score, matched)
    return result
# ---------------------------------------------------------------------------
# Examples / smoke test  (python -m beigebox.agents.agentic_scorer)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    examples = [
        # (prompt, expected_is_agentic)
        (
            "Search the web for the latest AI safety news and summarize the top 3 results",
            True,   # tool_verb + tool_ref + recency + multistep -> ~0.90
        ),
        (
            "What is the current price of Bitcoin?",
            True,   # factual_now + recency -> 0.50
        ),
        (
            "Explain how attention mechanisms work in transformers",
            False,  # pure generation, no patterns fire -> 0.0
        ),
    ]
    print("\\n=== Agentic Scorer — Example Runs ===\\n")
    all_passed = True
    for prompt, expected in examples:
        result = score_agentic_intent(prompt)
        status = "OK" if result.is_agentic == expected else "FAIL"
        if result.is_agentic != expected:
            all_passed = False
        print(f"  [{status}] score={result.score:.2f}  agentic={result.is_agentic}")
        print(f"       prompt:  {prompt[:72]}")
        print(f"       matched: {result.matched or '(none)'}\\n")
    sys.exit(0 if all_passed else 1)
