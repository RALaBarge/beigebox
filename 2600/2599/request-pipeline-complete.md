# ✅ COMPLETE — Reference document. The 14-stage pipeline (parse → z-command → session cache → embedding classifier → decision LLM → route → pre-hooks → semantic cache → backend stream → WASM transform → cache store → metrics → Tap) is fully implemented as described.

# Complete Request Pipeline Diagram

Full end-to-end flow of a `/v1/chat/completions` request through BeigeBox.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      USER REQUEST ARRIVES                            │
│                 POST /v1/chat/completions (streaming)                │
└─────────────────────────────────────────────────────────────────────┘
                                ↓
        ┌─────────────────────────────────────────┐
        │ 1. PARSE REQUEST                        │
        │    - Extract: messages, model, temp, etc│
        │    - Check: conversation_id, user_id    │
        └─────────────────────────────────────────┘
                                ↓
        ┌─────────────────────────────────────────┐
        │ 2. Z-COMMAND PARSING (Tier 1)           │
        │    - Check first message for "z: "      │
        │    - Examples: z: simple, z: code, etc  │
        │    - If match: override routing         │
        └─────────────────────────────────────────┘
                                ↓
            Does it have z: override?
                    /           \
                  YES            NO
                  /               \
         Use override        Continue to Tier 2
                                ↓
        ┌─────────────────────────────────────────┐
        │ 3. SESSION CACHE LOOKUP (Tier 2)        │
        │    - Key: conversation_id               │
        │    - Value: (model_name, timestamp)     │
        │    - Check: expired? (>24h)             │
        └─────────────────────────────────────────┘
                                ↓
        Has conversation_id been seen in 24h?
                    /           \
                  YES            NO
                  /               \
          Use cached model    Continue to Tier 3
                                ↓
        ┌─────────────────────────────────────────┐
        │ 4. EMBEDDING CLASSIFIER (Tier 3)        │
        │    - Embed: user's last message         │
        │    - Compute: cosine sim to centroids   │
        │    - Output: predicted category         │
        │      (code, creative, simple, complex)  │
        └─────────────────────────────────────────┘
                                ↓
        High confidence match to centroid?
                    /           \
                  YES            NO
                  /               \
          Use category's     Continue to Tier 4
          route config
                                ↓
        ┌─────────────────────────────────────────┐
        │ 5. DECISION LLM (Tier 4)                │
        │    - Ambiguous cases only               │
        │    - Small fast LLM judges the request  │
        │    - Output: routing decision + reason  │
        │    - Logs: /api/v1/tap emit here        │
        └─────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 6. ROUTE SELECTION                       │
        │    - Lookup config.yaml routing rules    │
        │    - Example:                            │
        │      code: → ollama:latest-llama          │
        │      creative: → openrouter:claude       │
        │    - Select backend + model              │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 7. PRE-REQUEST HOOKS (optional)          │
        │    - Modify messages                     │
        │    - Inject system context               │
        │    - Inject runtime config overrides     │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 8. SEMANTIC CACHE LOOKUP                 │
        │    - Hash: request signature             │
        │    - Check: ChromaDB for exact match     │
        │    - If hit: return cached response      │
        │    - Tap emit: cache_hit event           │
        └──────────────────────────────────────────┘
                                ↓
        Found in semantic cache?
                    /           \
                  YES            NO
                  /               \
         Return cached      Continue to Tier 9
         (0 latency!)
                                ↓
        ┌──────────────────────────────────────────┐
        │ 9. STREAM TO BACKEND                     │
        │    - Forward request to selected model   │
        │    - Capture streaming response          │
        │    - Buffer if WASM active               │
        │    - Tap emit: backend_selection,        │
        │      request_sent, streaming_started     │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 10. STREAM TO CLIENT (real-time)         │
        │     - Re-emit chunks as they arrive      │
        │     - Client sees response token-by-token│
        │     - Tap emit: chunk_received           │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 11. POST-STREAM PROCESSING               │
        │     - WASM transform (if active)         │
        │     - Extract stop reason, token counts  │
        │     - Tap emit: response_complete        │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 12. SEMANTIC CACHE STORE                 │
        │     - Embed full conversation            │
        │     - Store: request + response in       │
        │       ChromaDB with TTL                  │
        │     - Tap emit: cache_stored             │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 13. UPDATE METRICS                       │
        │     - SQLiteStore: add row to requests   │
        │     - Track: latency, tokens, cost,      │
        │       model, backend, route              │
        │     - Tap emit: metrics_stored           │
        └──────────────────────────────────────────┘
                                ↓
        ┌──────────────────────────────────────────┐
        │ 14. LOGGING & OBSERVABILITY              │
        │     - Flush Tap events                   │
        │     - Egress hooks: webhook delivery     │
        │       (batched, retry w/ backoff)        │
        │     - Cleanup: close streams, release    │
        │       locks, deregister from registry    │
        └──────────────────────────────────────────┘
                                ↓
                        REQUEST DONE ✓
```

---

## Key Decision Points

| Step | Decision | Cacheable | Examples |
|------|----------|-----------|----------|
| Tier 1 | User override via z-command | No | `z: simple`, `z: code`, `z: creative` |
| Tier 2 | Same conversation within 24h | Yes (in-memory dict) | Reuse last model choice |
| Tier 3 | Cosine similarity to centroid | Partial (if threshold high) | Route code queries to Ollama |
| Tier 4 | Decision LLM judgment | No | Complex/ambiguous cases |

---

## Performance Notes

- **Cache hits** (Tier 2 or 8): 0-1ms overhead
- **Classifier only** (Tier 3): ~10ms (embedding + 4 dot products)
- **With decision LLM** (Tier 4): +200-500ms (small model inference)
- **WASM transform** (if active): +50-200ms (depends on output size)
- **Full pipeline no-cache**: ~500-2000ms to backend response + transform

---

## Tap Events Emitted

| Event | Tier | When |
|-------|------|------|
| `z_command_parsed` | 1 | Z-command detected |
| `session_cache_hit` / `miss` | 2 | Conversation lookup result |
| `classifier_routed` | 3 | Centroid match selected |
| `decision_llm_called` | 4 | Ambiguous case judged |
| `route_selected` | 6 | Backend + model chosen |
| `semantic_cache_hit` / `miss` | 8 | Cache lookup result |
| `streaming_started` | 9 | Backend connection open |
| `wasm_transform_applied` | 11 | WASM executed |
| `cache_stored` | 12 | Response indexed |
| `metrics_stored` | 13 | Row written to SQLiteStore |
| `egress_batch_sent` | 14 | Webhook batch delivered |
