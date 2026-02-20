"""
Prompt Injection Detection Hook — filter suspicious inputs before they hit the LLM.

Scans user messages for known injection patterns: jailbreak phrases, role overrides,
instruction injection, and prompt boundary attacks. Operates in two modes:

  flag  — annotate the request (adds _bb_injection_flag to body) but let it through.
           The wire log will record the detection. Default mode.

  block — return a canned refusal response and halt the pipeline.

Enable in config.yaml:

    hooks:
      - name: prompt_injection
        path: ./hooks/prompt_injection.py
        enabled: true
        mode: flag          # or "block"
        score_threshold: 3  # number of pattern matches before triggering (default 2)

The hook is intentionally conservative — it looks for structural attacks
(boundary breaking, role override) rather than trying to detect semantic intent,
which would produce too many false positives.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Pattern library ──────────────────────────────────────────────────────────
# Each entry: (name, compiled_regex, weight)
# Weight is added to the score; threshold triggers the action.

_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # Boundary injections — try to end the prompt and start a new instruction
    ("boundary_injection",
     re.compile(r"(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|context|rules?|constraints?)",
                re.IGNORECASE), 3),

    # Role override — trying to become system/developer/DAN
    ("role_override",
     re.compile(r"\b(you\s+are\s+now|pretend\s+(you\s+are|to\s+be)|act\s+as|roleplay\s+as|from\s+now\s+on\s+you\s+(are|will))\b",
                re.IGNORECASE), 2),

    # DAN / jailbreak persona activation
    ("jailbreak_persona",
     re.compile(r"\b(DAN|STAN|evil\s+AI|no\s+restrictions?|unrestricted\s+mode|developer\s+mode|jailbreak|do\s+anything\s+now)\b",
                re.IGNORECASE), 3),

    # System prompt extraction
    ("prompt_extraction",
     re.compile(r"(repeat|print|output|show|reveal|tell\s+me)\s+(your\s+)?(system\s+prompt|instructions?|initial\s+prompt|full\s+prompt|original\s+instructions?)",
                re.IGNORECASE), 2),

    # Instruction delimiters injected mid-message
    ("delimiter_injection",
     re.compile(r"(</?(system|user|assistant|human|AI|instruction)>|\[INST\]|\[/INST\]|###\s*(System|Human|Assistant|Instruction))",
                re.IGNORECASE), 2),

    # Base64 / encoding obfuscation (common in prompt injection attacks)
    ("encoded_payload",
     re.compile(r"(base64|decode\s+this|hex\s+decode|rot13|caesar\s+cipher).{0,80}(instruction|prompt|command|execute)",
                re.IGNORECASE), 2),

    # Prompt chaining — "new task:", "new instruction:" etc.
    ("prompt_chaining",
     re.compile(r"\b(new\s+(task|instruction|command|directive|objective)|TASK:|INSTRUCTION:|SYSTEM:|COMMAND:)\b",
                re.IGNORECASE), 1),
]


def _score_message(text: str) -> tuple[int, list[str]]:
    """Return (total_score, list_of_matched_pattern_names)."""
    score = 0
    matched: list[str] = []
    for name, pattern, weight in _PATTERNS:
        if pattern.search(text):
            score += weight
            matched.append(name)
    return score, matched


def _get_config(context: dict) -> dict:
    """Extract hook config from the context dict."""
    cfg = context.get("config", {})
    # Hooks can be listed under hooks: or hook_config:
    hooks = cfg.get("hooks", [])
    for h in hooks:
        if h.get("name") == "prompt_injection":
            return h
    return {}


def pre_request(body: dict, context: dict) -> dict:
    """
    Scan the latest user message for injection patterns.
    Returns the (possibly annotated) body.
    """
    messages = body.get("messages", [])
    if not messages:
        return body

    # Check only the most recent user message
    user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            user_msg = content if isinstance(content, str) else str(content)
            break

    if not user_msg:
        return body

    hook_cfg = _get_config(context)
    mode = hook_cfg.get("mode", "flag")
    threshold = int(hook_cfg.get("score_threshold", 2))

    score, matched = _score_message(user_msg)

    if score < threshold:
        return body

    # Triggered
    conv_id = context.get("conversation_id", "")
    logger.warning(
        "Prompt injection detected (score=%d, patterns=%s, conv=%s)",
        score, matched, conv_id[:16] if conv_id else "?",
    )

    if mode == "block":
        # Signal the pipeline to return a refusal.
        # The proxy checks for _beigebox_block and short-circuits if set.
        body["_beigebox_block"] = {
            "reason": "prompt_injection",
            "score": score,
            "patterns": matched,
            "message": (
                "I noticed this message contains patterns associated with "
                "prompt injection attempts. I can't process it as written."
            ),
        }
        logger.info("Prompt injection blocked (score=%d)", score)
    else:
        # Flag mode — annotate but allow through
        body["_bb_injection_flag"] = {
            "score": score,
            "patterns": matched,
        }

    return body
