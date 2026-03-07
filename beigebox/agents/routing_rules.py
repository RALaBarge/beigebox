"""
routing_rules.py — hot-reloaded rule engine for proxy routing.

Rules live in runtime_config.yaml under ``routing_rules`` and are evaluated on
every request after z-commands and before the embedding classifier.

Rule schema
-----------
::

    routing_rules:
      - name: "label for wiretap"       # optional
        priority: 10                     # lower = evaluated first (default 50)
        continue: false                  # true = keep evaluating after match
        match:
          message:           "regex"     # re.search on latest user message (IGNORECASE)
          message_contains:  "substr"    # plain substring (IGNORECASE)
          model:             "glob*"     # fnmatch against the requested model name
          auth_key:          "name"      # exact API-key name
          has_tools:         true/false  # presence of a tools array in the request
          message_count:
            min: 1
            max: 5
          conversation_id:   "regex"     # re.search on conversation_id field
        action:
          # ── Routing ─────────────────────────────────────────────────────
          model:              "qwen3:14b"
          backend:            "openrouter"      # named backend from config.yaml
          route:              "complex"         # named route from decision_llm.routes
          tools:              ["web_search"]    # force-inject tools (runs before LLM)
          pass_through:       false             # true = apply other actions but still
                                               #        run classifier / decision LLM
          # ── Generation params ────────────────────────────────────────────
          temperature:        0.2
          top_p:              0.9
          top_k:              40
          num_ctx:            32768
          max_tokens:         4096
          repeat_penalty:     1.1
          seed:               42
          # ── Context injection ────────────────────────────────────────────
          system_prompt:      "You are a code review assistant."
          inject_file:        "/path/to/context.md"   # re-read from disk each request
          inject_context:     "Inline context text."  # combined with inject_file if both set
          # ── Cache behaviour ──────────────────────────────────────────────
          skip_session_cache:   false   # don't sticky this routing decision
          skip_semantic_cache:  false   # bypass semantic cache lookup
          # ── Observability ────────────────────────────────────────────────
          tag:                "code-request"   # appears in wiretap as routing_rule_tag

Internal body keys written by this module (all stripped before the request
reaches the backend):
    _bb_force_backend      — named backend override for MultiBackendRouter
    _bb_skip_semantic_cache — skip semantic cache lookup
    _bb_rule_tag           — wiretap tag from matched rule
    _bb_auth_key           — injected by main.py, stripped here before matching
    _bb_forced_tools       — list of tool names to run (applied by proxy.py)
"""
from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Internal body keys ─────────────────────────────────────────────────────────
BB_FORCE_BACKEND       = "_bb_force_backend"
BB_SKIP_SEMANTIC_CACHE = "_bb_skip_semantic_cache"
BB_RULE_TAG            = "_bb_rule_tag"
BB_AUTH_KEY            = "_bb_auth_key"
BB_FORCED_TOOLS        = "_bb_forced_tools"

# Generation param names accepted in action
_GEN_PARAMS = (
    "temperature", "top_p", "top_k", "num_ctx",
    "max_tokens", "repeat_penalty", "seed",
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_user_message(body: dict) -> str:
    """Extract the latest user message text from the request body."""
    for msg in reversed(body.get("messages", [])):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            return str(content)
    return ""


def _prepend_system_message(body: dict, text: str) -> dict:
    """Prepend text to the system message, creating one if absent."""
    messages = list(body.get("messages", []))
    if messages and messages[0].get("role") == "system":
        messages[0] = {**messages[0], "content": text + "\n\n" + messages[0]["content"]}
    else:
        messages.insert(0, {"role": "system", "content": text})
    body["messages"] = messages
    return body


# ── Match ──────────────────────────────────────────────────────────────────────

def _match_rule(
    match_spec: dict,
    user_message: str,
    body: dict,
    auth_key_name: str | None,
) -> bool:
    """Return True iff every condition in match_spec is satisfied."""

    # message — regex on latest user message
    if pattern := match_spec.get("message"):
        try:
            if not re.search(pattern, user_message, re.IGNORECASE):
                return False
        except re.error as exc:
            logger.warning("routing_rules: invalid message regex %r: %s", pattern, exc)
            return False

    # message_contains — plain substring
    if substr := match_spec.get("message_contains"):
        if substr.lower() not in user_message.lower():
            return False

    # model — fnmatch glob
    if model_pattern := match_spec.get("model"):
        if not fnmatch.fnmatch(body.get("model", ""), model_pattern):
            return False

    # auth_key — exact name
    if key_name := match_spec.get("auth_key"):
        if auth_key_name != key_name:
            return False

    # has_tools — boolean
    if (has_tools := match_spec.get("has_tools")) is not None:
        if bool(has_tools) != bool(body.get("tools")):
            return False

    # message_count — {min: N, max: N}
    if count_spec := match_spec.get("message_count"):
        n = len(body.get("messages", []))
        if (min_c := count_spec.get("min")) is not None and n < int(min_c):
            return False
        if (max_c := count_spec.get("max")) is not None and n > int(max_c):
            return False

    # conversation_id — regex
    if conv_pattern := match_spec.get("conversation_id"):
        conv_id = body.get("conversation_id", "")
        try:
            if not re.search(conv_pattern, conv_id, re.IGNORECASE):
                return False
        except re.error as exc:
            logger.warning(
                "routing_rules: invalid conversation_id regex %r: %s", conv_pattern, exc
            )
            return False

    return True


# ── Apply ──────────────────────────────────────────────────────────────────────

def _apply_action(body: dict, action: dict, routes: dict | None = None) -> dict:
    """Apply an action dict to the request body. Returns the modified body."""

    # ── Routing ───────────────────────────────────────────────────────────────
    if model := action.get("model"):
        body["model"] = model

    if backend := action.get("backend"):
        body[BB_FORCE_BACKEND] = backend

    # Named route alias — resolved via decision_llm.routes config
    if route_name := action.get("route"):
        resolved_model: str | None = None
        if routes:
            route_cfg = routes.get(route_name)
            if isinstance(route_cfg, dict):
                resolved_model = route_cfg.get("model")
            elif isinstance(route_cfg, str):
                resolved_model = route_cfg
        if resolved_model:
            body["model"] = resolved_model
        else:
            logger.warning(
                "routing_rules: route %r not found in decision_llm.routes — skipped",
                route_name,
            )

    # Force tools (list of names) — proxy runs them after _hybrid_route returns
    if tools := action.get("tools"):
        if isinstance(tools, list) and tools:
            existing = list(body.get(BB_FORCED_TOOLS) or [])
            body[BB_FORCED_TOOLS] = existing + [t for t in tools if t not in existing]

    # ── Generation params ─────────────────────────────────────────────────────
    for key in _GEN_PARAMS:
        if (val := action.get(key)) is not None:
            body[key] = val

    # ── Context injection ─────────────────────────────────────────────────────
    text_parts: list[str] = []

    if inject_file := action.get("inject_file"):
        try:
            text_parts.append(Path(inject_file).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "routing_rules: could not read inject_file %r: %s", inject_file, exc
            )

    if inline := action.get("inject_context"):
        text_parts.append(str(inline))

    if explicit := action.get("system_prompt"):
        text_parts.append(str(explicit))

    if text_parts:
        body = _prepend_system_message(body, "\n\n".join(text_parts))

    # ── Cache flags ───────────────────────────────────────────────────────────
    if action.get("skip_semantic_cache"):
        body[BB_SKIP_SEMANTIC_CACHE] = True

    # ── Observability ─────────────────────────────────────────────────────────
    if tag := action.get("tag"):
        body[BB_RULE_TAG] = str(tag)

    return body


# ── Engine ─────────────────────────────────────────────────────────────────────

def evaluate_routing_rules(
    rules: list[dict],
    body: dict,
    routes: dict | None = None,
) -> tuple[dict, list[str], bool, bool]:
    """
    Evaluate routing rules against the current request.

    Reads ``body[BB_AUTH_KEY]`` (set by main.py) and strips it before matching
    so it never reaches the backend.

    Rules are sorted by ``priority`` (ascending; lower = higher priority,
    default 50) before evaluation.  ``continue: true`` allows multiple rules
    to fire in sequence; otherwise the first match stops evaluation.

    Returns:
        (modified_body, matched_rule_names, skip_session_cache, pass_through)

        skip_session_cache — True if any matched rule set skip_session_cache
        pass_through       — True if any matched rule set pass_through
                             (means: apply actions but still run the ML stack)
    """
    auth_key_name: str | None = body.pop(BB_AUTH_KEY, None)
    user_message: str = _get_user_message(body)

    sorted_rules = sorted(
        (r for r in rules if isinstance(r, dict)),
        key=lambda r: r.get("priority", 50),
    )

    matched_names: list[str] = []
    skip_session_cache = False
    pass_through = False

    for rule in sorted_rules:
        match_spec = rule.get("match", {})
        if not _match_rule(match_spec, user_message, body, auth_key_name):
            continue

        name = rule.get("name", "<unnamed>")
        action = rule.get("action", {})
        body = _apply_action(body, action, routes=routes)
        matched_names.append(name)

        if action.get("skip_session_cache"):
            skip_session_cache = True
        if action.get("pass_through"):
            pass_through = True

        if not rule.get("continue", False):
            break

    return body, matched_names, skip_session_cache, pass_through
