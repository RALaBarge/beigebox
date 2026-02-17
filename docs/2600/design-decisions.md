# BeigeBox Design Decisions Log

*Running log of architectural decisions, alternatives considered, and reasoning. The README covers the "what" — this covers the "why we didn't do it the other way."*

---

## Embedding Model: nomic-embed-text via Ollama (not sentence-transformers)

**Decision**: Use the embedding model already loaded in Ollama rather than adding sentence-transformers as a direct Python dependency.

**Alternatives considered**:
- `all-MiniLM-L6-v2` via sentence-transformers (what NadirClaw uses): ~10ms in-process, 384 dimensions, ~80MB model. Would require adding `sentence-transformers` + `torch` to requirements — massive dependency footprint.
- `nomic-embed-text` via Ollama HTTP API: ~50ms round-trip, 768 dimensions, model already loaded for ChromaDB. Zero new dependencies.

**Why we chose Ollama**: The 40ms difference is negligible in the context of an LLM request that takes 2-30 seconds. Adding PyTorch as a transitive dependency would bloat the install significantly and create version conflicts. The nomic model is already pinned in GPU memory for ChromaDB embeddings — we're literally reusing hot memory.

## Z-Commands: User Override Philosophy

**Decision**: Give users absolute routing control via a simple prefix syntax that bypasses all automation.

**Rationale**: No classifier is perfect. Power users will develop intuition for when the router is wrong. Rather than forcing them to fight the system, give them an escape hatch. The `z:` prefix was chosen because:
- Short to type
- Unlikely to appear in natural language
- Evokes the phreaker theme (like "zero out" or "zone")
- Case-insensitive, whitespace-tolerant — hard to get wrong

## Decision LLM: Structured JSON Output (not free-text)

**Decision**: The decision LLM outputs constrained JSON with a fixed schema, not free-text reasoning.

**Rationale**: Free-text output requires parsing, is unpredictable in format, and wastes tokens. By constraining the output to `{"route": "...", "needs_search": bool, ...}`, we get deterministic parsing and can set `max_tokens` very low (256). The LLM's "reasoning" field is optional and purely for debug logging.

## Three-Tier Routing (not two-tier, not one-tier)

**Decision**: Z-commands → embedding classifier → decision LLM, with each tier being optional.

**Alternatives**:
- One-tier (decision LLM only): Simple but adds 500ms-2s to every request
- Two-tier (embedding + decision LLM): Good, but no user override
- Three-tier with graceful degradation: Each tier is independent. Z-commands work even if Ollama is down. Embedding classifier works even if the decision LLM model isn't pulled. Decision LLM works even if centroids haven't been built.

## Config-Driven Model References (no hardcoded model names)

**Decision**: All model names live in config.yaml under `decision_llm.routes`. Code references routes by name ("fast", "large", "code"), never by model string.

**Rationale**: Model names change constantly. New quantizations, new versions, new providers. If "the fast model" is hardcoded as a specific model throughout the codebase, every model swap requires a code change. With named routes, you edit one line in config.yaml.

---

*Add new decisions here as the project evolves.*
