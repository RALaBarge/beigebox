"""
Tests for the panel-convergent refinements applied to request_normalizer
and response_normalizer:

  1. deepcopy at request_normalizer pipeline entry — caller's body is never
     mutated.
  2. Rule application order documented on normalize_request.__doc__.
  3. finalize_stream() helper assembles a NormalizedResponse from a stream
     of NormalizedDeltas.
  4. sanitize_unicode flag scrubs control chars + lone surrogates from
     content / reasoning on both normalize_response and finalize_stream.
  5. register_profile thread-safety: parallel registrations don't drop
     entries or raise.
"""

from __future__ import annotations

import copy
import threading

import pytest

from beigebox import request_normalizer as rq
from beigebox import response_normalizer as rs


# ─────────────────────────────────────────────────────────────────────────────
# 1. deepcopy at pipeline entry
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_request_does_not_mutate_caller_body():
    body = {
        "model": "openai/o4-mini",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi", "reasoning_content": "stale prior turn"},
        ],
        "max_tokens": 100,
        "temperature": 0.7,
    }
    snapshot = copy.deepcopy(body)
    nr = rq.normalize_request(body, target="openai_reasoning")

    # Caller's dict — top level and every nested message — unchanged
    assert body == snapshot
    # And the result is a different object
    assert nr.body is not body
    # Confirm o-series transformations actually fired on the copy
    assert "max_tokens" not in nr.body
    assert "max_completion_tokens" in nr.body
    assert "temperature" not in nr.body


def test_normalize_request_deepcopy_isolates_nested_lists():
    """Mutating the result's nested lists must not bleed into the caller."""
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    snapshot = copy.deepcopy(body)
    nr = rq.normalize_request(body, target="openai_compat")

    # Mutate the result aggressively
    nr.body["messages"].append({"role": "user", "content": "injected"})
    nr.body["messages"][0]["content"] = "overwritten"

    assert body == snapshot


# ─────────────────────────────────────────────────────────────────────────────
# 2. Rule application order documented
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_request_docstring_documents_pipeline_order():
    doc = rq.normalize_request.__doc__ or ""
    # The docstring should call out each phase
    for needle in ("deepcopy", "messages_coerce", "strip_message_fields",
                   "collapse_system", "canonicalize_tools", "extra_rules"):
        assert needle in doc, f"docstring missing pipeline phase: {needle}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. finalize_stream() — basic assembly
# ─────────────────────────────────────────────────────────────────────────────


def _delta(content="", reasoning="", tool_calls=None, finish=None,
           raw_extra=None, is_final=False) -> rs.NormalizedDelta:
    raw: dict = {"choices": [{"delta": {}}]}
    if raw_extra:
        raw.update(raw_extra)
    return rs.NormalizedDelta(
        content_delta=content,
        reasoning_delta=reasoning,
        tool_calls_delta=tool_calls,
        finish_reason=finish,
        is_final=is_final,
        raw=raw,
        errors=[],
    )


def test_finalize_stream_concatenates_content_and_reasoning():
    deltas = [
        _delta(content="Hel", reasoning="thinking "),
        _delta(content="lo, ", reasoning="harder..."),
        _delta(content="world!", finish="stop", is_final=True,
               raw_extra={"usage": {"prompt_tokens": 4, "completion_tokens": 6,
                                    "total_tokens": 10}}),
    ]
    n = rs.finalize_stream(deltas)
    assert n.content == "Hello, world!"
    assert n.reasoning == "thinking harder..."
    assert n.finish_reason == "stop"
    assert n.usage.prompt_tokens == 4
    assert n.usage.completion_tokens == 6
    assert n.usage.total_tokens == 10
    assert n.tool_calls is None
    assert n.errors == []


def test_finalize_stream_handles_empty_iterable():
    n = rs.finalize_stream([])
    assert n.content == ""
    assert n.reasoning is None
    assert n.tool_calls is None
    assert "no_deltas" in n.errors


def test_finalize_stream_merges_tool_call_chunks_by_index():
    """OpenAI streams tool_calls as fragments; finalize_stream reassembles them."""
    deltas = [
        _delta(tool_calls=[{"index": 0, "id": "call_xyz", "type": "function",
                            "function": {"name": "search", "arguments": ""}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"q":"so'}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": 'lar"}'}}]),
        _delta(finish="tool_calls", is_final=True),
    ]
    n = rs.finalize_stream(deltas)
    assert n.finish_reason == "tool_calls"
    assert n.tool_calls and len(n.tool_calls) == 1
    tc = n.tool_calls[0]
    assert tc["id"] == "call_xyz"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "search"
    assert tc["function"]["arguments"] == '{"q":"solar"}'


def test_finalize_stream_picks_last_finish_reason():
    """If multiple deltas carry a finish_reason, the latest wins."""
    deltas = [
        _delta(content="a", finish="stop"),  # provider quirk: early termination
        _delta(content="b", finish="length", is_final=True),
    ]
    n = rs.finalize_stream(deltas)
    assert n.finish_reason == "length"
    assert n.content == "ab"


# ─────────────────────────────────────────────────────────────────────────────
# 4. sanitize_unicode flag
# ─────────────────────────────────────────────────────────────────────────────


def _wrap(content: str, reasoning: str | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    return {
        "choices": [{"message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_sanitize_off_by_default_preserves_garbage():
    """Don't lie about upstream output — by default, surface what came down."""
    bad = "hello\x07world"  # bell character
    n = rs.normalize_response(_wrap(bad))
    assert n.content == bad


def test_sanitize_strips_control_chars():
    bad = "ok\x00\x01\x07line\x1f\x7fend"
    n = rs.normalize_response(_wrap(bad), sanitize_unicode=True)
    assert "\x00" not in n.content
    assert "\x07" not in n.content
    assert "\x7f" not in n.content
    assert n.content.startswith("ok")
    assert n.content.endswith("end")


def test_sanitize_preserves_whitespace_and_normal_text():
    """Tab, newline, CR must survive. Non-control text untouched."""
    s = "first\tcol\nsecond\r\nthird"
    n = rs.normalize_response(_wrap(s), sanitize_unicode=True)
    assert n.content == s


def test_sanitize_strips_lone_surrogates():
    bad = "ok \ud800 broken \ud83d end"  # lone surrogate + half a pair
    n = rs.normalize_response(_wrap(bad), sanitize_unicode=True)
    assert "\ud800" not in n.content
    assert "\ud83d" not in n.content
    # Replaced with U+FFFD (the encode/decode replace step)
    assert "ok " in n.content and " end" in n.content


def test_sanitize_applies_to_reasoning_too():
    bad = "thought\x00bubble"
    n = rs.normalize_response(_wrap("ok", reasoning=bad), sanitize_unicode=True)
    assert n.reasoning is not None
    assert "\x00" not in n.reasoning


def test_sanitize_idempotent():
    bad = "junk\x00here"
    once = rs._sanitize_text(bad)
    twice = rs._sanitize_text(once)
    assert once == twice


def test_finalize_stream_sanitize_flag():
    deltas = [_delta(content="ok\x00", reasoning="r\x07s", finish="stop", is_final=True)]
    n = rs.finalize_stream(deltas, sanitize_unicode=True)
    assert "\x00" not in n.content
    assert n.reasoning is not None
    assert "\x07" not in n.reasoning


# ─────────────────────────────────────────────────────────────────────────────
# 5. register_profile thread-safety
# ─────────────────────────────────────────────────────────────────────────────


def test_register_profile_concurrent_registrations(monkeypatch):
    """Race 64 threads each registering a unique profile; none should be lost."""
    test_registry: dict[str, rq.TargetProfile] = {}
    n = 64
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            profile = rq.TargetProfile(name=f"race_{i}", rules=[])
            rq.register_profile(profile, registry=test_registry)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"register_profile raised under contention: {errors}"
    assert len(test_registry) == n
    for i in range(n):
        assert f"race_{i}" in test_registry


def test_register_profile_default_registry_thread_safe():
    """Same as above but exercises DEFAULT_PROFILES, then cleans up."""
    n = 16
    barrier = threading.Barrier(n)
    names = [f"_thread_safe_test_{i}" for i in range(n)]

    def worker(i: int) -> None:
        barrier.wait(timeout=5.0)
        rq.register_profile(rq.TargetProfile(name=names[i], rules=[]))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    try:
        for name in names:
            assert name in rq.DEFAULT_PROFILES
    finally:
        # cleanup so other tests don't see our pollution
        with rq._REGISTRY_LOCK:
            for name in names:
                rq.DEFAULT_PROFILES.pop(name, None)


def test_capability_layering_openrouter_plus_o_series():
    """target=openrouter + model=o-series must apply BOTH the openrouter
    profile (stream_options injection) AND the o-series capability rules
    (drop temperature, rename max_tokens). Pre-fix, only the openrouter
    profile fired and the o-series caps were lost.
    """
    body = {
        "model": "openai/o4-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
        "temperature": 0.7,
        "top_p": 0.9,
        "stream": True,
    }
    nr = rq.normalize_request(body, target="openrouter")
    # openrouter profile fired (stream_options injection)
    assert nr.body.get("stream_options") == {"include_usage": True}
    # o-series capability layer composed on top
    assert "max_tokens" not in nr.body
    assert nr.body.get("max_completion_tokens") == 50
    assert "temperature" not in nr.body
    assert "top_p" not in nr.body
    # Transform log records the composition
    assert any(t.startswith("capability_layer:openai_reasoning_caps")
               for t in nr.transforms)


def test_capability_layering_no_op_for_non_reasoning_model():
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
        "temperature": 0.7,
    }
    nr = rq.normalize_request(body, target="openrouter")
    assert nr.body.get("max_tokens") == 50  # untouched
    assert nr.body.get("temperature") == 0.7
    assert not any(t.startswith("capability_layer:") for t in nr.transforms)


def test_capability_layering_skipped_when_profile_already_has_caps():
    """target=openai_reasoning shouldn't double-apply the o-series rules
    via capability layering — it already has them."""
    body = {
        "model": "openai/o4-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
    }
    nr = rq.normalize_request(body, target="openai_reasoning")
    assert nr.body.get("max_completion_tokens") == 50
    # No capability_layer transform tag (the SKIP_FOR_PROFILE check)
    assert not any(t.startswith("capability_layer:") for t in nr.transforms)


# ─────────────────────────────────────────────────────────────────────────────
# finalize_stream + tool-call extraction integration
# ─────────────────────────────────────────────────────────────────────────────


def test_finalize_stream_lifts_tool_call_from_assembled_content():
    """If a streamed response carries the tool call as text deltas (no
    structured tool_calls_delta), the finalizer should run the extractor
    on the assembled content — same on-by-default behaviour as
    normalize_response."""
    deltas = [
        _delta(content='{"name": "get_weather"'),
        _delta(content=', "arguments": {"city": "SF"}}'),
        _delta(finish="stop", is_final=True),
    ]
    n = rs.finalize_stream(deltas)
    assert n.tool_calls is not None
    assert n.tool_calls[0]["function"]["name"] == "get_weather"
    assert n.content == ""  # extractor stripped the JSON
    assert any(e.startswith("content_rewritten:") for e in n.errors)


def test_finalize_stream_extraction_can_be_disabled():
    deltas = [
        _delta(content='{"name": "get_weather", "arguments": {"city": "SF"}}',
               finish="stop", is_final=True),
    ]
    n = rs.finalize_stream(deltas, enable_tool_call_extraction=False)
    assert n.tool_calls is None
    assert n.content.startswith("{")


def test_finalize_stream_skips_extraction_when_structured_calls_present():
    deltas = [
        _delta(tool_calls=[{"index": 0, "id": "call_x", "type": "function",
                            "function": {"name": "real", "arguments": "{}"}}]),
        _delta(finish="tool_calls", is_final=True),
    ]
    n = rs.finalize_stream(deltas)
    assert n.tool_calls is not None
    assert n.tool_calls[0]["function"]["name"] == "real"


# ─────────────────────────────────────────────────────────────────────────────
# Tool-call extractor ReDoS guard
# ─────────────────────────────────────────────────────────────────────────────


def test_extractor_redos_guard_skips_oversized_content():
    from beigebox.tool_call_extractors import (
        extract_tool_calls,
        MAX_EXTRACTION_CHARS,
    )
    # Build content larger than the cap. Use a pathological pattern that
    # would trip backtracking on the .*? extractors if they ran.
    big = "<function_calls>" + ("a" * (MAX_EXTRACTION_CHARS + 100))
    errors: list[str] = []
    calls, rewritten = extract_tool_calls(big, errors=errors)
    assert calls is None
    assert rewritten == big  # untouched
    assert any(e.startswith("extraction_skipped:content_too_large:") for e in errors)


def test_extractor_under_size_cap_runs_normally():
    from beigebox.tool_call_extractors import extract_tool_calls
    content = '{"name": "ping", "arguments": {"host": "1.1.1.1"}}'
    assert len(content) < 1024
    calls, _ = extract_tool_calls(content)
    assert calls is not None  # normal pipeline still works


# ─────────────────────────────────────────────────────────────────────────────
# RAGPoisoningDetector.import_baseline count clamping
# ─────────────────────────────────────────────────────────────────────────────


def test_import_baseline_clamps_count_to_norms_length():
    """Hand-built state with count<len(norms) should still leave the
    detector internally consistent."""
    from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
    d = RAGPoisoningDetector()
    d.import_baseline({
        "norms": [1.0, 2.0, 3.0, 4.0, 5.0],
        "mean_norm": 3.0,
        "std_norm": 1.5,
        "count": 0,  # lying about count
    })
    stats = d.get_baseline_stats()
    assert stats["count"] >= 5  # clamped up to len(norms)
    assert stats["baseline_window_size"] == 5


def test_import_baseline_preserves_high_count():
    """If count > len(norms), trust the caller — the deque is a rolling
    window and count tracks total updates ever (can exceed window size)."""
    from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector
    d = RAGPoisoningDetector()
    d.import_baseline({
        "norms": [1.0, 2.0, 3.0],
        "mean_norm": 2.0,
        "std_norm": 0.8,
        "count": 9999,
    })
    stats = d.get_baseline_stats()
    assert stats["count"] == 9999


# ─────────────────────────────────────────────────────────────────────────────
# Memory backend quarantine race
# ─────────────────────────────────────────────────────────────────────────────


def test_memory_backend_quarantine_count_concurrent_safe():
    """20 threads × 5 quarantined upserts each. Final count must be exactly
    100; previously the increment was outside the lock and counts were lost."""
    from beigebox.storage.backends.memory import MemoryBackend
    from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

    # Strict-mode poisoning detector that flags everything
    class AlwaysPoison(RAGPoisoningDetector):
        def is_poisoned(self, embedding):
            return (True, 0.99, "test forced")

    backend = MemoryBackend(rag_detector=AlwaysPoison(), detection_mode="quarantine")
    n_threads = 20
    per_thread = 5
    barrier = threading.Barrier(n_threads)

    def worker(i):
        barrier.wait(timeout=5.0)
        backend.upsert(
            ids=[f"t{i}_{j}" for j in range(per_thread)],
            embeddings=[[0.1, 0.2, 0.3]] * per_thread,
            documents=["x"] * per_thread,
            metadatas=[{}] * per_thread,
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10.0)

    stats = backend.get_detector_stats()
    assert stats["quarantine_count"] == n_threads * per_thread


# ─────────────────────────────────────────────────────────────────────────────
# Cheap-deepcopy: caller's body shape preserved, vision payload not duplicated
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_request_does_not_deep_copy_top_level_unrelated_keys():
    """Vision payloads or other large opaque fields at the top level should
    not be deep-copied — the cheap-deepcopy compromise per DeepSeek's review."""
    big_blob = "x" * 500_000  # 500 KB
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "_my_huge_metadata_field": big_blob,
    }
    nr = rq.normalize_request(body, target="openai_compat")
    # Top-level opaque values are shared (not copied) — same id
    assert nr.body["_my_huge_metadata_field"] is big_blob
    # But messages were deep-copied — the rules can mutate without bleeding
    assert nr.body["messages"] is not body["messages"]


def test_normalize_request_snapshot_unaffected_by_concurrent_registration():
    """A normalize_request in flight must use a stable snapshot of the registry."""
    body = {
        "model": "openai/o4-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 50,
    }

    # Just verify that calling normalize_request alongside a flurry of
    # registrations doesn't raise or produce torn state.
    stop = threading.Event()
    errors: list[Exception] = []

    def churn():
        try:
            i = 0
            while not stop.is_set():
                rq.register_profile(rq.TargetProfile(
                    name=f"_churn_{i}", rules=[]
                ))
                i += 1
                if i > 200:  # cap to avoid runaway
                    break
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=churn, daemon=True)
    t.start()
    try:
        for _ in range(50):
            nr = rq.normalize_request(body, target="openai_reasoning")
            assert "max_completion_tokens" in nr.body
    finally:
        stop.set()
        t.join(timeout=2.0)
        # cleanup
        with rq._REGISTRY_LOCK:
            for k in list(rq.DEFAULT_PROFILES.keys()):
                if k.startswith("_churn_"):
                    rq.DEFAULT_PROFILES.pop(k, None)

    assert not errors, errors
