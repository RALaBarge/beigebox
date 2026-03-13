"""
Trajectory evaluation scoring for autonomous operator runs.

Pure stdlib — no I/O, no LLM calls. Scores from SSE event dicts collected
during a run. Called after the run loop completes in api_harness_autonomous.

Usage:
    score = score_run(query, events, max_turns, final_answer)
    # {"score": 7.4, "flow": 8, "efficiency": 6, "quality": 9, "intent": 7,
    #  "flags": ["hit_turn_cap"], "turns_used": 4, "tool_calls": 11}
"""
from __future__ import annotations

import re
from collections import Counter

_CODING_KEYWORDS = frozenset(
    {"build", "write", "create", "implement", "code", "fix", "generate",
     "develop", "program", "script", "function", "class", "module", "api",
     "endpoint", "test", "refactor", "deploy"}
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _is_coding_task(query: str) -> bool:
    return bool(_words(query) & _CODING_KEYWORDS)


def score_run(
    query: str,
    events: list[dict],
    max_turns: int,
    final_answer: str,
) -> dict:
    """
    Score an autonomous operator run from its SSE event stream.

    Parameters
    ----------
    query       : original user question
    events      : list of event dicts (type, tool, input, content, …)
    max_turns   : the max_turns value used for this run
    final_answer: the final answer string (may contain ##DONE##)

    Returns
    -------
    dict with keys: score, flow, efficiency, quality, intent,
                    flags, turns_used, tool_calls
    """
    # ── Collect metrics from event stream ──────────────────────────────────
    tool_call_pairs: list[tuple[str, str]] = []
    turns_used = 1  # turn 0 always happens
    error_count = 0
    workspace_writes = 0
    turns_with_no_tools: list[int] = []
    current_turn = 0
    tools_in_current_turn = 0

    for ev in events:
        etype = ev.get("type", "")

        if etype == "turn_start":
            # Flush previous turn's tool count
            if current_turn > 0 and tools_in_current_turn == 0:
                turns_with_no_tools.append(current_turn)
            current_turn = ev.get("turn", current_turn + 1) - 1
            turns_used = max(turns_used, current_turn + 1)
            tools_in_current_turn = 0

        elif etype == "tool_call":
            tool = ev.get("tool", "")
            inp = (ev.get("input", "") or "")[:120]  # fingerprint, not full input
            tool_call_pairs.append((tool, inp))
            tools_in_current_turn += 1
            if tool == "workspace_file" and (
                '"action": "write"' in ev.get("input", "")
                or '"action": "append"' in ev.get("input", "")
                or "write" in ev.get("input", "").lower()[:50]
            ):
                workspace_writes += 1

        elif etype == "error":
            error_count += 1

    # Flush last turn
    if tools_in_current_turn == 0 and current_turn > 0:
        # Don't penalise final turn for no tools if it produced an answer
        pass

    tool_calls = len(tool_call_pairs)

    # ── Loop detection ──────────────────────────────────────────────────────
    pair_counts = Counter(tool_call_pairs)
    looped_pairs = sum(1 for c in pair_counts.values() if c >= 3)
    loop_detected = looped_pairs > 0

    # ── Flags ───────────────────────────────────────────────────────────────
    hit_turn_cap = turns_used / max(max_turns, 1) >= 0.8
    coding_task = _is_coding_task(query)
    no_file_writes = coding_task and workspace_writes == 0
    flags = []
    if loop_detected:
        flags.append("loop_detected")
    if hit_turn_cap:
        flags.append("hit_turn_cap")
    if no_file_writes:
        flags.append("no_file_writes")

    # ── Flow (0–10) ─────────────────────────────────────────────────────────
    flow = 10.0
    flow -= looped_pairs * 2                        # −2 per looped pair
    flow -= len(turns_with_no_tools) * 1            # −1 per stalled turn
    flow = max(0.0, min(10.0, flow))

    # ── Efficiency (0–10) ───────────────────────────────────────────────────
    efficiency = 10.0
    if hit_turn_cap:
        efficiency -= 3
    if no_file_writes:
        efficiency -= 2
    efficiency = max(0.0, min(10.0, efficiency))

    # ── Quality (0–10) ──────────────────────────────────────────────────────
    quality = 10.0
    if max_turns > 1 and "##DONE##" not in (final_answer or ""):
        quality -= 3
    quality -= error_count * 2
    if tool_calls == 0 and len(final_answer or "") < 100:
        quality -= 1
    quality = max(0.0, min(10.0, quality))

    # ── Intent (0–10) ───────────────────────────────────────────────────────
    if not final_answer:
        intent = 5.0
    else:
        query_words = _words(query)
        answer_words = _words(final_answer)
        if query_words and answer_words:
            overlap = len(query_words & answer_words) / len(query_words)
            intent = 5.0 + overlap * 5.0   # 5–10 based on keyword retention
        else:
            intent = 7.0
    intent = max(0.0, min(10.0, intent))

    # ── Overall score ────────────────────────────────────────────────────────
    score = (
        flow       * 0.30
        + efficiency * 0.25
        + quality    * 0.30
        + intent     * 0.15
    )
    score = round(max(0.0, min(10.0, score)), 1)

    return {
        "score":      score,
        "flow":       round(flow, 1),
        "efficiency": round(efficiency, 1),
        "quality":    round(quality, 1),
        "intent":     round(intent, 1),
        "flags":      flags,
        "turns_used": turns_used,
        "tool_calls": tool_calls,
    }
