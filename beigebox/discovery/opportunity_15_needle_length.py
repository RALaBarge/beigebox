"""
Opportunity #15: Gold Context Size Optimization (Needle Length)

Hypothesis: Smaller, denser fact chunks (200 chars) are easier for the model
to find and retrieve than large chunks (1000 chars). Adaptive chunk sizing
per content type improves recall.

Expected impact: +10-20% fact recall (HIGH confidence)

Research: PMC 2026 — smaller needles more difficult (counter-intuitive);
VLDB 2026 — precision wins over recall for RAG. Sweet spot: 150-300 chars.

Variants
--------
- baseline_long:     Long context blocks (~400 chars each, current RAG default)
- short_dense:       Short dense facts (~150 chars)
- medium_chunks:     Medium chunks (~250 chars)
- adaptive_typed:    Code=short (syntax-dense), prose=medium, lists=long
"""
from __future__ import annotations

from typing import Any

from beigebox.discovery.base import DiscoveryOpportunity, DiscoveryTestCase

# One long block of content that will be chunked differently per variant
_LONG_FACT_1 = (
    "The BeigeBox proxy pipeline processes every /v1/chat/completions request "
    "through 14 stages: parse, z-command (T1), session cache (T2), embedding "
    "classifier (T3), decision LLM (T4), route, pre-hooks, semantic cache lookup, "
    "backend stream, WASM transform, cache store, metrics, Tap. Each stage has "
    "a clear decision point and can short-circuit the pipeline."
)

_LONG_FACT_2 = (
    "The multi-backend router maintains a rolling P95 latency window of 100 samples "
    "per backend. Backends exceeding latency_p95_threshold_ms are deprioritized to "
    "second-pass fallback. A/B traffic splitting uses weighted random selection via "
    "_select_ab(). The router falls back through priority-ordered backends on error. "
    "OpenRouter requires fully-qualified model IDs (provider/model format)."
)

_LONG_FACT_3 = (
    "The semantic cache uses ChromaDB for vector storage. Embeddings are generated "
    "via nomic-embed-text. Cache lookup uses cosine similarity with a configurable "
    "threshold (default 0.92). Cache hits return immediately without hitting the "
    "backend. Cache stores happen post-stream. TTL is configurable per entry. "
    "The cache hit rate is currently 34% with a miss penalty of ~45ms."
)

# Short dense versions of the same content
_SHORT_FACTS = [
    "Pipeline: 14 stages — parse→z-cmd→session-cache→embed-classify→decision-LLM→route→pre-hooks→sem-cache→stream→WASM→cache-store→metrics→Tap.",
    "Router: rolling P95 per backend; slow backends deprioritized; A/B via weighted random; falls back on error; OR needs provider/model IDs.",
    "Semantic cache: ChromaDB + nomic-embed-text; cosine sim threshold 0.92; hit rate 34%; miss penalty 45ms; post-stream store; configurable TTL.",
]


def _split_long(text: str, max_len: int) -> list[str]:
    """Split text into chunks of at most max_len chars, breaking on spaces."""
    words = text.split()
    chunks, current = [], []
    for word in words:
        current.append(word)
        if sum(len(w) + 1 for w in current) >= max_len:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def _get_body_turns(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    system = [m for m in messages if m["role"] == "system"]
    body = [m for m in messages if m["role"] != "system"]
    question = body[-1] if body else {"role": "user", "content": ""}
    context = body[:-1]
    return system, context, question


def _turns_from_facts(facts: list[str]) -> list[dict]:
    turns = []
    for fact in facts:
        turns.append({"role": "user", "content": f"Remember this: {fact}"})
        turns.append({"role": "assistant", "content": f"Noted: {fact[:60]}..."})
    return turns


class NeedleLengthExperiment(DiscoveryOpportunity):
    OPPORTUNITY_ID = "needle_length"
    OPPORTUNITY_NAME = "Gold Context Size / Needle Length (#15)"
    HYPOTHESIS = "Shorter, denser fact chunks improve recall vs. long paragraphs"
    EXPECTED_IMPACT = "+10-20% fact recall"
    WEIGHT_PROFILE = "general"

    VARIANTS = [
        {"name": "baseline_long",    "chunk_strategy": "long"},
        {"name": "short_dense",      "chunk_strategy": "short",  "max_chars": 150},
        {"name": "medium_chunks",    "chunk_strategy": "medium", "max_chars": 250},
        {"name": "adaptive_typed",   "chunk_strategy": "adaptive"},
    ]

    def transform(
        self,
        messages: list[dict[str, Any]],
        variant_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        strategy = variant_config.get("chunk_strategy", "long")
        system, context, question = _get_body_turns(messages)

        if strategy == "long":
            return messages

        # Collect all factual content from context
        raw_content = []
        for msg in context:
            content = msg.get("content", "")
            # Strip "Remember this:" prefix injected by _build_messages
            stripped = content.replace("Remember this: ", "").replace("Noted: ", "")
            if stripped and len(stripped) > 20:
                raw_content.append(stripped)

        if not raw_content:
            return messages

        all_text = " ".join(raw_content)

        if strategy == "short":
            max_chars = variant_config.get("max_chars", 150)
            chunks = _split_long(all_text, max_chars)
        elif strategy == "medium":
            max_chars = variant_config.get("max_chars", 250)
            chunks = _split_long(all_text, max_chars)
        elif strategy == "adaptive":
            # Code-like content (contains symbols): short; prose: medium
            if any(c in all_text for c in "→:=()[]"):
                chunks = _split_long(all_text, 150)
            else:
                chunks = _split_long(all_text, 280)
        else:
            return messages

        new_context = _turns_from_facts(chunks)
        return system + new_context + [question]

    def test_cases(self) -> list[DiscoveryTestCase]:
        long_facts = [_LONG_FACT_1, _LONG_FACT_2, _LONG_FACT_3]
        return [
            DiscoveryTestCase(
                context_facts=long_facts,
                question="How many stages are in the request pipeline?",
                expected="14",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What is the semantic cache similarity threshold?",
                expected="0.92",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What format do OpenRouter model IDs require?",
                expected="provider/model",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What is the cache hit rate?",
                expected="34%",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What is the rolling window size for P95 tracking?",
                expected="100",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What embedding model is used for the semantic cache?",
                expected="nomic-embed-text",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="What is the cache miss penalty?",
                expected="45ms",
                task_type="needle_in_long",
            ),
            DiscoveryTestCase(
                context_facts=long_facts,
                question="Describe the backend fallback strategy.",
                expected="priority",
                task_type="synthesis",
            ),
        ]
