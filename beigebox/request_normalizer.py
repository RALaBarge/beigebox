"""Generic request normalizer for OpenAI-compatible chat completions.

The symmetric counterpart to response_normalizer.py. Every upstream BeigeBox
talks to claims the OpenAI chat-completion shape, but each one carves out a
slightly different dialect — different param names, different reasoning
toggles, different tolerance for prior-turn ``reasoning_content`` echoed
back in ``messages``. This module is the single chokepoint every outgoing
request runs through.

Design contract — read this before adding a new backend:

- The canonical BeigeBox shape is OpenAI-compatible chat completions.
  Everything inside BeigeBox speaks this dialect.
- This module's job is canonical → target-specific rewriting at egress.
- Rewrites are expressed as **rules** — small pure functions of
  ``(body, transforms) -> body``. A **profile** is an ordered list of
  rules for one target.
- Adding a new provider means defining a new ``TargetProfile`` from the
  built-in rule constructors (or your own); no module edits required.
- Every rule appends a human-readable tag to ``transforms`` describing
  what it changed. ``NormalizedRequest.transforms`` is the audit trail
  — wiretap can log it, tests can assert on it.
- Every function is total: never raises on malformed input. Errors land
  in ``NormalizedRequest.errors`` instead.

Built-in profiles live in ``DEFAULT_PROFILES`` and exist for the providers
we have today (openai_compat, openai_reasoning, openrouter, ollama,
anthropic). The dict is mutable — register your own with
``register_profile`` or by passing a ``profiles=`` override.
"""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


# A rule rewrites the body and records what it changed. Rules must be pure
# w.r.t. the input dict — never mutate ``body`` in place; return a new dict.
# They may freely append to ``transforms``.
Rule = Callable[[dict, list[str]], dict]


@dataclass
class TargetProfile:
    """An ordered list of rules applied for a named egress target."""

    name: str
    rules: list[Rule] = field(default_factory=list)

    def with_rules(self, *extra: Rule) -> "TargetProfile":
        """Return a copy with extra rules appended. Useful for ad-hoc tweaks."""
        return replace(self, rules=[*self.rules, *extra])


@dataclass
class NormalizedRequest:
    """Result of normalizing a request body for a specific target."""

    body: dict
    target: str
    transforms: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Generic primitives
# ---------------------------------------------------------------------------


def _replace_messages(body: dict, messages: list[dict]) -> dict:
    """Return a copy of ``body`` with ``messages`` swapped in."""
    return {**body, "messages": messages}


def _get_messages(body: dict) -> list[dict]:
    """Return body['messages'] as a list of dicts, or [] if malformed.

    Rules below assume messages have already been coerced by
    ``coerce_messages_rule``; if they haven't, we fall back to a permissive
    read so a misordered profile doesn't crash everything.
    """
    m = body.get("messages")
    if isinstance(m, list):
        return [x for x in m if isinstance(x, dict)]
    return []


# ---------------------------------------------------------------------------
# Rule constructors (built-ins)
# ---------------------------------------------------------------------------


def coerce_messages_rule() -> Rule:
    """Clean up ``body["messages"]`` into a list of role-bearing dicts.

    - None → empty list (records ``no_messages`` *not* an error here, since
      callers may legitimately send empty messages; the caller can validate).
    - dict → wrapped to a single-item list.
    - non-list → empty list.
    - non-dict items dropped.
    - dicts missing/empty ``role`` get ``role="user"``.
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        msgs = body.get("messages")
        if msgs is None:
            return _replace_messages(body, [])
        if isinstance(msgs, dict):
            transforms.append("messages_wrapped_to_list")
            msgs = [msgs]
        if not isinstance(msgs, list):
            transforms.append("messages_replaced_with_empty:not_a_list")
            return _replace_messages(body, [])

        cleaned: list[dict] = []
        dropped = 0
        role_defaulted = 0
        for m in msgs:
            if not isinstance(m, dict):
                dropped += 1
                continue
            role = m.get("role")
            if not isinstance(role, str) or not role:
                m = {**m, "role": "user"}
                role_defaulted += 1
            cleaned.append(m)
        if dropped:
            transforms.append(f"dropped_non_dict_messages:{dropped}")
        if role_defaulted:
            transforms.append(f"defaulted_missing_role:{role_defaulted}")
        return _replace_messages(body, cleaned)

    _rule.__bb_name__ = "coerce_messages"  # type: ignore[attr-defined]
    return _rule


def strip_message_fields_rule(fields_to_strip: Iterable[str]) -> Rule:
    """Remove the named keys from every message in ``messages``.

    Default use: strip prior-turn ``reasoning_content`` / ``reasoning`` /
    ``thinking`` from echoed assistant messages — DeepSeek-R1 docs require
    this, others ignore them.
    """
    keys = tuple(fields_to_strip)

    def _rule(body: dict, transforms: list[str]) -> dict:
        msgs = _get_messages(body)
        if not msgs:
            return body
        stripped = 0
        out: list[dict] = []
        for m in msgs:
            if any(k in m for k in keys):
                out.append({k: v for k, v in m.items() if k not in keys})
                stripped += 1
            else:
                out.append(m)
        if stripped:
            transforms.append(f"stripped_message_fields:{','.join(keys)}:{stripped}")
        return _replace_messages(body, out)

    _rule.__bb_name__ = f"strip_message_fields:{','.join(keys)}"  # type: ignore[attr-defined]
    return _rule


def collapse_system_messages_rule(mode: str = "concat") -> Rule:
    """Collapse multiple ``role: system`` messages into one leading message.

    ``mode="concat"`` joins their text content with blank-line separators;
    ``mode="first"`` keeps the first system message and drops later ones.
    Any other value disables the rule (no-op).
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        if mode not in ("concat", "first"):
            return body
        msgs = _get_messages(body)
        sys_idx = [i for i, m in enumerate(msgs) if m.get("role") == "system"]
        if len(sys_idx) <= 1:
            return body

        if mode == "first":
            keep = sys_idx[0]
            out = [m for i, m in enumerate(msgs) if m.get("role") != "system" or i == keep]
            transforms.append(f"dropped_extra_system_messages:{len(sys_idx) - 1}")
            return _replace_messages(body, out)

        parts: list[str] = []
        for i in sys_idx:
            c = msgs[i].get("content")
            if isinstance(c, str) and c:
                parts.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        t = p.get("text")
                        if isinstance(t, str) and t:
                            parts.append(t)
        merged = "\n\n".join(parts)

        out: list[dict] = []
        first_seen = False
        for m in msgs:
            if m.get("role") == "system":
                if not first_seen:
                    out.append({**m, "content": merged})
                    first_seen = True
            else:
                out.append(m)
        transforms.append(f"merged_system_messages:{len(sys_idx)}")
        return _replace_messages(body, out)

    _rule.__bb_name__ = f"collapse_system_messages:{mode}"  # type: ignore[attr-defined]
    return _rule


def drop_keys_rule(keys: Iterable[str], reason: str = "unsupported") -> Rule:
    """Remove named top-level keys from the body."""
    keys_set = frozenset(keys)

    def _rule(body: dict, transforms: list[str]) -> dict:
        removed = [k for k in body if k in keys_set]
        if not removed:
            return body
        transforms.append(f"dropped:{reason}:{','.join(sorted(removed))}")
        return {k: v for k, v in body.items() if k not in keys_set}

    _rule.__bb_name__ = f"drop_keys:{reason}"  # type: ignore[attr-defined]
    return _rule


def rename_key_rule(old: str, new: str, *, on_conflict: str = "prefer_new") -> Rule:
    """Rename a top-level key.

    ``on_conflict="prefer_new"`` (default): if both ``old`` and ``new`` are
    present, drop ``old`` and keep ``new``.
    ``on_conflict="prefer_old"``: drop ``new`` and rename ``old``.
    ``on_conflict="skip"``: leave both alone.
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        if old not in body:
            return body
        if new in body:
            if on_conflict == "prefer_new":
                transforms.append(f"dropped:{old}(superseded_by:{new})")
                return {k: v for k, v in body.items() if k != old}
            if on_conflict == "prefer_old":
                out = {k: v for k, v in body.items() if k not in (old, new)}
                out[new] = body[old]
                transforms.append(f"renamed:{old}->{new}(replaced_existing)")
                return out
            return body  # skip
        out = {(new if k == old else k): v for k, v in body.items()}
        transforms.append(f"renamed:{old}->{new}")
        return out

    _rule.__bb_name__ = f"rename_key:{old}->{new}"  # type: ignore[attr-defined]
    return _rule


def set_nested_default_rule(path: tuple[str, ...], value: Any, *, only_if: Callable[[dict], bool] | None = None) -> Rule:
    """Set ``body[path[0]][path[1]]...`` to ``value`` if not already set.

    ``only_if`` gates whether the rule fires (e.g. only when streaming).
    Used for things like setting ``stream_options.include_usage = True``.
    """
    if not path:
        raise ValueError("set_nested_default_rule requires a non-empty path")

    def _rule(body: dict, transforms: list[str]) -> dict:
        if only_if is not None and not only_if(body):
            return body
        # Navigate, copying along the way so we don't mutate input.
        out = {**body}
        cursor = out
        for k in path[:-1]:
            child = cursor.get(k)
            if not isinstance(child, dict):
                child = {}
            else:
                child = {**child}
            cursor[k] = child
            cursor = child
        last = path[-1]
        if cursor.get(last) == value:
            return body  # already set; no transform recorded
        cursor[last] = value
        transforms.append(f"set:{'.'.join(path)}={value}")
        return out

    _rule.__bb_name__ = f"set_nested_default:{'.'.join(path)}"  # type: ignore[attr-defined]
    return _rule


def canonicalize_tools_rule() -> Rule:
    """Canonicalize ``body["tools"]`` to the OpenAI shape.

    - Each entry must be ``{"type": "function", "function": {"name": str,
      "description": str, "parameters": dict}}``.
    - Missing ``type`` defaults to ``"function"``.
    - Entries with no string ``function.name`` are dropped.
    - Missing ``description`` defaults to ``""``.
    - Missing ``parameters`` defaults to a permissive empty schema.
    - Empty list / non-list / fully-invalid contents drop the key entirely
      so providers don't see ``tools: []`` (which some reject).
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        tools = body.get("tools")
        if tools is None:
            return body
        if not isinstance(tools, list):
            transforms.append("dropped:tools(not_a_list)")
            return {k: v for k, v in body.items() if k != "tools"}

        cleaned: list[dict] = []
        dropped = 0
        for t in tools:
            if not isinstance(t, dict):
                dropped += 1
                continue
            fn = t.get("function")
            if not isinstance(fn, dict):
                dropped += 1
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                dropped += 1
                continue
            desc = fn.get("description")
            params = fn.get("parameters")
            new_fn = {
                "name": name,
                "description": desc if isinstance(desc, str) else "",
                "parameters": params if isinstance(params, dict) else {"type": "object", "properties": {}},
            }
            cleaned.append({"type": "function", "function": new_fn})

        if dropped:
            transforms.append(f"dropped_invalid_tools:{dropped}")
        if not cleaned:
            transforms.append("dropped:tools(empty_after_canonicalization)")
            return {k: v for k, v in body.items() if k != "tools"}
        # Only emit a transform if anything actually changed.
        if cleaned != tools:
            transforms.append("canonicalized_tools")
        return {**body, "tools": cleaned}

    _rule.__bb_name__ = "canonicalize_tools"  # type: ignore[attr-defined]
    return _rule


def canonicalize_tool_choice_rule() -> Rule:
    """Validate ``body["tool_choice"]`` shape; drop if malformed.

    Valid shapes (OpenAI canonical):
    - ``"auto" | "required" | "none"``
    - ``{"type": "function", "function": {"name": str}}``
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        tc = body.get("tool_choice")
        if tc is None:
            return body
        if isinstance(tc, str) and tc in ("auto", "required", "none"):
            return body
        if isinstance(tc, dict):
            fn = tc.get("function")
            if (
                tc.get("type") == "function"
                and isinstance(fn, dict)
                and isinstance(fn.get("name"), str)
                and fn.get("name")
            ):
                return body
        transforms.append("dropped:tool_choice(invalid_shape)")
        return {k: v for k, v in body.items() if k != "tool_choice"}

    _rule.__bb_name__ = "canonicalize_tool_choice"  # type: ignore[attr-defined]
    return _rule


def canonicalize_tool_messages_rule() -> Rule:
    """Canonicalize tool-call assistant messages and tool-result messages.

    Per-message rules:

    - ``role=assistant`` carrying ``tool_calls``: each call gets
      ``{"id": str, "type": "function", "function": {"name": str,
      "arguments": str}}``. Missing ids are synthesized
      (``call_<msg_idx>_<call_idx>``). Dict-valued ``arguments`` are
      JSON-encoded (OpenAI spec mandates a string).
    - ``role=tool`` (a tool result): must have ``tool_call_id`` (string)
      and string ``content``. Messages missing ``tool_call_id`` are
      dropped. Non-string ``content`` is JSON-encoded.
    - Other roles are passed through.
    """

    def _rule(body: dict, transforms: list[str]) -> dict:
        msgs = _get_messages(body)
        if not msgs:
            return body

        out: list[dict] = []
        synthesized_ids = 0
        coerced_args = 0
        dropped_tool_msgs = 0
        coerced_tool_content = 0

        for idx, m in enumerate(msgs):
            role = m.get("role")

            if role == "assistant" and isinstance(m.get("tool_calls"), list):
                new_calls: list[dict] = []
                for tc_idx, tc in enumerate(m["tool_calls"]):
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    args = fn.get("arguments")
                    if isinstance(args, dict) or isinstance(args, list):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                            coerced_args += 1
                        except (TypeError, ValueError):
                            args = "{}"
                    elif args is None:
                        args = ""
                    elif not isinstance(args, str):
                        args = str(args)
                    tc_id = tc.get("id")
                    if not isinstance(tc_id, str) or not tc_id:
                        tc_id = f"call_{idx}_{tc_idx}"
                        synthesized_ids += 1
                    tc_type = tc.get("type")
                    new_calls.append({
                        "id": tc_id,
                        "type": tc_type if isinstance(tc_type, str) and tc_type else "function",
                        "function": {"name": name, "arguments": args},
                    })
                if new_calls:
                    out.append({**m, "tool_calls": new_calls})
                else:
                    # All tool_calls were invalid — keep the message but drop the key
                    # so the provider sees a plain assistant turn.
                    out.append({k: v for k, v in m.items() if k != "tool_calls"})
                continue

            if role == "tool":
                tc_id = m.get("tool_call_id")
                if not isinstance(tc_id, str) or not tc_id:
                    dropped_tool_msgs += 1
                    continue
                content = m.get("content")
                if not isinstance(content, str):
                    if isinstance(content, (dict, list)):
                        try:
                            content = json.dumps(content, ensure_ascii=False)
                            coerced_tool_content += 1
                        except (TypeError, ValueError):
                            content = str(content)
                    elif content is None:
                        content = ""
                    else:
                        content = str(content)
                        coerced_tool_content += 1
                out.append({**m, "content": content})
                continue

            out.append(m)

        if synthesized_ids:
            transforms.append(f"synthesized_tool_call_ids:{synthesized_ids}")
        if coerced_args:
            transforms.append(f"coerced_tool_arguments_to_string:{coerced_args}")
        if coerced_tool_content:
            transforms.append(f"coerced_tool_result_content_to_string:{coerced_tool_content}")
        if dropped_tool_msgs:
            transforms.append(f"dropped_tool_messages_missing_id:{dropped_tool_msgs}")

        return _replace_messages(body, out) if out != msgs else body

    _rule.__bb_name__ = "canonicalize_tool_messages"  # type: ignore[attr-defined]
    return _rule


def drop_tools_rule(reason: str = "tools_unsupported") -> Rule:
    """Strip ``tools`` and ``tool_choice`` — for targets without tool support."""

    def _rule(body: dict, transforms: list[str]) -> dict:
        removed = [k for k in ("tools", "tool_choice") if k in body]
        if not removed:
            return body
        transforms.append(f"dropped:{reason}:{','.join(removed)}")
        return {k: v for k, v in body.items() if k not in removed}

    _rule.__bb_name__ = f"drop_tools:{reason}"  # type: ignore[attr-defined]
    return _rule


# ---------------------------------------------------------------------------
# Detection (genericized — patterns are data)
# ---------------------------------------------------------------------------


# Substrings that identify a model id as a reasoning model. Append your own
# at runtime — this list is mutable on purpose.
DEFAULT_REASONING_MARKERS: list[str] = [
    "o1-",
    "o1mini",
    "o1-mini",
    "o3-",
    "o3mini",
    "o3-mini",
    "o4-",
    "o4-mini",
    "gpt-5-thinking",
    "deepseek-r1",
    "deepseek-reasoner",
    "qwq-",
    "trinity-thinking",
    "-thinking",
]


def is_reasoning_model(model: Any, markers: Iterable[str] | None = None) -> bool:
    """True iff ``model`` is a string containing any of ``markers``.

    Defaults to ``DEFAULT_REASONING_MARKERS``. Pass a custom iterable to
    extend or replace the default list for one call.
    """
    if not isinstance(model, str) or not model:
        return False
    src = markers if markers is not None else DEFAULT_REASONING_MARKERS
    lowered = model.lower()
    return any(m in lowered for m in src)


# ---------------------------------------------------------------------------
# Built-in profiles (assembled from the rule constructors above)
# ---------------------------------------------------------------------------


# Sampling params OpenAI o-series rejects.
_OPENAI_REASONING_DROP: tuple[str, ...] = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "logprobs",
    "top_logprobs",
)


def _default_cross_cutting_rules() -> list[Rule]:
    """The rules every profile starts with unless explicitly overridden."""
    return [
        coerce_messages_rule(),
        strip_message_fields_rule(("reasoning_content", "reasoning", "thinking")),
        collapse_system_messages_rule("concat"),
        canonicalize_tools_rule(),
        canonicalize_tool_choice_rule(),
        canonicalize_tool_messages_rule(),
    ]


def _build_default_profiles() -> dict[str, TargetProfile]:
    base = _default_cross_cutting_rules

    return {
        "openai_compat": TargetProfile(name="openai_compat", rules=base()),
        "openai_reasoning": TargetProfile(
            name="openai_reasoning",
            rules=[
                *base(),
                drop_keys_rule(_OPENAI_REASONING_DROP, reason="o_series"),
                rename_key_rule("max_tokens", "max_completion_tokens"),
            ],
        ),
        "openrouter": TargetProfile(
            name="openrouter",
            rules=[
                *base(),
                set_nested_default_rule(
                    ("stream_options", "include_usage"),
                    True,
                    only_if=lambda b: bool(b.get("stream")),
                ),
            ],
        ),
        "ollama": TargetProfile(name="ollama", rules=base()),
        # Anthropic native is not yet implemented (we proxy via openai-compat
        # shims today). The profile keeps the cross-cutting rules but skips
        # reasoning-strip — Anthropic's signed thinking blocks must round-trip.
        "anthropic": TargetProfile(
            name="anthropic",
            rules=[
                coerce_messages_rule(),
                collapse_system_messages_rule("concat"),
                canonicalize_tools_rule(),
                canonicalize_tool_choice_rule(),
                canonicalize_tool_messages_rule(),
            ],
        ),
    }


DEFAULT_PROFILES: dict[str, TargetProfile] = _build_default_profiles()

# Guards reads/writes of DEFAULT_PROFILES. The registry is intended to be
# stable post-startup, but the ABI lets callers register at any time, so we
# protect both paths against torn dicts under concurrent registrations.
_REGISTRY_LOCK = threading.Lock()


def register_profile(profile: TargetProfile, *, registry: dict[str, TargetProfile] | None = None) -> None:
    """Register a profile so it can be addressed by name. Mutates the registry in place.

    Thread-safe against concurrent register_profile calls and against
    normalize_request reading DEFAULT_PROFILES at the same time.
    """
    target = registry if registry is not None else DEFAULT_PROFILES
    with _REGISTRY_LOCK:
        target[profile.name] = profile


# Default model→profile autodetection. First match wins. List items are
# (predicate, profile_name) tuples; the predicate is called with the model id.
DEFAULT_MODEL_PROFILE_RULES: list[tuple[Callable[[str], bool], str]] = [
    (lambda m: is_reasoning_model(m), "openai_reasoning"),
]


def _resolve_profile(
    target: str | TargetProfile | None,
    model: Any,
    profiles: dict[str, TargetProfile],
    autodetect: list[tuple[Callable[[str], bool], str]],
) -> tuple[TargetProfile, str | None]:
    """Pick the effective profile.

    Returns (profile, redirect_note). redirect_note is non-None when the
    caller's target was changed by autodetection — used as a transform tag.
    """
    if isinstance(target, TargetProfile):
        return target, None

    # Explicit string target wins over autodetect — but openai_compat is
    # ambiguous (vanilla vs reasoning), so we still let autodetect upgrade it.
    if isinstance(target, str) and target in profiles:
        if target == "openai_compat" and isinstance(model, str):
            for predicate, redirect in autodetect:
                if redirect != "openai_compat" and redirect in profiles and predicate(model):
                    return profiles[redirect], f"target_resolved:{target}->{redirect}"
        return profiles[target], None

    # Unknown / None target: try autodetect, then fall back to openai_compat.
    if isinstance(model, str):
        for predicate, redirect in autodetect:
            if redirect in profiles and predicate(model):
                return profiles[redirect], f"target_resolved:auto->{redirect}"

    fallback = profiles.get("openai_compat") or TargetProfile(name="openai_compat", rules=_default_cross_cutting_rules())
    note = None
    if isinstance(target, str) and target not in profiles:
        note = f"target_unknown:{target}->openai_compat"
    return fallback, note


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def normalize_request(
    body: dict | None,
    target: str | TargetProfile | None = None,
    *,
    profiles: dict[str, TargetProfile] | None = None,
    autodetect: list[tuple[Callable[[str], bool], str]] | None = None,
    extra_rules: Iterable[Rule] = (),
) -> NormalizedRequest:
    """Rewrite a chat-completion request body for the chosen target.

    Pipeline guarantees (in order — rules within a profile compose):

        1. The caller's ``body`` is **deepcopy'd** at entry. Rules mutate the
           copy freely; the caller's dict is never modified in place.
        2. The profile's rules run in the order declared on
           ``TargetProfile.rules``. Convention is::

               messages_coerce
                  → strip_message_fields  (drop prior-turn reasoning)
                  → collapse_system
                  → canonicalize_tools / tool_choice / tool_messages
                  → drop / rename / set_default  (provider-specific)

           Cross-cutting transforms (messages, tools) come before
           provider-specific transforms (rename ``max_tokens`` →
           ``max_completion_tokens`` for o-series, set
           ``stream_options.include_usage`` for OpenRouter, etc.) so each
           provider's rules see canonical inputs.
        3. ``extra_rules`` run after the profile's rules — one-off tweaks
           without defining a whole profile.
        4. A single rule that raises is caught, logged into ``errors``,
           and skipped; subsequent rules still run.

    Args:
        body: Canonical OpenAI-compat request dict. None / non-dict yields an
            empty body with an error tag.
        target: Profile name (looked up in ``profiles``) or a TargetProfile
            instance, or None to autodetect from ``body["model"]``.
        profiles: Override registry. Defaults to ``DEFAULT_PROFILES``.
        autodetect: Override autodetect rules. Defaults to
            ``DEFAULT_MODEL_PROFILE_RULES``.
        extra_rules: Rules appended after the profile's rules — handy for
            one-off tweaks without defining a whole profile.

    Returns:
        ``NormalizedRequest`` with the rewritten body and a transform log.
    """
    transforms: list[str] = []
    errors: list[str] = []

    if not isinstance(body, dict):
        errors.append("not_a_dict")
        return NormalizedRequest(
            body={},
            target=target.name if isinstance(target, TargetProfile) else (target or "openai_compat"),
            transforms=transforms,
            errors=errors,
            raw={},
        )

    raw = body
    # Deepcopy at pipeline entry — rules are free to mutate; caller's dict
    # stays pristine. (Panel-convergent: multiple reviewers flagged in-place
    # mutation of the caller's request body as a latent footgun, especially
    # for clients reusing a request template across tries.)
    body = copy.deepcopy(body)

    if profiles is not None:
        registry = profiles
    else:
        with _REGISTRY_LOCK:
            registry = dict(DEFAULT_PROFILES)
    detect = autodetect if autodetect is not None else DEFAULT_MODEL_PROFILE_RULES
    profile, redirect = _resolve_profile(target, body.get("model"), registry, detect)
    if redirect:
        transforms.append(redirect)

    out = body
    for rule in (*profile.rules, *extra_rules):
        try:
            out = rule(out, transforms)
        except Exception as exc:  # rules must never break the pipeline
            name = getattr(rule, "__bb_name__", rule.__class__.__name__)
            errors.append(f"rule_failed:{name}:{exc.__class__.__name__}")

    return NormalizedRequest(
        body=out,
        target=profile.name,
        transforms=transforms,
        errors=errors,
        raw=raw,
    )
