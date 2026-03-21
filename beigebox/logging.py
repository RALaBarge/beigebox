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
