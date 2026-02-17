# BeigeBox Routing Theory — Design Notes & Research

*Working notes from the development of BeigeBox's hybrid routing system. This is the brainstorming, theorycrafting, and research that informed the implementation. Kept here for future reference so we don't re-derive the same conclusions.*

---

## The Routing Problem

When a user sends a message to a local LLM stack, the middleware needs to decide:
1. Which model should handle this? (small/fast vs large/slow vs specialized)
2. Does this need tool augmentation? (web search, RAG, calculator)
3. How confident are we in that decision?

The naive approach is to run every request through a "decision LLM" — a small model that reads the prompt and outputs a routing decision. This works but adds 500ms–2s of latency to every single request, even "hello, how are you?"

## NadirClaw Analysis

[NadirClaw](https://github.com/doramirdor/NadirClaw) solved this elegantly with an embedding-based binary classifier:

**How it works:**
- Pre-compute ~170 seed prompts (simple and complex categories)
- Embed all seeds using `all-MiniLM-L6-v2` (384 dimensions, ~80MB model)
- Average each category's embeddings into a single centroid vector (~1.5KB each)
- At runtime: embed the user prompt, compute cosine similarity to both centroids, classify based on which is closer

**Routing modifiers layered on top:**
- Agentic detection: regex scoring for tool-calling patterns
- Reasoning detection: phrase matching for step-by-step / analytical requests
- Context window checks: route to models that can handle the input length
- Session persistence: 30-minute cache so the same conversation stays on the same model

**Performance:**
- Classification: ~10ms (sentence-transformers runs in-process, no HTTP overhead)
- Binary output: simple or complex
- Handles ~80% of requests without needing a heavier classifier

**Limitations:**
- Binary only (simple vs complex) — no N-way routing, no tool detection
- Requires sentence-transformers as a dependency (~80MB model download)
- Centroid quality depends heavily on seed prompt selection
- No borderline handling — everything gets classified, even ambiguous prompts

## Why We Can't Reuse NadirClaw's Centroids

NadirClaw uses `all-MiniLM-L6-v2` (384-dimensional vectors). BeigeBox uses `nomic-embed-text` via Ollama (768-dimensional vectors). These are fundamentally incompatible:

- **Different dimensionality**: 384 vs 768 — the vectors aren't even the same shape
- **Different embedding spaces**: Each model learns its own semantic geometry during training. "Design a microservices architecture" occupies a completely different region of 384-space in MiniLM than it does in 768-space in nomic-embed-text
- **Cosine similarity is space-dependent**: Similarity scores only mean something when both vectors came from the same embedder

This means we must generate our own centroids from our own seed prompts using our own embedding model. The `beigebox build-centroids` command handles this — it embeds all seed prototypes through Ollama's nomic-embed-text and computes fresh centroids.

The *approach* is portable across embedders. The *weights* are not.

## BeigeBox Hybrid Routing Design

We took NadirClaw's core insight (embedding classification is fast and handles most cases) and extended it into a three-tier system:

### Tier 1: Z-Commands (0ms)
User-level overrides via `z:` prefix. Absolute priority, bypasses everything. This exists because sometimes the human knows better than any classifier. The prefix is stripped before the LLM sees the message.

### Tier 2: Embedding Classifier (~50ms)
NadirClaw-inspired binary classification using nomic-embed-text (already loaded for ChromaDB — zero new dependencies). Handles clear-cut simple/complex cases. The ~50ms latency (vs NadirClaw's ~10ms) comes from the HTTP round-trip to Ollama, but we avoid adding sentence-transformers as a dependency.

### Tier 3: Decision LLM (~500ms–2s)
Full N-way routing with tool detection. Only triggered when the embedding classifier flags a borderline case (confidence below threshold). This is the expensive path — a small model reads the prompt and outputs structured JSON with route, tool needs, and reasoning.

### The Borderline Threshold

The embedding classifier reports a confidence score: the absolute difference between cosine similarity to the simple centroid vs the complex centroid. When this gap is small (< 0.04 by default), the prompt is genuinely ambiguous — it could go either way. These borderline cases get escalated to the decision LLM for a more nuanced judgment.

The threshold is configurable. Lower = more requests hit the fast path (but with lower accuracy on edge cases). Higher = more requests escalate to the decision LLM (slower but more accurate).

## Seed Prompt Selection

The quality of the embedding classifier depends entirely on the seed prompts used to build centroids. Our current set:

- **41 simple prototypes**: factual questions, one-line tasks, quick lookups, basic operations, greetings, simple math
- **30 complex prototypes**: architecture design, multi-step debugging, algorithm implementation, security audits, system design, long-form analysis

Design principles for seeds:
- Seeds should represent the *center* of each category, not the edges
- Avoid seeds that could reasonably go either way (those are for the decision LLM)
- Include domain diversity: code, general knowledge, system admin, creative
- Quantity matters less than coverage — 30-40 well-chosen seeds beat 200 redundant ones

## Future Directions

**Multi-class centroids**: Instead of binary simple/complex, build centroids for each route (code, general, creative, research). This would give the embedding classifier N-way routing without needing the decision LLM for most cases.

**Adaptive centroids**: Track which classifications the decision LLM overrides and use those as training signal to improve the seed set over time.

**Agentic detection layer**: NadirClaw's regex-based agentic scoring could be added as a cheap pre-filter before the embedding classifier. Pattern matching for tool-calling syntax costs essentially nothing.

**Session persistence**: Route stickiness within a conversation (if the first message routes to the code model, subsequent messages in the same conversation should probably stay there unless the topic clearly shifts).

---

*These notes reflect the state of the system as of v0.3.0. Update as the routing system evolves.*
