"""
Tool-call extraction shim — recover tool invocations a model emitted as text.

Some models don't reliably populate the OpenAI `tool_calls` array even when
they clearly intended to call a tool: they wrap the call in Anthropic-style
XML, in a fenced JSON block, in LangChain Action/Action-Input pairs, or just
spit a JSON object out as the whole response. This module is a fallback —
when `normalize_response` sees no structured tool_calls but content that
looks like one or more, the extractor pipeline tries to lift them into the
canonical OpenAI shape.

Off by default. Opt in per call:
    normalize_response(data, enable_tool_call_extraction=True)
or per-profile by setting `enable_tool_call_extraction: True` on the
TargetProfile (caller does that wiring).

Each extractor is a pure function:
    extract(content: str, declared_tools: set[str] | None,
            errors: list[str]) -> tuple[list[dict], str] | None

Returns (lifted_calls, content_with_match_spans_stripped) on a hit,
None on a miss. Never raises — failures append to `errors`.

The pipeline runs extractors in priority order; the first to return a
non-None hit wins. Accumulating across extractors invites duplicates
(same call wrapped in both XML and a fence). Multiple calls within ONE
extractor's match are fine and expected.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable

# A canonical OpenAI tool_call dict:
#   {"id": "call_<12hex>", "type": "function",
#    "function": {"name": str, "arguments": "<json-string>"},
#    "_extracted_from": "<extractor_name>"}

Extractor = Callable[[str, "set[str] | None", "list[str]"],
                     "tuple[list[dict], str] | None"]


# Hard cap on content length the extractor pipeline will consider. Several
# patterns use `.*?` with DOTALL; without a length bound, an adversarial
# model output (long alternating brackets, deeply-nested fences) can force
# catastrophic backtracking. 256 KiB is comfortably above any realistic
# tool-call payload — anything larger is almost certainly not a single tool
# call worth lifting and isn't worth the risk.
MAX_EXTRACTION_CHARS = 256 * 1024


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]{0,63}$")
_PLACEHOLDER_NAMES = frozenset({
    "example", "schema", "tool", "function", "your_tool_name",
    "tool_name", "function_name",
})
_PLACEHOLDER_VALUE_RE = re.compile(r"<[A-Z_]{2,}>|^\.\.\.$")


def _valid_name(name: str | None, declared_tools: set[str] | None) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if name.lower() in _PLACEHOLDER_NAMES:
        return False
    if not _NAME_RE.match(name):
        return False
    if declared_tools is not None and name not in declared_tools:
        return False
    return True


def _has_placeholder_args(args: dict) -> bool:
    """Reject {"...": "..."} or {"key": "<INSERT_X>"} style template stubs."""
    if not isinstance(args, dict):
        return False
    for v in args.values():
        if isinstance(v, str) and _PLACEHOLDER_VALUE_RE.search(v):
            return True
    return False


def _make_call(name: str, arguments: dict | str, ordinal: int,
               extractor_name: str) -> dict:
    """Build a canonical OpenAI-shape tool_call entry.

    `arguments` is JSON-serialized to a string per OpenAI semantics. id is
    deterministic from (name, args, ordinal) so re-runs of the same extraction
    produce stable ids.
    """
    if isinstance(arguments, dict):
        args_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    elif isinstance(arguments, str):
        # Trust callers that already canonicalized. Validate it's at least JSON.
        try:
            json.loads(arguments)
            args_json = arguments
        except (json.JSONDecodeError, ValueError):
            args_json = json.dumps({"_raw": arguments})
    else:
        args_json = json.dumps({"_raw": str(arguments)})

    seed = f"{extractor_name}|{name}|{args_json}|{ordinal}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"call_{digest}",
        "type": "function",
        "function": {"name": name, "arguments": args_json},
        "_extracted_from": extractor_name,
    }


def _replace_spans(content: str, spans: list[tuple[int, int]]) -> str:
    """Remove the (start,end) char ranges from content; collapse extra blank lines."""
    if not spans:
        return content
    spans = sorted(spans)
    out = []
    cursor = 0
    for s, e in spans:
        if s > cursor:
            out.append(content[cursor:s])
        cursor = e
    out.append(content[cursor:])
    stripped = "".join(out)
    # Collapse three+ newlines into two; trim
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped


# ─────────────────────────────────────────────────────────────────────────────
# Extractors — priority order top→bottom
# ─────────────────────────────────────────────────────────────────────────────


_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls>\s*(?P<body>.*?)\s*</function_calls>",
    re.DOTALL | re.IGNORECASE,
)
_INVOKE_RE = re.compile(
    r"<invoke\s+name=\"(?P<name>[^\"]+)\"\s*>(?P<body>.*?)</invoke>",
    re.DOTALL | re.IGNORECASE,
)
_PARAMETER_RE = re.compile(
    r"<parameter\s+name=\"(?P<key>[^\"]+)\"\s*>(?P<val>.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)


def _extract_anthropic_function_calls(content, declared_tools, errors):
    try:
        outer = _FUNCTION_CALLS_RE.search(content)
        if not outer:
            return None
        calls: list[dict] = []
        for ord_, m in enumerate(_INVOKE_RE.finditer(outer.group("body"))):
            name = m.group("name").strip()
            if not _valid_name(name, declared_tools):
                continue
            args = {}
            for pm in _PARAMETER_RE.finditer(m.group("body")):
                args[pm.group("key").strip()] = pm.group("val").strip()
            if _has_placeholder_args(args):
                continue
            calls.append(_make_call(name, args, ord_, "anthropic_function_calls"))
        if not calls:
            return None
        return calls, _replace_spans(content, [outer.span()])
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:anthropic_function_calls:{type(e).__name__}")
        return None


_TOOL_USE_RE = re.compile(
    r"<tool_use>\s*<name>(?P<name>[^<]+)</name>\s*"
    r"<input>(?P<input>.*?)</input>\s*</tool_use>",
    re.DOTALL | re.IGNORECASE,
)


def _extract_anthropic_tool_use(content, declared_tools, errors):
    try:
        matches = list(_TOOL_USE_RE.finditer(content))
        if not matches:
            return None
        calls, spans = [], []
        for ord_, m in enumerate(matches):
            name = m.group("name").strip()
            if not _valid_name(name, declared_tools):
                continue
            try:
                args = json.loads(m.group("input").strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(args, dict) or _has_placeholder_args(args):
                continue
            calls.append(_make_call(name, args, ord_, "anthropic_tool_use"))
            spans.append(m.span())
        if not calls:
            return None
        return calls, _replace_spans(content, spans)
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:anthropic_tool_use:{type(e).__name__}")
        return None


_EXPLICIT_MARKER_RE = re.compile(
    r"<\|tool_call\|>(?P<body>.*?)<\|/tool_call\|>"
    r"|<tool_call>(?P<body2>.*?)</tool_call>",
    re.DOTALL,
)


def _extract_explicit_markers(content, declared_tools, errors):
    try:
        matches = list(_EXPLICIT_MARKER_RE.finditer(content))
        if not matches:
            return None
        calls, spans = [], []
        for ord_, m in enumerate(matches):
            body = (m.group("body") or m.group("body2") or "").strip()
            try:
                obj = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            for c in _coerce_call_objects(obj, declared_tools, ord_,
                                          "explicit_markers"):
                calls.append(c)
            spans.append(m.span())
        if not calls:
            return None
        return calls, _replace_spans(content, spans)
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:explicit_markers:{type(e).__name__}")
        return None


_FENCED_HINT_RE = re.compile(
    r"```(?P<hint>tool_call|tool_use|function_call|json)\s*\n"
    r"(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_fenced(content, declared_tools, errors):
    try:
        matches = list(_FENCED_HINT_RE.finditer(content))
        if not matches:
            return None
        calls, spans = [], []
        ord_ = 0
        for m in matches:
            hint = m.group("hint").lower()
            body = m.group("body").strip()
            try:
                obj = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                continue
            # A `json` hint requires the body to sniff as a tool call.
            # `tool_call` / `tool_use` / `function_call` hints are explicit.
            require_sniff = (hint == "json")
            for c in _coerce_call_objects(
                obj, declared_tools, ord_, "fenced_hint",
                require_sniff=require_sniff,
            ):
                calls.append(c)
                ord_ += 1
            spans.append(m.span())
        if not calls:
            return None
        return calls, _replace_spans(content, spans)
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:fenced:{type(e).__name__}")
        return None


_LANGCHAIN_RE = re.compile(
    r"^Action:\s*(?P<name>\S+)\s*\n+\s*Action Input:\s*(?P<input>.+?)"
    r"(?=\n\s*(?:Action|Observation|Thought|Final Answer)\s*:|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _extract_langchain(content, declared_tools, errors):
    try:
        matches = list(_LANGCHAIN_RE.finditer(content))
        if not matches:
            return None
        calls, spans = [], []
        for ord_, m in enumerate(matches):
            name = m.group("name").strip().rstrip(",.")
            input_str = m.group("input").strip()
            if not _valid_name(name, declared_tools):
                continue
            try:
                args = json.loads(input_str)
                if not isinstance(args, dict):
                    args = {"input": input_str}
            except (json.JSONDecodeError, ValueError):
                args = {"input": input_str}
            if _has_placeholder_args(args):
                continue
            calls.append(_make_call(name, args, ord_, "langchain"))
            spans.append(m.span())
        if not calls:
            return None
        return calls, _replace_spans(content, spans)
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:langchain:{type(e).__name__}")
        return None


_REACT_RE = re.compile(
    r"^Action:\s*(?P<name>[a-zA-Z_][a-zA-Z0-9_.\-]*)\s*"
    r"\[(?P<args>[^\]]*)\]\s*$",
    re.MULTILINE,
)


def _extract_react(content, declared_tools, errors):
    try:
        matches = list(_REACT_RE.finditer(content))
        if not matches:
            return None
        calls, spans = [], []
        for ord_, m in enumerate(matches):
            name = m.group("name").strip()
            if not _valid_name(name, declared_tools):
                continue
            args_str = m.group("args").strip()
            args: dict = {}
            for kv in re.split(r",\s*", args_str) if args_str else []:
                if "=" not in kv:
                    continue
                k, _, v = kv.partition("=")
                v = v.strip().strip('"').strip("'")
                args[k.strip()] = v
            calls.append(_make_call(name, args, ord_, "react"))
            spans.append(m.span())
        if not calls:
            return None
        return calls, _replace_spans(content, spans)
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:react:{type(e).__name__}")
        return None


def _extract_bare_json(content, declared_tools, errors):
    """Last-resort: content is essentially-only-JSON and sniffs as a tool call.

    Tolerates a short non-JSON prefix (≤ 16 chars AND < 15% of stripped length)
    to handle prefix-glitch tokens some models emit before the actual tool call
    JSON (observed: a Hebrew artifact " מדה\\n" before clean JSON from
    qwen3-next-80b). The remaining JSON must still cover ≥ 60% of stripped
    content. Anything longer than that is treated as "JSON example embedded
    in prose" and rejected.
    """
    try:
        stripped = content.strip()
        if not stripped:
            return None
        # Locate the earliest '{' or '['
        first_brace = -1
        for j, ch in enumerate(stripped):
            if ch in "{[":
                first_brace = j
                break
        if first_brace < 0:
            return None
        prefix = stripped[:first_brace]
        if len(prefix) > 16 and len(prefix) > 0.15 * len(stripped):
            return None
        json_part = stripped[first_brace:]
        try:
            obj = json.loads(json_part)
        except (json.JSONDecodeError, ValueError):
            return None
        # Coverage: the JSON span must dominate
        if len(json_part) / max(len(stripped), 1) < 0.6:
            return None
        calls = list(_coerce_call_objects(
            obj, declared_tools, 0, "bare_json", require_sniff=True,
        ))
        if not calls:
            return None
        # Strip the whole matched region — the model returned essentially only this
        return calls, ""
    except (re.error, AttributeError) as e:
        errors.append(f"extractor_failed:bare_json:{type(e).__name__}")
        return None


DEFAULT_EXTRACTOR_PIPELINE: list[Extractor] = [
    _extract_anthropic_function_calls,
    _extract_anthropic_tool_use,
    _extract_explicit_markers,
    _extract_fenced,
    _extract_langchain,
    _extract_react,
    _extract_bare_json,
]


# ─────────────────────────────────────────────────────────────────────────────
# Shared coercion: turn a parsed JSON blob into 0+ tool_call dicts
# ─────────────────────────────────────────────────────────────────────────────


def _coerce_call_objects(obj, declared_tools, start_ordinal, extractor_name,
                         require_sniff: bool = False):
    """Yield canonical tool_calls from a parsed JSON object or list."""
    if isinstance(obj, list):
        ord_ = start_ordinal
        for item in obj:
            yield from _coerce_call_objects(
                item, declared_tools, ord_, extractor_name, require_sniff,
            )
            ord_ += 1
        return
    if not isinstance(obj, dict):
        return

    # Accept several common shapes:
    #   {"name": ..., "arguments": {...}}
    #   {"name": ..., "input": {...}}
    #   {"name": ..., "parameters": {...}}
    #   {"tool": ..., "arguments": {...}}
    #   {"function": ..., "arguments": {...}}
    #   {"function": {"name": ..., "arguments": ...}}
    #   {"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}
    name = None
    args = None

    if isinstance(obj.get("function"), dict):
        inner = obj["function"]
        name = inner.get("name")
        args = inner.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                pass
    if name is None:
        for nk in ("name", "tool", "function"):
            if isinstance(obj.get(nk), str):
                name = obj[nk]
                break
    if args is None:
        for ak in ("arguments", "input", "parameters", "args"):
            if ak in obj:
                args = obj[ak]
                break

    if require_sniff:
        # When the format is ambiguous (json fence, bare json), only accept
        # if we have BOTH a clean name and a dict-like args bag.
        if not isinstance(args, dict):
            return
        if not _valid_name(name, declared_tools):
            return
    else:
        if not _valid_name(name, declared_tools):
            return
        if args is None:
            args = {}

    if isinstance(args, dict) and _has_placeholder_args(args):
        return

    yield _make_call(name, args if args is not None else {},
                     start_ordinal, extractor_name)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline driver
# ─────────────────────────────────────────────────────────────────────────────


def extract_tool_calls(
    content: str,
    *,
    declared_tools: set[str] | None = None,
    extractors: list[Extractor] | None = None,
    errors: list[str] | None = None,
) -> tuple[list[dict] | None, str]:
    """Run the extractor pipeline over `content`.

    Returns:
        (tool_calls, rewritten_content)
        - tool_calls: list of canonical OpenAI tool_call dicts, or None on miss
        - rewritten_content: content with matched spans stripped (unchanged on miss)

    First-extractor-wins: as soon as one extractor returns a non-empty list,
    we stop. Each extractor independently traps its own exceptions and pushes
    a string onto `errors` rather than raising.
    """
    if not isinstance(content, str) or not content.strip():
        return None, content if isinstance(content, str) else ""
    if errors is None:
        errors = []
    # ReDoS guard: refuse to run pattern-matching extractors on oversized
    # content. Fall straight through to a no-op rather than burn CPU on a
    # backtracking storm.
    if len(content) > MAX_EXTRACTION_CHARS:
        errors.append(f"extraction_skipped:content_too_large:{len(content)}")
        return None, content
    pipeline = extractors if extractors is not None else DEFAULT_EXTRACTOR_PIPELINE
    for ex in pipeline:
        result = ex(content, declared_tools, errors)
        if result is not None:
            calls, rewritten = result
            if calls:
                if rewritten != content:
                    errors.append(f"content_rewritten:{ex.__name__}")
                return calls, rewritten
    return None, content


__all__ = [
    "DEFAULT_EXTRACTOR_PIPELINE",
    "Extractor",
    "extract_tool_calls",
]
