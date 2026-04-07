"""
Comprehensive event logging for observability in Tap.

Logs all non-trivial decisions and measurements:
- Cache hits/misses
- Routing decisions  
- Model selection
- Token usage
- Latency per stage
- Cost tracking
- LLM scoring details
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_tap_logger():
    """Get the proxy's wiretap logger if available."""
    try:
        from beigebox.main import get_state
        state = get_state()
        return state.proxy.wire if state.proxy else None
    except Exception:
        return None


def log_cache_event(
    event_type: str,  # "hit" | "miss" | "store"
    cache_type: str,  # "semantic" | "embedding" | "session"
    key: str,
    similarity: Optional[float] = None,
    ttl_remaining: Optional[int] = None,
):
    """Log cache hit/miss/store event."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "cache_type": cache_type,
        "key_hash": hash(key) % 10000,
        "similarity": similarity,
        "ttl_remaining": ttl_remaining,
    }
    
    content = f"Cache {event_type}: {cache_type} (key_hash={meta['key_hash']})"
    if similarity is not None:
        content += f" similarity={similarity:.3f}"
    
    wire.log(
        direction="inbound",
        role="cache",
        content=content,
        event_type=f"cache_{event_type}",
        source="cache",
        meta=meta,
    )


def log_routing_decision(
    decision: str,  # "tier1_session" | "tier2_classifier" | "tier3_semantic" | "tier4_judge"
    route: str,
    confidence: float,
    latency_ms: float,
    details: Optional[Dict[str, Any]] = None,
):
    """Log routing tier decision."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "decision": decision,
        "route": route,
        "confidence": confidence,
        "latency_ms": latency_ms,
        **(details or {}),
    }
    
    content = f"Route: {decision} → {route} (confidence={confidence:.3f}, {latency_ms:.1f}ms)"
    
    wire.log(
        direction="inbound",
        role="router",
        content=content,
        event_type="routing_decision",
        source="router",
        meta=meta,
    )


def log_model_selection(
    context: str,  # "default" | "judge" | "routing" | "summary" | "agentic"
    model: str,
    reason: str,  # "default" | "force" | "config" | "fallback"
):
    """Log model selection decision."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "context": context,
        "model": model,
        "reason": reason,
    }
    
    content = f"Model {context}: {model} ({reason})"
    
    wire.log(
        direction="inbound",
        role="model_selector",
        content=content,
        event_type="model_selection",
        source="router",
        meta=meta,
    )


def log_token_usage(
    component: str,  # "prompt" | "completion" | "judge" | "classifier" | "summary"
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost: Optional[float] = None,
):
    """Log token usage per component."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "component": component,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost": cost,
    }
    
    content = f"Tokens {component}: {total_tokens} (p={prompt_tokens}, c={completion_tokens}) cost=${cost or 0:.4f}"
    
    wire.log(
        direction="inbound",
        role="token_counter",
        content=content,
        event_type="token_usage",
        source="inference",
        meta=meta,
    )


def log_latency_stage(
    stage: str,  # "encode" | "classify" | "cache_lookup" | "inference" | "judge" | "postprocess"
    latency_ms: float,
    details: Optional[Dict[str, Any]] = None,
):
    """Log latency for a specific stage."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "stage": stage,
        "latency_ms": latency_ms,
        **(details or {}),
    }
    
    content = f"Latency {stage}: {latency_ms:.1f}ms"
    
    wire.log(
        direction="inbound",
        role="profiler",
        content=content,
        event_type="latency_stage",
        source="profiler",
        meta=meta,
    )


def log_judge_scores(
    component: str,  # "dimension" | "overall"
    scores: Dict[str, float],  # {accuracy, efficiency, clarity, hallucination, safety}
    weighted: Optional[float] = None,
):
    """Log LLM judge scoring results."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "component": component,
        "scores": scores,
        "weighted": weighted,
    }
    
    score_str = ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
    content = f"Judge {component}: {score_str}"
    if weighted is not None:
        content += f" weighted={weighted:.3f}"
    
    wire.log(
        direction="inbound",
        role="judge",
        content=content,
        event_type="judge_score",
        source="judge",
        meta=meta,
    )


def log_cost_event(
    source: str,  # "openrouter" | "local" | "anthropic"
    model: str,
    cost: float,
    tokens: int,
):
    """Log cost tracking event."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "source": source,
        "model": model,
        "cost": cost,
        "tokens": tokens,
        "cost_per_token": cost / tokens if tokens > 0 else 0,
    }
    
    content = f"Cost {source}: ${cost:.6f} ({tokens} tokens, {meta['cost_per_token']:.8f}$/token)"
    
    wire.log(
        direction="inbound",
        role="cost_tracker",
        content=content,
        event_type="cost_tracking",
        source="billing",
        meta=meta,
    )


def log_embedding_decision(
    similarity: float,
    threshold: float,
    decision: str,  # "use_cached" | "refresh_needed" | "cache_miss"
):
    """Log embedding classifier decision."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "similarity": similarity,
        "threshold": threshold,
        "decision": decision,
    }
    
    content = f"Embedding: similarity={similarity:.3f} vs threshold={threshold:.3f} → {decision}"
    
    wire.log(
        direction="inbound",
        role="classifier",
        content=content,
        event_type="embedding_decision",
        source="classifier",
        meta=meta,
    )


# ── Higher-level composite events ──────────────────────────────────────────

def log_request_started(model: str, tokens: int):
    """Log the start of a request."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {"model": model, "tokens": tokens}
    content = f"Request start: {model} ({tokens} tokens)"
    
    wire.log(
        direction="inbound",
        role="request",
        content=content,
        event_type="request_started",
        source="proxy",
        meta=meta,
    )


def log_request_completed(
    model: str,
    latency_ms: float,
    tokens_in: int,
    tokens_out: int,
    cost: Optional[float] = None,
):
    """Log request completion with full stats."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "model": model,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
    }
    
    content = f"Request done: {latency_ms:.0f}ms ({tokens_in}→{tokens_out} tokens) cost=${cost or 0:.6f}"
    
    wire.log(
        direction="outbound",
        role="request",
        content=content,
        event_type="request_completed",
        source="proxy",
        meta=meta,
    )


def log_backend_selection(
    backend: str,
    model: str,
    reason: str,  # "session_sticky" | "classifier" | "judge" | "fallback" | "default"
):
    """Log which backend was selected and why."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "backend": backend,
        "model": model,
        "reason": reason,
    }
    
    content = f"Backend: {backend} ({model}) — {reason}"
    
    wire.log(
        direction="inbound",
        role="backend",
        content=content,
        event_type="backend_selection",
        source="router",
        meta=meta,
    )


def log_classifier_run(scores: dict, chosen_route: str, confidence: float):
    """Log embedding classifier results."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "scores": scores,
        "chosen_route": chosen_route,
        "confidence": confidence,
    }
    
    score_str = ", ".join(f"{k}={v:.2f}" for k, v in scores.items())
    content = f"Classifier: {chosen_route} (conf={confidence:.3f}) scores=[{score_str}]"
    
    wire.log(
        direction="inbound",
        role="classifier",
        content=content,
        event_type="classifier_result",
        source="classifier",
        meta=meta,
    )


def log_decision_llm_call(
    prompt_len: int,
    decision: str,
    confidence: float,
    latency_ms: float,
):
    """Log decision LLM judge call results."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "prompt_len": prompt_len,
        "decision": decision,
        "confidence": confidence,
        "latency_ms": latency_ms,
    }
    
    content = f"Judge: {decision} (conf={confidence:.3f}, {latency_ms:.0f}ms)"
    
    wire.log(
        direction="inbound",
        role="judge",
        content=content,
        event_type="decision_llm_result",
        source="judge",
        meta=meta,
    )


def log_tool_call(tool_name: str, status: str, latency_ms: float, error: Optional[str] = None):
    """Log tool invocation and result."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "tool": tool_name,
        "status": status,
        "latency_ms": latency_ms,
        "error": error,
    }
    
    content = f"Tool {tool_name}: {status} ({latency_ms:.0f}ms)"
    if error:
        content += f" error={error[:50]}"
    
    wire.log(
        direction="inbound",
        role="tool",
        content=content,
        event_type="tool_call",
        source="tools",
        meta=meta,
    )


def log_harness_turn(
    run_id: str,
    turn: int,
    model: str,
    tokens_in: int,
    tokens_out: int,
    status: str,  # "thinking" | "tool_call" | "response" | "done" | "error"
):
    """Log harness/orchestrator turn."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "run_id": run_id,
        "turn": turn,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "status": status,
    }
    
    content = f"Harness turn {turn}: {status} ({tokens_in}→{tokens_out} tokens)"
    
    wire.log(
        direction="inbound",
        role="harness",
        content=content,
        event_type="harness_turn",
        source="harness",
        run_id=run_id,
        meta=meta,
    )


def log_payload_event(
    source: str,
    payload: dict | None = None,
    response: str | None = None,
    model: str = "",
    backend: str = "",
    conversation_id: str = "",
    latency_ms: float = 0.0,
) -> None:
    """Log a full LLM payload — summary onto Tap bus, full body to payload.jsonl.

    Gate-checked here so call sites need no conditional logic.
    Only active when payload_log_enabled: true in runtime_config.
    """
    from beigebox.config import get_runtime_config
    if not get_runtime_config().get("payload_log_enabled", False):
        return

    # Lightweight summary event on the Tap bus (no payload data — keep bus lean)
    wire = _get_tap_logger()
    if wire:
        msg_count = len(payload.get("messages", [])) if payload else 0
        summary = f"Payload {source}: {model}"
        if msg_count:
            summary += f" [{msg_count} msgs]"
        if latency_ms:
            summary += f" ({latency_ms:.0f}ms)"
        wire.log(
            direction="outbound" if payload else "inbound",
            role="payload",
            content=summary,
            model=model,
            event_type="payload",
            source=source,
            meta={
                "backend": backend,
                "conversation_id": conversation_id,
                "latency_ms": latency_ms,
            },
        )

    # Full body written to payload.jsonl — off the bus, separate concern
    try:
        from beigebox.payload_log import write_payload
        write_payload(
            source=source,
            payload=payload,
            response=response,
            model=model,
            backend=backend,
            conversation_id=conversation_id,
            latency_ms=latency_ms,
        )
    except Exception:
        pass


def log_error_event(component: str, error: str, severity: str = "error"):
    """Log errors and exceptions."""
    wire = _get_tap_logger()
    if not wire:
        return
    
    meta = {
        "component": component,
        "severity": severity,
        "error": error[:200],
    }
    
    content = f"ERROR {component}: {error[:100]}"
    
    wire.log(
        direction="inbound",
        role="error",
        content=content,
        event_type="error",
        source=component,
        meta=meta,
    )
