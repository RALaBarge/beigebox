# BeigeBox — Todo / Backlog

---

## Active / Next

- [ ] **Config tab hover tooltips** — hovering over any config field label should show a brief
      description of what that setting does. Every exposed setting in the Config tab gets a
      tooltip. Implementation: add a `data-tip` attribute to each label element, write a single
      delegated `mouseover` handler that renders a small floating box. Content lives in a JS
      map keyed by config field ID so it's easy to extend.

---

## Known Issues

- **Busybox wrapper broken call syntax** — `system_info.py` calls `subprocess.run([bb, "-c", cmd])`
  but `bb` expects `bb <applet> [args]`, not `-c`. Result: every system_info command silently
  returns empty string in Docker. The Python allowlist/audit logging still runs but busybox
  provides zero actual filtering. Quick fix: detect when shell is `bb` and build the argv as
  `[bb, applet, ...args]` instead of `[bb, "-c", full_cmd_string]`. Until fixed the only real
  security gate is the Python-level allowlist in `_is_command_allowed()`.

- **Operator shell sandbox (VM/container isolation)** — for truly safe arbitrary shell execution,
  the right answer is spawning a throwaway container per shell call and destroying it afterward.
  Current approach (Python allowlist + non-root + bb wrapper) is adequate for the current use
  case (read-only stats queries), but argument injection is still possible: e.g. `grep` is
  allowed but `grep -r "" /app/data` would exfiltrate the conversation DB. Two options:
  (1) tighten the allowlist to read-only, argument-safe commands only and accept the limitation;
  (2) proper per-call container sandbox via `docker run --rm` with a stripped image, capped
  CPU/RAM, no network, read-only mounts. Option 2 requires Docker socket access in the container
  which has its own tradeoffs.

- **JACK TUI** — `ValueError: No Tab with id '--content-tab-config'` at startup. The TUI
  code was removed from the codebase but is still referenced in the Docker image. Either
  restore the Textual TUI (re-adding `textual` dep, rewriting tabs to match 7-tab layout)
  or formally drop the `jack` command and remove it from any docs/entrypoints.

- **ChromaDB thread safety** — `PersistentClient` is not thread-safe by default. Concurrent
  embedding writes under high load could cause lock errors. Mitigation: wrap collection
  ops in an asyncio lock, or switch to the HTTP client mode.

- **Session cache unbounded growth** — `_session_routes` in proxy.py grows forever.
  Low priority for personal use, relevant for long-running shared instances. Add a simple
  LRU cap (e.g. 1000 entries) or TTL sweep on a background task.

- **Embedding error handling** — `vector_store.py` does not gracefully handle: model not
  pulled yet, empty embeddings array, non-JSON response from Ollama. Should catch and log
  rather than propagate into the request pipeline.

---

## Nice To Have

- **Embex vector backend abstraction** — replace direct ChromaDB usage with a thin
  `VectorBackend` ABC (3 methods: `upsert`, `query`, `count`). Wrap the current ChromaDB
  code as `ChromaBackend`. Wire a config key `vector_backend: chromadb` (default, zero
  behaviour change). Adding LanceDB, Qdrant, Pinecone etc. later becomes one new class.
  Estimated effort: ~3 hours, low risk. The embedding calls are already separate from
  the store calls so the surface to abstract is small.

- **Plugin system for tools** — drop Python files into a `plugins/` directory, auto-discover
  and register them into the tool registry at startup. No code changes needed to add a tool.

- **Conversation forking via z-command** — `z: fork` creates a branch from the current
  message into a new conversation ID. Complements the existing replay fork in the web UI.

- **Model performance dashboard charts** — tokens/sec by model, routing accuracy over time,
  cache hit rate for embeddings. Data already collected in SQLite; needs a chart rendering
  pass in the Dashboard tab.

- **TUI (JACK) restore** — rebuild the Textual TUI to match the current 7-tab layout.
  Requires `textual>=0.70` dep, tab IDs reconciled with current structure. Low priority
  given the web UI covers the same ground.

---

## Closed / Resolved (reference)

- ~~Port mismatch in docs~~ — fixed, internal port is 8000, host-mapped to 1337.
- ~~No health check endpoint~~ — `/beigebox/health` added.
- ~~Flight recorder / semantic map~~ — both killed in v0.10.0; timing merged into wiretap.
- ~~Ensemble voting buried on Chat tab~~ — moved to Harness tab as a third mode (v0.10.1).
- ~~`cli.py` version stale at 0.9.0~~ — bumped to 0.10.0.
- ~~Config tab label says tab (8)~~ — corrected to tab (7).
