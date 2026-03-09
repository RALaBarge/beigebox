"""
routing_rules.py — hot-reloaded rule engine for proxy routing.

Rules live in runtime_config.yaml under ``routing_rules`` and are evaluated on
every request after z-commands and before the embedding classifier.  Changes to
runtime_config.yaml are picked up immediately — no restart required.

Pipeline position
-----------------
  0. Session cache (sticky model for this conversation)
  1. Z-command  (user override — z: prefix, always wins)
  1.75 ► Routing rules  ◄ (this module)
  2. Embedding classifier  (~50 ms fast path)
  3. Decision LLM  (slow path, borderline cases only)

A rule whose action does NOT set ``pass_through: true`` short-circuits steps
2 and 3 when it matches.  ``pass_through: true`` applies the rule's other
actions (model override, context injection, gen params, etc.) but still lets
the classifier / decision LLM run afterward.

Evaluation order
----------------
Rules are sorted by ``priority`` (ascending; lower number = evaluated first).
The default priority is 50.  Within the same priority, rules appear in the
order they are written.  The first matching rule fires and stops evaluation
unless ``continue: true`` is set on that rule, in which case evaluation
continues and subsequent matching rules layer their effects on top.

Complete schema — every accepted key
-------------------------------------
Copy this block into runtime_config.yaml and remove the keys you don't need.
All keys inside ``match`` and ``action`` are optional.

::

  routing_rules:

    # ── Example: full schema with every accepted key ──────────────────────────
    - name: "human-readable label (shown in wiretap logs)"
                                        # str, optional — defaults to "<unnamed>"
      priority: 50                      # int, default 50 — lower = higher priority
      continue: false                   # bool, default false
                                        #   false → stop after this rule fires
                                        #   true  → keep evaluating subsequent rules
                                        #            (effects layer on top of each other)

      # ── Match conditions (ALL must be true for the rule to fire) ─────────────
      # Omit any condition to match everything for that field.
      match:
        message: "^(fix|debug|refactor)"
                                        # str — Python re.search pattern applied to the
                                        # latest user message, case-insensitive.
                                        # Unset = match any message.

        message_contains: "summarize"   # str — plain substring match, case-insensitive.
                                        # Simpler alternative to a regex.

        model: "gpt-4*"                 # str — fnmatch glob against the model name the
                                        # client sent (e.g. "qwen3:*", "gpt-4*", "*").
                                        # Unset = match any model.

        auth_key: "ci-runner"           # str — exact match against the API key name
                                        # (from auth.keys[].name in config.yaml).
                                        # Unset = match any key (including unauthenticated).

        has_tools: true                 # bool — true  = request must include a tools array
                                        #         false = request must NOT include tools
                                        # Unset = match regardless of tools.

        message_count:                  # dict — number of messages in the conversation.
          min: 1                        #   int, optional — must have at least this many
          max: 10                       #   int, optional — must have at most this many

        conversation_id: "^proj-abc"    # str — Python re.search pattern applied to the
                                        # conversation_id field in the request body.
                                        # Unset = match any conversation.

      # ── Actions (all optional; unset keys are left unchanged) ────────────────
      action:

        # -- Routing ------------------------------------------------------------
        model: "qwen3:14b"              # str — override the model for this request.

        backend: "openrouter"           # str — force a specific named backend from
                                        # config.yaml backends[].name, bypassing normal
                                        # latency-aware / A-B selection entirely.

        route: "complex"                # str — resolve a named route alias defined under
                                        # decision_llm.routes in config.yaml.
                                        # e.g. "complex", "simple", "code".
                                        # Sets body["model"] to the route's model value.

        tools:                          # list[str] — tool names to force-run before the
          - web_search                  # LLM call.  Results injected as system context.
          - memory                      # Same tools available via z: search / z: memory.

        pass_through: false             # bool, default false.
                                        #   false → matched rule short-circuits the ML
                                        #            routing stack (classifier + decision LLM
                                        #            are skipped).
                                        #   true  → actions are applied but the ML stack
                                        #            still runs to pick / confirm the model.
                                        # Use true when you want to inject context or set
                                        # gen params without locking the model choice.

        # -- Generation parameters ----------------------------------------------
        # These override runtime_config gen_* defaults and per-model options, but
        # are still overridden by per-pane window config sent by the web UI.
        temperature: 0.2                # float 0.0–2.0
        top_p: 0.9                      # float 0.0–1.0
        top_k: 40                       # int
        num_ctx: 32768                  # int — context window in tokens
        max_tokens: 4096                # int — max output tokens
        repeat_penalty: 1.1             # float
        seed: 42                        # int — fixed seed for reproducibility

        # -- Context injection --------------------------------------------------
        # All three injection keys can be used together; their text is combined
        # in this order: inject_file → inject_context → system_prompt.
        # The combined text is prepended to the system message (creating one if
        # absent, otherwise prepending to the existing system message content).

        inject_file: "/path/to/context.md"
                                        # str — absolute path to a file read from disk
                                        # on EVERY matching request (hot — edit the file
                                        # mid-session and the next request gets the new
                                        # version immediately, no restart needed).
                                        # Useful for project READMEs, spec files, etc.

        inject_context: "Inline text injected into the system message."
                                        # str — literal text, cheaper alternative to
                                        # inject_file for short static context.

        system_prompt: "You are a concise code review assistant."
                                        # str — appended after inject_file / inject_context.
                                        # Use this for persona / style instructions.

        # -- Cache behaviour ----------------------------------------------------
        skip_session_cache: false       # bool, default false.
                                        #   true → the routing decision made by this rule
                                        #          is NOT stored in the session cache, so
                                        #          the next message in the same conversation
                                        #          goes through the full routing pipeline
                                        #          again instead of being sticky.

        skip_semantic_cache: false      # bool, default false.
                                        #   true → semantic cache lookup is bypassed for
                                        #          this request (always calls the backend).
                                        #          Useful when freshness matters more than
                                        #          speed (e.g. live data queries).

        # -- Observability ------------------------------------------------------
        tag: "code-request"             # str — attached to the wiretap log entry for this
                                        # request as routing_rule_tag.  Filter by it in
                                        # the Tap tab or grep the wire.jsonl file.


    # ── Example: inject a project README into every request from one key ──────
    - name: "always have project context"
      match:
        auth_key: "dev"
      action:
        inject_file: "/home/user/project/README.md"
        pass_through: true              # still let the classifier pick the model

    # ── Example: route code requests to a larger model ────────────────────────
    - name: "code → big model"
      priority: 10
      match:
        message: "^(fix|debug|refactor|implement|write.*function)"
      action:
        model: "qwen3:14b"
        temperature: 0.1
        num_ctx: 32768
        tag: "code"

    # ── Example: cheaper model + skip cache for summarisation ─────────────────
    - name: "summarise → fast + fresh"
      match:
        message_contains: "summarize"
        message_count: {max: 2}
      action:
        backend: "openrouter"
        model: "meta-llama/llama-3.1-8b-instruct"
        skip_semantic_cache: true

    # ── Example: inject search results for all requests, then let ML route ────
    - name: "always search"
      match:
        auth_key: "research"
      action:
        tools: [web_search]
        pass_through: true
        skip_session_cache: true

Internal body keys written by this module (all stripped before the request
reaches the backend):
    _bb_force_backend       — named backend override for MultiBackendRouter
    _bb_skip_semantic_cache — skip semantic cache lookup
    _bb_rule_tag            — wiretap tag from matched rule
    _bb_auth_key            — injected by main.py, stripped here before matching
    _bb_forced_tools        — list of tool names to run (applied by proxy.py)
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
# BB_AUTH_KEY is injected into the request body by ApiKeyMiddleware in main.py
# and stripped by evaluate_routing_rules() before any rule sees the body —
# it is never forwarded to the backend.
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
            # OpenAI vision format sends content as a list of typed parts
            # ({"type": "text", "text": "..."} and {"type": "image_url", ...}).
            # Extract and join only the text parts for pattern matching.
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
    """Return True iff every condition in match_spec is satisfied.

    Each condition short-circuits with return False on first mismatch —
    implementing AND semantics: all conditions must be true for the rule to fire.
    """

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
            # Merge without duplicates so multiple continue:true rules can each
            # contribute tool names and they accumulate correctly across rules.
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
        # All three injection keys combined in a fixed order (inject_file →
        # inject_context → system_prompt) and written with a single
        # _prepend_system_message call to keep the system message coherent
        # rather than triple-prepending with separate calls.
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
    # pop (not get) ensures the auth key is stripped from the body even if no
    # rule matches — it can never leak through to the backend under any path.
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

        # Default stop-on-first-match. continue:true allows effect layering:
        # one rule injects context, another overrides the model — both fire on
        # the same request and their effects accumulate on the body dict.
        if not rule.get("continue", False):
            break

    return body, matched_names, skip_session_cache, pass_through
