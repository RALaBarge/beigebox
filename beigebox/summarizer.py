"""
Auto-summarizer — context window management for long conversations.

When a conversation's estimated token count exceeds `token_budget`, the
summarizer collapses older messages into a single system-role summary
message, freeing context window for new turns.

Config (in config.yaml or config.docker.yaml):

    auto_summarization:
      enabled: false
      token_budget: 3000       # trigger when history exceeds this
      summary_model: ""        # defaults to backend.default_model
      keep_last: 4             # always keep the N most recent turns intact
      summary_prefix: "Summary of earlier conversation: "

Usage (called from proxy.py before forwarding):

    from beigebox.summarizer import maybe_summarize
    messages = await maybe_summarize(messages, cfg)

The function returns the (possibly shortened) messages list unchanged if
summarization is not needed or not configured. Fails silently — if the
summary LLM call fails, original messages are returned untouched.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from beigebox.config import get_runtime_config

logger = logging.getLogger(__name__)


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return total_chars // 4


async def _call_llm(prompt: str, model: str, backend_url: str) -> str:
    """Single synchronous LLM call for summarization."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0,
        "max_tokens": 512,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{backend_url.rstrip('/')}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            from beigebox.response_normalizer import normalize_response
            return normalize_response(resp.json()).content.strip()
    except Exception as e:
        logger.warning("auto_summarizer: LLM call failed: %s", e)
        return ""


async def maybe_summarize(
    messages: list[dict[str, Any]],
    cfg: dict,
) -> list[dict[str, Any]]:
    """
    Summarize older messages if the conversation exceeds the token budget.

    Returns the messages list — either unchanged or with older turns
    replaced by a single summary system message.
    """
    summ_cfg = cfg.get("auto_summarization", {})
    if not summ_cfg.get("enabled", False):
        return messages

    token_budget = int(summ_cfg.get("token_budget", 3000))
    keep_last    = int(summ_cfg.get("keep_last", 4))
    prefix       = summ_cfg.get("summary_prefix", "Summary of earlier conversation: ")

    # Resolve summary model from unified models registry (Phase 2 refactoring)
    rt = get_runtime_config()
    models_cfg = cfg.get("models", {})
    model = (
        rt.get("models_summary")  # runtime override, new unified key
        or rt.get("auto_summary_model")  # runtime override, old key (compat)
        or summ_cfg.get("summary_model")  # static config, old location (compat)
        or models_cfg.get("profiles", {}).get("summary")  # new unified location
        or models_cfg.get("default")  # fallback to global default
        or cfg.get("backend", {}).get("default_model", "")  # ultimate fallback
    )
    backend_url  = cfg.get("backend", {}).get("url", "http://localhost:11434")

    if not model:
        logger.warning("auto_summarizer: no model configured, skipping")
        return messages

    estimated = _estimate_tokens(messages)
    if estimated <= token_budget:
        return messages

    # System messages are always preserved at the front of the context window
    # (they carry the operator system prompt, skills list, etc.). We only
    # summarise the user/assistant turn history, keeping the N most recent turns
    # verbatim so the model has full detail on the immediate conversation.
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system  = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_last:
        return messages  # Not enough history to summarise

    to_summarise = non_system[:-keep_last]
    to_keep      = non_system[-keep_last:]

    # Build summarisation prompt
    history_text = "\n".join(
        f"{m.get('role', '?').upper()}: {str(m.get('content', ''))[:500]}"
        for m in to_summarise
    )
    prompt = (
        "Summarise the following conversation history concisely. "
        "Preserve key facts, decisions, and context. "
        "Write in third person. Be brief — 3-6 sentences maximum.\n\n"
        f"{history_text}"
    )

    summary_text = await _call_llm(prompt, model, backend_url)

    if not summary_text:
        logger.warning("auto_summarizer: empty summary returned, keeping original messages")
        return messages

    summary_msg = {
        "role": "system",
        "content": f"{prefix}{summary_text}",
    }

    result = system_msgs + [summary_msg] + to_keep
    new_tokens = _estimate_tokens(result)
    logger.info(
        "auto_summarizer: compressed %d msgs → summary + %d recent (was ~%d tokens, now ~%d)",
        len(to_summarise), keep_last, estimated, new_tokens,
    )
    return result


async def aggressive_summarize(
    messages: list[dict[str, Any]],
    cfg: dict,
) -> list[dict[str, Any]]:
    """
    Compress older messages into tight bullet points on every request.

    Unlike maybe_summarize(), this runs unconditionally (no token-budget check).
    Replaces all history older than keep_last turns with a single system message
    of bullet points — maximally concise, preserving exact values.

    Config (aggressive_summarization section):
        enabled: false
        keep_last: 2      # verbatim recent turns to preserve
        model: ""         # blank = fall through to summary model / default
    """
    agg_cfg = cfg.get("aggressive_summarization", {})
    if not agg_cfg.get("enabled", False):
        return messages

    keep_last = int(agg_cfg.get("keep_last", 2))

    rt = get_runtime_config()
    models_cfg = cfg.get("models", {})
    model = (
        rt.get("agg_sum_model")
        or agg_cfg.get("model")
        or models_cfg.get("profiles", {}).get("summary")
        or models_cfg.get("default")
        or cfg.get("backend", {}).get("default_model", "")
    )
    backend_url = cfg.get("backend", {}).get("url", "http://localhost:11434")

    if not model:
        logger.warning("aggressive_summarizer: no model configured, skipping")
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system  = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_last:
        return messages  # nothing old enough to compress

    to_compress = non_system[:-keep_last]
    to_keep     = non_system[-keep_last:]

    history_text = "\n".join(
        f"{m.get('role', '?').upper()}: {str(m.get('content', ''))[:500]}"
        for m in to_compress
    )
    prompt = (
        "Compress the following conversation into bullet points.\n"
        "Rules:\n"
        "- Each bullet \u2264 10 words\n"
        "- Preserve exact names, numbers, dates, file paths, URLs, code identifiers\n"
        "- Omit small talk, filler, and repetition\n"
        "- Output ONLY the bullets, no preamble\n\n"
        f"{history_text}"
    )

    bullets = await _call_llm(prompt, model, backend_url)
    if not bullets:
        logger.warning("aggressive_summarizer: empty response, keeping original messages")
        return messages

    summary_msg = {"role": "system", "content": f"Compressed history:\n{bullets}"}
    result = system_msgs + [summary_msg] + to_keep
    logger.info(
        "aggressive_summarizer: compressed %d msgs → bullets + %d recent",
        len(to_compress), keep_last,
    )
    return result
