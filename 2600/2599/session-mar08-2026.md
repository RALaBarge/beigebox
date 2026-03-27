# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# Session — 2026-03-08

## Topics covered

### Document knowledge and context architecture
Reviewed and completed the `beigebox_document_knowledge_and_context_architecture.md` doc
that was partially in `2600/`. Added missing stages 1–6 (upload → parse → chunk → embed →
dense retrieval → reranking), BeigeBox-specific callout notes, tightened OSS reference
section, and moved to `2600/2599/document-knowledge-context-architecture.md`.

### Audit of existing RAG/memory infrastructure

Mapped every stage of the document pipeline against what BeigeBox already had:

| Stage | Status |
|---|---|
| Document ingestion (workspace/in/, upload endpoint) | Done |
| Parsing | On-demand only (PdfReaderTool — reads whole file, no chunking) |
| Chunking | Missing |
| Embedding + indexing | Machinery present (nomic-embed-text + ChromaDB), not wired for docs |
| Dense retrieval | Works for conversations, not documents |
| Reranking | Missing (Phase 2) |
| Query normalization | Partial (MemoryTool query_preprocess, operator pre-hook) |
| Prompt assembly | No retrieval lane |
| Compression | Auto-summarization handles budget pressure |

### Blob store decision

Key insight: storing large tool results and document chunks inline in ChromaDB wastes space
and makes the DB unwieldy. Decision: content-addressed gzip blob store on disk, pointer in
ChromaDB metadata.

- sha256(content) → filename, natural dedup, no cleanup needed currently
- ChromaDB stores: embedding + metadata with `blob_hash`, truncated preview in `documents` field
- Retrieval is two steps (query → blob load) but that's fine for the use case
- Blobs are the verbatim backup copy — auditable, tamper-evident by hash

### DB scalability discussion

SQLite + ChromaDB for single-user household use: sufficient indefinitely.
- SQLite: file format, handles terabytes, WAL mode added for concurrent reads
- ChromaDB: HNSW, fine for hundreds of thousands of vectors; pluggable VectorBackend
  abstraction is the safety net if it ever needs swapping
- WAL mode was not previously enabled — added this session

### Tool capture design

Problem: operator tool loop I/O (inputs, results, intermediate reasoning) was TAP-logged to
SQLite but not vector-indexed, making debugging sessions hard.

Design decisions:
- **Opt-in per tool** via `capture_tool_io: bool = False` class attribute — no base class change
- **`max_context_chars`** limits what the operator sees; full result always stored in blob
- **Lazy session ID** — `uuid4().hex[:12]` generated on first tool call in a `run()` invocation
- **Hook mode separation** — pre/post hook tool calls go to workspace `.prehook`/`.posthook`
  dump files, NOT ChromaDB. Infrastructure noise stays out of the main data chain.
- **ChromaDB metadata filter** — `source_type: tool_result` / `source_type: document` /
  (no key = conversation message) keeps three data types in one collection but cleanly separated

### Post-hook addition

User requested post-hook alongside pre-hook. Post-hook:
- Receives completed user message + assistant response
- Fire-and-forget, never modifies response
- `_POST_HOOK_SYSTEM` in operator.py — "act, don't re-answer" prompt
- Wired into both streaming and non-streaming proxy paths via `asyncio.ensure_future`
- Config: `operator.post_hook.enabled`, same shape as pre_hook

---

## What was built

### New files
- `beigebox/storage/blob_store.py` — BlobStore class, sha256 + gzip, 2-char prefix subdirs
- `beigebox/storage/chunker.py` — `chunk_text()`, paragraph-aware with overlap, no deps
- `beigebox/tools/document_search.py` — DocumentSearchTool, `source_type: document` filter

### Modified files
- `beigebox/storage/vector_store.py` — `store_tool_result()`, `store_document_chunk()`
- `beigebox/storage/sqlite_store.py` — WAL mode pragma in `_connect()`
- `beigebox/agents/operator.py` — `blob_store` + `post_hook` params; lazy `_session_id`;
  `_dump_dir` for pre/post hook workspace dumps; `_POST_HOOK_SYSTEM`; new `_run_tool()` with
  capture/truncate/dump logic
- `beigebox/proxy.py` — `blob_store` on Proxy; `_run_operator_post_hook()`; wired into both
  pipeline paths; pre-hook updated to pass `blob_store`
- `beigebox/main.py` — global `blob_store`; init from `{vector_store_path}/blobs/`; passed to
  Proxy + Operator endpoints; `_index_document()` helper; background indexing on upload
- `beigebox/tools/registry.py` — DocumentSearchTool registered (enabled by default with vector_store)
- `web_search`, `web_scraper`, `pdf_reader`, `browserbox`, `python_interpreter` — `capture_tool_io`
  and `max_context_chars` class attributes added

### Test result
473 passed, 3 skipped — no regressions.

---

## Design principles confirmed this session

1. **Compression is a budget tool, not a memory format** — store raw, compress only at prompt assembly time
2. **Hook infrastructure ≠ user data** — pre/post hook tool I/O goes to workspace dumps, never ChromaDB
3. **Single ChromaDB collection, metadata separation** — simpler than multiple collections; `source_type` filter is sufficient; missing-key exclusion works naturally in ChromaDB
4. **Blob store is an audit trail, not just optimization** — verbatim retrieval by hash, tamper-evident
5. **SQLite + ChromaDB sufficient for household use indefinitely** — pluggable backend abstraction is the real insurance, not the DB choice
