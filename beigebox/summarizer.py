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
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
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
    model        = (
        summ_cfg.get("summary_model")
        or cfg.get("backend", {}).get("default_model", "")
    )
    backend_url  = cfg.get("backend", {}).get("url", "http://localhost:11434")

    if not model:
        logger.warning("auto_summarizer: no model configured, skipping")
        return messages

    estimated = _estimate_tokens(messages)
    if estimated <= token_budget:
        return messages

    # Separate system messages (keep at front), messages to summarise, recent tail
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
