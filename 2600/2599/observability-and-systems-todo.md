# ⚠️ PARTIAL — Some items done: operator background execution, verbose harness logging, VRAM stats via /api/ps. Remaining: orchestration profiles, web search source weighting, ensemble defaults UI, harness window config fixes.

# Observability & Systems Backlog

Items from 2026-03-16 planning session. Quick items are being worked immediately; these are medium and larger scope.

---

## Medium — Logging / Observability

### Harness data → Tap (wiretap)
Currently only proxy `/v1/chat/completions` traffic hits `WireLog`. Harness runs (orchestrate, run, stream) bypass it entirely.
- Wire up `WireLog.log()` calls at harness start, each turn, tool call, and completion
- Include: run_id, model, iteration, elapsed_ms, token estimates, final status

### Verbose agentic logging
Operator and harness need much more signal in the logs:
- Every thought extracted from JSON response
- Every tool call: name, input, result preview, elapsed_ms
- Per-iteration: cumulative token estimate, confidence signals (if any)
- Loop detection events (when nudge fires)
- Final answer with total iterations + total elapsed

### Model hardware stats
Ollama exposes `/api/ps` with per-model GPU layer counts, VRAM usage, context size.
- Pull this on health check / model load
- Surface in UI: CPU/GPU split, estimated VRAM used, context window in use
- "Back of the envelope" RAM estimate is just: layers_on_gpu * (model_size / total_layers)

---

## Larger — New Systems

### Orchestration profiles
Named, saved orchestration configs stored in SQLite.
- Fields: name, model, tools (list), system_prompt, max_iterations, run_timeout, created_at
- API: CRUD at `/api/v1/orchestrations`
- UI: dropdown in operator/harness pane to load a saved profile
- Usable by harness, operator, council — shared config layer
- Related to AppState refactor (arch branch) — good place to hang this

### Source reputation weighting (web search)
Domain-level scoring applied to web_search and web_scraper results before returning to operator.
- Allowlist with scores: wikipedia.org=1.0, arxiv.org=0.95, github.com=0.85, etc.
- Unknown domains default to 0.5, known-bad domains to 0.1
- Score injected into result text so operator can factor it in
- Config-driven: `tools.web_search.source_weights` in config.yaml

### Ensemble default model set + UI
Currently ensemble requires caller to pass `models` list explicitly — no defaults.
- Add `tools.ensemble.default_models` list to config
- EnsembleTool falls back to this list when no `models` key in input
- Expose in UI: multi-select in operator/ensemble pane showing available models
- Judge model also configurable per-invocation

---

## Notes
- Standardizing chat view components (chat, op, ensemble, harness) is a prerequisite for
  "fix token counts in harness" — tracked separately as a UI refactor task
- Verbose logging work should happen before chat window standardization so there's
  actual data to display
