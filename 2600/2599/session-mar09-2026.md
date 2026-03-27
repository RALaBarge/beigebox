# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# Session — 2026-03-09

## Topics covered

### WASM modules — compile, clean, fix

Three new WASM modules were found untracked in the repo with built Rust binaries still
sitting in `target/` subdirs:

- `json_extractor` — extracts first valid JSON from mixed prose+JSON LLM responses; handles
  raw JSON, ` ```json ` fences, and inline JSON embedded in prose
- `markdown_stripper` — strips Markdown formatting to plain text; intended for TTS pipelines
  where `**bold**` would be read aloud literally
- `pii_redactor` — regex-based redactor for emails, US phones, SSNs, and credit card numbers

`markdown_stripper` had a compile bug: the test string `r#"..."#` was terminated early by
`"#` inside the JSON content (`"content":"# Title`). Fixed by switching to `r##"..."##`.

All four custom modules (plus `passthrough`) compiled and copied to `wasm_modules/*.wasm`,
`target/` dirs deleted.

Config.yaml WASM paths updated from `target/wasm32-wasip1/release/` to root `.wasm` files.

---

### Config audit and fixes

Several config key mismatches between `config.yaml` and the code reading them were fixed:

| Section | yaml had | code reads | fix |
|---|---|---|---|
| `auto_summarization` | `keep_last_n` | `keep_last` | renamed |
| `auto_summarization` | `model` | `summary_model` | renamed |
| `auto_summarization` | (missing) | `summary_prefix` | added |

`auto_summarization` token budget updated: `500` → `24000`, `keep_last` `5` → `8`.
Rationale: 500 tokens is ~2 turns of conversation; 24k is appropriate for 32k–128k context
models (qwen3:4b, llama3.2, 7B–12B range). `keep_last: 8` keeps enough recent context for
coherent threading while still having room to summarise the bulk.

New config sections added that were read by code but absent from yaml:

- **`semantic_cache`** — `similarity_threshold`, `max_entries`, `ttl_seconds`, `enabled`
- **`operator.post_hook`** — `enabled`, `model`, `max_iterations`
- **`operator.timeout`** — seconds before operator run is abandoned (was hardcoded 300)

---

### Context token counter in status bar

Added a live `~Nk tok` counter to the header status bar that updates after each message
push to any pane's history. Colour-coded: neutral below 12k, yellow 12k–24k, red above 24k
(matching the `auto_summarization.token_budget`). Hides itself below 100 tokens.

Token estimation: chars / 4, consistent with the summarizer's heuristic. Not exact — accurate
enough for a visual signal.

---

### Vi-mode toggle moved inline

The floating `π` button (fixed position, bottom-center, always in the way) was removed.
Replaced with a tiny inline `π` pill in the header status bar. Dim by default, lavender when
active. Tooltip on hover shows "vi mode: on/off". Same `toggleViMode()` JS, just pointing at
`#vi-pill` instead of `#vi-toggle`.

All responsive/media-query overrides for `#vi-toggle` removed.

---

### Z-command help button

`GET /api/v1/zcommands` endpoint added to `main.py`. Returns structured z-command data:
- `routing` — alias groups mapped to route targets (e.g. `simple/easy/fast → fast`)
- `tools` — alias groups mapped to tool names
- `special` — `help`, `fork`
- `custom` — any commands from `config.yaml`'s `zcommands.commands` not already in the
  hardcoded dicts

A `z: ?` button added left of the chat textarea. On hover: CSS-only popover (`:hover` on
wrapper div shows `.z-help-tip`). Content populated once at page load via `loadZHelp()`.
Custom z-commands from config appear automatically. No ongoing JS for hover interaction.

Key observation: z-commands are currently hardcoded in `agents/zcommand.py` — the
`zcommands:` section in `config.yaml` was vestigial. The endpoint bridges this by reading
both sources and merging custom commands from config when present.

---

### Conversation on LLM efficiency frontiers

Discussion of what's actually being explored/bet on:

- **Test-time compute** — allocate more inference budget per query rather than training bigger
  models; o1/o3/R1 are this bet
- **MoE (Mixture of Experts)** — activate only a relevant subset of parameters per token;
  large model knowledge at small model inference cost; scaling laws still being worked out
- **Speculative decoding** — draft model proposes tokens, large model verifies in parallel;
  big latency wins with no quality loss
- **Memory/state** — context window is a hack; SSMs (Mamba etc.) haven't decisively won yet
- **Better data** — the quiet consensus is that synthetic data generation from models
  producing their own training signal is where the real next gains are

Key insight surfaced: **why agents work at inference time maps directly to why clean data
works at training time**. Both are about constraining the solution space and reducing noise:

- Clean training data → better internal representations, less noise per parameter
- Structured system prompt + specialized tools → constrain the solution space at inference,
  offload deterministic operations (math, facts, state) to verified external functions,
  activate the right "genre" of model behaviour rather than letting it thrash

Implication: the quality ceiling for agents is "how well can you decompose the problem into
steps the model is good at, and how good are your tools at the leaf nodes." The model is the
coordinator; the architecture is the quality floor.

---

### Auto-summarization strategy discussion

Current approach (one-shot collapse when budget exceeded) is the simplest and most common.
Rolling summarization was identified as the next meaningful upgrade — maintain a running
summary updated every N turns rather than a sudden cliff when the budget is hit.

The user asked about the Claude Code context compression threshold — not publicly documented
by Anthropic, so genuinely unknown. The observation was made that for a personal local setup,
hitting context limits in a single session is rare enough that the current approach plus
`z: memory` for explicit recall is the right tradeoff. Pre-hook auto-search for implicit
references was considered but rejected: it fires on every message, adds latency and noise, and
a small model will still miss subtle references. User-explicit `z: memory` has zero miss rate.

---

## Files changed

- `wasm_modules/markdown_stripper/src/main.rs` — fix `r#` → `r##` for raw string with `"#`
- `wasm_modules/*.wasm` — all five modules compiled and placed at root
- `config.yaml` — auto_summarization key fixes, token budget/keep_last, semantic_cache section,
  operator.post_hook + timeout, WASM paths
- `beigebox/main.py` — `GET /api/v1/zcommands` endpoint
- `beigebox/web/index.html` — ctx-usage counter, vi-pill, z-help button + CSS tooltip
- `README.md` — WASM modules list, project structure, API endpoint, Web UI section
