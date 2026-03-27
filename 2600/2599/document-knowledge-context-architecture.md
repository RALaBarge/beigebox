# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# Document Knowledge and Context Architecture

## Overview

This document describes the architecture for adding document-aware retrieval and long-term memory to BeigeBox. It covers the ingestion pipeline, retrieval stages, memory layer design, and a phased implementation plan.

---

## The core pipeline

A strong document-aware system does **not** feed the whole file to the model every time. It usually:

```text
upload
-> parse
-> chunk
-> embed/index
-> retrieve relevant chunks
-> rerank
-> optionally compress bulky evidence
-> assemble prompt
-> send to operator model
```

Each stage is described below.

---

## Ingestion and retrieval stages

### Stage 1: Document ingestion

Accept documents via upload. Store the raw source as the canonical artifact — never discard it. The raw file is the ground truth; everything downstream is derived.

Supported formats at minimum: plain text, markdown, PDF (text layer), code files.

Index each document with metadata:
- source filename
- upload timestamp
- document type
- any user-supplied tags or workspace association

### Stage 2: Parsing

Convert the raw source into clean text. Key concerns:

- strip formatting that adds noise without adding meaning (decorative headers, footers, page numbers)
- preserve structure that carries meaning (section headers, code blocks, tables)
- preserve exact technical strings: commands, paths, version numbers, error messages
- for code files, preserve indentation and syntax structure

### Stage 3: Chunking with metadata

Split the parsed text into overlapping chunks. Each chunk should carry provenance metadata:

- source document ID
- section or heading path
- chunk index within document
- character or token offset

Good chunking strategies:

- respect paragraph and section boundaries where possible
- use a fixed token budget with overlap (e.g. 256 tokens, 32 overlap)
- for code, chunk at the function or class level where the structure permits

Avoid splitting in the middle of code blocks, tables, or lists.

### Stage 4: Embedding and indexing

Embed each chunk and store in a vector index. In BeigeBox, `nomic-embed-text` via Ollama and ChromaDB are already in place — use them.

Store chunk metadata alongside the embedding so it can be returned at retrieval time.

### Stage 5: Dense retrieval

At query time, embed the user query and retrieve the top-K chunks by cosine similarity.

K is a tunable parameter. A reasonable starting range is 5–10 chunks. The right value depends on chunk size, context budget, and task type.

### Stage 6: Reranking and candidate merging

A reranker scores the retrieved candidates against the query and reorders them by relevance. Cross-encoder rerankers (e.g. FlashRank-style) substantially improve retrieval precision over cosine similarity alone.

A good retrieval pattern is often:

```text
BM25 and/or dense retrieval
-> merge candidates
-> rerank
-> take top N
-> optionally expand with neighbors
```

This hybrid pattern is often stronger than semantic-only retrieval.

> **Complexity note for BeigeBox**: BM25 requires a separate keyword index (sqlite-fts5 is a natural fit given the existing SQLite store). It is a Phase 2 improvement — the dense-only baseline is worthwhile to ship first.

---

### Stage 7: Query-time normalization

Before retrieval, a lightweight model or rule-based layer can rewrite the user query into a cleaner canonical form.

Examples:

- expand abbreviations if known
- preserve exact entities and version strings
- strip conversational fluff
- extract likely intent
- generate alternate retrieval queries

This is a better place to do controlled compression than mutilating user text.

> **BeigeBox note**: The operator pre-hook (`proxy.py → _run_operator_pre_hook()`) already fires before system context injection and can absorb query normalization without an additional LLM hop. Consider wiring retrieval query generation into the pre-hook rather than adding a separate stage.

---

### Stage 8: Prompt assembly

Construct the final context from several lanes rather than one giant blob.

Recommended order:

1. system instructions
2. durable task constraints
3. relevant user or project memory
4. current user message
5. retrieved evidence chunks
6. optional episodic summary
7. optional recent transcript tail

This keeps the high-value items explicit and reduces the chance that crucial context gets buried in noisy dialogue.

---

### Stage 9: Prompt-time compression only where needed

If you are over token budget, compress the bulky sections only.

Good compression targets:

- verbose older transcript
- long tool logs
- large retrieved passages
- duplicated explanation text

Poor compression targets:

- exact commands
- paths
- port numbers
- model names
- error strings
- short but crucial task constraints

Compression is a **budget-management tool**, not your source of truth.

---

## Recommended memory architecture for BeigeBox

Use multiple memory layers instead of one monolithic memory blob.

### 1. Raw transcript store

Keep recent raw user/assistant turns.

Purpose:

- preserve exact local wording
- maintain near-term coherence
- support regeneration and auditing

### 2. Structured semantic memory

Store extracted durable facts.

Examples:

- user preferences
- stable environment details
- recurring constraints
- confirmed project facts

### 3. Decision log

Store explicit decisions made during the project.

Examples:

- chosen architecture
- model-routing policy
- accepted trade-offs
- rejected approaches

### 4. Task-state memory

Store the current active work state.

Examples:

- current task
- completed steps
- pending steps
- blockers
- artifacts generated

### 5. Episodic summaries

Store compact summaries of bounded conversation segments.

These are useful as compressed recollections, but they should not replace raw transcript or structured memory.

---

## What to save from LLM outputs

Save selectively, not indiscriminately.

### Usually worth saving

- confirmed conclusions
- final recommendations
- explicit decisions
- extracted facts
- exact technical identifiers
- action outcomes
- unresolved questions

### Usually not worth saving verbatim

- filler explanation
- rhetorical restatements
- dead-end branches
- speculative alternatives that were discarded
- verbose scaffolding text

---

## Should LLMLingua-style compression be run on LLM outputs and used as memory?

Usually **no**, not as the default canonical memory representation.

Why:

- it is lossy
- it can drift over repeated compression cycles
- it can preserve the wrong salience
- it may drop exact technical details
- it is optimized for one-shot budget reduction, not long-term truth preservation

A better pattern is:

```text
LLM output
-> raw transcript store
-> memory extractor -> structured records
-> optional episodic summary
```

Then later:

```text
prompt assembly
-> retrieve relevant items
-> compress only bulky sections if needed
-> send to operator model
```

---

## Purpose-built preprocessors and helper models

### 1. Embedding models

Use these for retrieval, clustering, and relevance selection.

They help answer:

- which prior turns matter?
- which chunks from docs matter?
- which memories are relevant now?

### 2. Small instruct models

Use these for:

- query rewriting
- memory extraction
- constraint extraction
- compact restatement
- task-state synthesis

These are more flexible than simple compressors.

### 3. Prompt compressors

Use these for token-budget pressure on large context blocks.

Good use cases:

- long retrieved docs
- old transcript blocks
- verbose logs

Bad use case:

- your only durable memory format

---

## Reference systems worth studying

### Product-style systems

**OpenAI Assistants / File Search**: the canonical reference for upload → chunk → index → retrieve → assemble. Useful as a baseline pattern even if you are not using the API.

**AnythingLLM**: explicit about the distinction between *attached* documents and *embedded* documents, and why a model does not automatically know everything about embedded files. Worth studying for UX patterns.

**Open WebUI knowledge / RAG**: local-first example of chunking and retrieval wired into a general chat interface.

**Onyx (formerly Danswer)**: production-oriented retrieval with permissions-aware document search.

### Frameworks

**LlamaIndex**: strong for ingestion pipelines, chunking, retrieval workflows, and reranking. Good if you want a modular data-to-context framework.

**Haystack**: explicit pipeline composition, hybrid retrieval, reranking. Good if you want a graph/pipeline view.

**LangChain / LangGraph**: broad integrations, orchestration, agentic retrieval. Useful but requires discipline to keep dependencies contained — worth noting given that `langchain` was recently removed from BeigeBox.

### Vector stores

**Qdrant**: good for hybrid retrieval infrastructure at scale.

**Chroma**: already in use in BeigeBox — the natural starting point.

**FAISS**: good for local experimentation if Chroma becomes a bottleneck.

---

## Suggested BeigeBox implementation order

### Phase 1: Basic usable system

1. upload and parse documents (land in `workspace/in/`, already exists)
2. chunk with metadata
3. embed and index raw chunks (nomic-embed-text + ChromaDB, already in place)
4. retrieve top K by similarity
5. assemble prompt with source handles
6. answer with operator model

### Phase 2: Make retrieval better

1. add BM25 keyword retrieval via sqlite-fts5 (extends existing SQLite store)
2. merge dense + keyword candidates
3. rerank top candidates (FlashRank or similar)
4. include neighboring chunks
5. preserve exact technical strings carefully

### Phase 3: Add memory architecture

1. raw transcript store (already in SQLite)
2. structured fact / decision / constraint extraction
3. episodic summaries
4. query-time memory retrieval
5. task-state lane in prompt assembly

### Phase 4: Add compression intelligently

1. compress only bulky evidence blocks
2. leave exact atoms untouched
3. compare compressed vs uncompressed answer quality
4. measure token savings vs answer degradation

---

## Practical design rule

Do not think of uploaded knowledge as "the model now knows the file."

Think of it as three assets:

1. **raw source of truth**
2. **retrieval index**
3. **derived compact knowledge artifacts**

The strongest systems usually use all three.

---

## Bottom line

A strong document-aware LLM system usually does **not** feed the whole file to the model every time. It retrieves, reranks, assembles, and compresses only where needed.

A strong memory system separates:

- raw transcript
- structured memory
- episodic summary
- retrieval over documents and history
- prompt-time compression only when needed

That division gives much better control over cost, relevance, and long-term fidelity.
