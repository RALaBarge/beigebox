"""
Eval scorers — each returns (passed: bool, score: float, reason: str).

Scorer contract:
  fn(output: str, expect: dict) → (bool, float, str)
  score is 0.0–1.0 (partial credit where applicable).
"""
from __future__ import annotations

import re


def score_contains(output: str, expect: dict) -> tuple[bool, float, str]:
    """All strings in expect['contains'] must appear in output (case-insensitive)."""
    terms = expect.get("contains", [])
    if not terms:
        return True, 1.0, "no criteria"
    lower = output.lower()
    matched = [t for t in terms if t.lower() in lower]
    score = len(matched) / len(terms)
    passed = score == 1.0
    reason = f"{len(matched)}/{len(terms)} terms matched"
    return passed, round(score, 4), reason


def score_exact(output: str, expect: dict) -> tuple[bool, float, str]:
    """Output must exactly equal expect['exact'] (stripped)."""
    expected = expect.get("exact", "")
    passed = output.strip() == expected.strip()
    return passed, 1.0 if passed else 0.0, "exact match" if passed else "mismatch"


def score_regex(output: str, expect: dict) -> tuple[bool, float, str]:
    """Output must match expect['regex'] pattern."""
    pattern = expect.get("regex", "")
    if not pattern:
        return True, 1.0, "no pattern"
    try:
        passed = bool(re.search(pattern, output, re.DOTALL | re.IGNORECASE))
    except re.error as e:
        return False, 0.0, f"invalid regex: {e}"
    return passed, 1.0 if passed else 0.0, "pattern matched" if passed else "pattern not found"


def score_not_contains(output: str, expect: dict) -> tuple[bool, float, str]:
    """None of the strings in expect['not_contains'] may appear in output."""
    terms = expect.get("not_contains", [])
    if not terms:
        return True, 1.0, "no criteria"
    lower = output.lower()
    found = [t for t in terms if t.lower() in lower]
    passed = not found
    score = 1.0 - (len(found) / len(terms))
    reason = f"forbidden terms found: {found}" if found else "none present"
    return passed, round(score, 4), reason


def score_llm_judge(
    output: str,
    expect: dict,
    model: str,
    backend_url: str,
) -> tuple[bool, float, str]:
    """Ask an LLM to judge whether the output satisfies the criterion."""
    import httpx
    import json

    criterion = expect.get("llm_judge", "The response is helpful and accurate.")
    system = (
        "You are an evaluator. Given a criterion and a response, decide if the response "
        "satisfies the criterion. Respond ONLY with valid JSON: "
        '{"passed": true|false, "score": 0.0-1.0, "reason": "<one sentence>"}'
    )
    user = f"Criterion: {criterion}\n\nResponse:\n{output}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0.0,
    }
    try:
        resp = httpx.post(f"{backend_url}/v1/chat/completions", json=body, timeout=30.0)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()
        data = json.loads(raw)
        return (
            bool(data.get("passed")),
            float(data.get("score", 0.5)),
            data.get("reason", ""),
        )
    except Exception as e:
        return False, 0.0, f"llm_judge error: {e}"


SCORERS: dict[str, callable] = {
    "contains":     score_contains,
    "exact":        score_exact,
    "regex":        score_regex,
    "not_contains": score_not_contains,
}
