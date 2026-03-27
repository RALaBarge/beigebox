# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# BeigeBox — Todo / Backlog

---

## Active / Next

_Nothing queued — see backlog below._

## Recently shipped

- **doc_parser plugin** (`plugins/doc_parser.py`) — MarkItDown-based document parser.
  Supports PDF, DOCX, PPTX, XLSX, HTML, MD, TXT, CSV, JSON, XML, images, ZIP.
  OCR: Tesseract (auto-detected) or vision LLM via `ocr_model` config key.
  Chunks ingested into ChromaDB for z: memory recall.
  z-aliases: `parse`, `doc`, `ingest`. Config under `tools.plugins.doc_parser`.

---

## Backlog

_Empty — all backlog items resolved._

---

## Closed / Resolved (reference)

- ~~Port mismatch in docs~~ — fixed, internal port is 8000, host-mapped to 1337.
- ~~No health check endpoint~~ — `/beigebox/health` added.
- ~~Flight recorder / semantic map~~ — both killed in v0.10.0; timing merged into wiretap.
- ~~Ensemble voting buried on Chat tab~~ — moved to Harness tab as a third mode (v0.10.1).
- ~~`cli.py` version stale at 0.9.0~~ — bumped to 0.10.0.
- ~~Config tab label says tab (8)~~ — corrected to tab (7).
- ~~Config tab hover tooltips~~ — `data-tip` on all editable fields, delegated mouseover handler, TOOLTIPS map in JS.
- ~~Busybox wrapper broken call syntax~~ — `[bb, "sh", "-c", cmd]` instead of `[bb, "-c", cmd]`.
- ~~Session cache unbounded growth~~ — hard cap at 1000 entries, trims to 800 oldest-first.
- ~~Embedding error handling~~ — model-not-pulled 404, empty array, non-JSON all caught with actionable messages; `search()` and `search_grouped()` return `[]` on failure.
- ~~ChromaDB thread safety~~ — `threading.Lock()` on all collection ops in `ChromaBackend`.
- ~~JACK TUI~~ — dropped. Code already removed; remaining comment refs in `runtime_config.yaml` scrubbed.
- ~~Operator shell sandbox~~ — bwrap sandbox implemented in `system_info.py`. Standard profile (no `/app`, no `/home`, no network); GPU profile for nvidia-smi. Busybox wrapper retained as fallback. `user=` kwarg bug fixed. Allowlist corrected (`nproc`, `cut`, `uptime`, `nvidia-smi` were missing). Blocked patterns tightened. `bubblewrap` added to Dockerfile.
- ~~Embex vector backend abstraction~~ — `VectorBackend` ABC (`upsert`/`query`/`count`), `ChromaBackend` implementation, `make_backend()` factory in `storage/backends/`. `vector_backend: chromadb` config key. All call sites updated. `chroma_path` vs `vector_store_path` mismatch resolved.
- ~~Plugin system for tools~~ — `plugin_loader.py` auto-discovers `*Tool` classes from `./plugins/*.py`. Per-plugin enable flags in config. Conflict protection against built-ins. Bundled examples: `dice`, `units`, `wiretap_summary`.
- ~~Conversation forking via z-command~~ — `z: fork` copies current conversation to a new UUID in SQLite, returns synthetic response with new ID, logged to wiretap.
- ~~Model performance dashboard charts~~ — tokens/sec chart (green bars) and cache hit rate stat cards added. `avg_tokens_per_sec` computed in SQL. `/api/v1/routing-stats` endpoint reads wiretap tail. Perf table updated with `tok/s` column.
