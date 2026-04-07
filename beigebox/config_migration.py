"""
Config migration utilities for v1 → v2 refactoring.

Phases 1-3 refactoring consolidated:
- Phase 1: Feature flags (scattered → centralized)
- Phase 2: Agent config + models registry (fragmented → unified)
- Phase 3: Routing logic (5 sections → 1 consolidated)

This module handles backward compatibility by auto-converting old format to new.
"""

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)


def detect_config_version(config: Dict[str, Any]) -> int:
    """
    Detect which config version we're looking at.

    v1.0: Has top-level backends_enabled, separate backend/backends, scattered features
    v2.0: Has features: section, routing.tiers, agents: section, models: section

    Returns:
        1 or 2
    """
    if "features" in config or "routing" in config and "tiers" in config.get("routing", {}):
        return 2
    return 1


def migrate_v1_to_v2(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-migrate v1 config to v2 format.

    Phase 1: Consolidate scattered feature flags into features: section
    Phase 2: Unify agent config (decision_llm, operator, harness) and create models registry
    Phase 3: Consolidate routing (backend, backends, backends_enabled, decision_llm routing)

    Args:
        config: v1 format config dict

    Returns:
        v2 format config dict
    """
    migrated = config.copy()

    # ─ Phase 1: Feature Flags ────────────────────────────────────────────

    features = {
        "backends": config.get("backends_enabled", True),
        "decision_llm": config.get("decision_llm", {}).get("enabled", True),
        "semantic_cache": config.get("semantic_cache", {}).get("enabled", False),
        "classifier": config.get("classifier", {}).get("enabled", False),
        "operator": config.get("operator", {}).get("enabled", True),
        "harness": config.get("harness", {}).get("enabled", True),
        "cost_tracking": config.get("cost_tracking", {}).get("enabled", True),
        "conversation_replay": config.get("conversation_replay", {}).get("enabled", False),
        "auto_summarization": config.get("auto_summarization", {}).get("enabled", False),
        "system_context": config.get("system_context", {}).get("enabled", False),
        "wiretap": config.get("wiretap", {}).get("enabled", True),
        "payload_log": config.get("payload_log", {}).get("enabled", False),
        "wasm": config.get("wasm", {}).get("enabled", False),
        "guardrails": config.get("guardrails", {}).get("enabled", False),
        "amf_mesh": config.get("amf_mesh", {}).get("enabled", False),
        "tools": config.get("tools", {}).get("enabled", True),
        "voice": config.get("voice", {}).get("enabled", False),
        "web_ui_voice": config.get("web_ui", {}).get("voice_enabled", False),
    }
    migrated["features"] = features

    # ─ Phase 2: Models Registry & Agents ─────────────────────────────────

    models = {
        "default": config.get("backend", {}).get("default_model", "qwen3:4b"),
        "profiles": {
            "routing": config.get("decision_llm", {}).get("model", "qwen3:4b"),
            "agentic": config.get("operator", {}).get("model", "qwen3:4b"),
            "summary": config.get("auto_summarization", {}).get("summary_model", "qwen3:4b"),
            "embedding": config.get("embedding", {}).get("model", "nomic-embed-text"),
        },
        "per_task": {},
        "whitelist": config.get("local_models", {"enabled": False, "allowed_models": []}),
    }
    migrated["models"] = models

    # Migrate agents config with unified structure
    agents = {
        "decision_llm": {
            "enabled": features["decision_llm"],
            # Config moved to routing.tiers.4_decision_llm
        },
        "operator": {
            "enabled": features["operator"],
            "timeout_ms": config.get("operator", {}).get("timeout", 300) * 1000,
            "run_timeout_s": config.get("operator", {}).get("run_timeout", 600),
            "max_iterations": config.get("operator", {}).get("max_iterations", 10),
            "shell": config.get("operator", {}).get("shell", {"enabled": True}),
            "autonomous": config.get("operator", {}).get("autonomous", {"enabled": False}),
            "pre_hook": config.get("operator", {}).get("pre_hook", {"enabled": False}),
            "post_hook": config.get("operator", {}).get("post_hook", {"enabled": False}),
            "context_pruning": config.get("operator", {}).get("context_pruning", {"enabled": False}),
            "reflection": config.get("operator", {}).get("reflection", {"enabled": False}),
        },
        "harness": {
            "enabled": features["harness"],
            "ralph_enabled": config.get("harness", {}).get("ralph_enabled", False),
            "retry": config.get("harness", {}).get("retry", {}),
            "timeout_ms": _migrate_harness_timeout(config.get("harness", {})),
            "shadow_agents": config.get("harness", {}).get("shadow_agents", {"enabled": False}),
            "stagger": _migrate_harness_stagger(config.get("harness", {})),
            "store_runs": config.get("harness", {}).get("store_runs", True),
            "max_stored_runs": config.get("harness", {}).get("max_stored_runs", 1000),
        },
    }
    migrated["agents"] = agents

    # ─ Phase 3: Routing Consolidation ────────────────────────────────────

    routing = {
        "tiers": {
            "1_backends": {
                "enabled": features["backends"],
                "backends": config.get("backends", []),
            },
            "2_classifier": {
                "enabled": features["classifier"],
                "border_threshold": config.get("classifier", {}).get("border_threshold", 0.04),
            },
            "3_semantic_cache": {
                "enabled": features["semantic_cache"],
                "similarity_threshold": config.get("semantic_cache", {}).get("similarity_threshold", 0.95),
                "max_entries": config.get("semantic_cache", {}).get("max_entries", 10000),
                "ttl_seconds": config.get("semantic_cache", {}).get("ttl_seconds", 3600),
            },
            "4_decision_llm": {
                "enabled": features["decision_llm"],
                "temperature": config.get("decision_llm", {}).get("temperature", 0.2),
                "timeout_ms": config.get("decision_llm", {}).get("timeout", 5) * 1000,
                "max_tokens": config.get("decision_llm", {}).get("max_tokens", 256),
            },
        },
        "session_ttl_seconds": config.get("routing", {}).get("session_ttl_seconds", 1800),
        "fallback_model": models["default"],
        "force_route": config.get("routing", {}).get("force_route", ""),
        "force_decision": config.get("routing", {}).get("force_decision", False),
    }
    migrated["routing"] = routing

    # Remove old sections that have been consolidated
    for key in [
        "backends_enabled",
        "backend",  # Keep only embedding-related backend
        "decision_llm",  # Moved to routing.tiers.4_decision_llm
        "operator",  # Moved to agents.operator
        "harness",  # Moved to agents.harness
        "local_models",  # Moved to models.whitelist
    ]:
        migrated.pop(key, None)

    return migrated


def _migrate_harness_timeout(harness_cfg: Dict[str, Any]) -> Dict[str, int]:
    """Convert harness.timeouts (in seconds) to timeout_ms (in ms)."""
    timeouts = harness_cfg.get("timeouts", {})
    return {
        "task": timeouts.get("task_seconds", 120) * 1000,
        "operator": timeouts.get("operator_seconds", 180) * 1000,
    }


def _migrate_harness_stagger(harness_cfg: Dict[str, Any]) -> Dict[str, float]:
    """Convert harness.stagger (in seconds) to stagger_ms (in ms)."""
    stagger = harness_cfg.get("stagger", {})
    return {
        "operator_ms": int(stagger.get("operator_seconds", 1.0) * 1000),
        "model_ms": int(stagger.get("model_seconds", 0.4) * 1000),
    }


def log_migration(config_version: int) -> None:
    """Log migration status at startup."""
    if config_version == 1:
        _log.warning(
            "Config format v1.0 detected. Auto-migrating to v2.0 (Phases 1-3 refactoring). "
            "Update config.yaml to v2.0 format at your convenience. "
            "See config.yaml.v2-template for new structure."
        )
    elif config_version == 2:
        _log.debug("Config format v2.0 (Phases 1-3) detected. All good.")
    else:
        _log.error(f"Unknown config version {config_version}. Unable to migrate.")
