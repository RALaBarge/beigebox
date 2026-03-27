# ✅ COMPLETE — Implemented and archived (pre-2026-03-16)

# BeigeBox WASM/WASI Integration Design

## Overview

WASM modules act as a **pipeline transform layer** — sitting inline in the proxy path, receiving the raw buffered response from the backend and optionally mutating it before it reaches the client.

```
Client → BeigeBox → Backend
                      ↓ (SSE stream)
              [buffer full response]
                      ↓
              [WASM transform module]
                      ↓
              (possibly modified response)
                      ↓
Client ←──────────────
```

---

## ABI: WASI stdio

The interface contract is **WASI stdio** — the simplest possible boundary:

- WASM module reads raw JSON bytes from **stdin**
- WASM module writes (possibly modified) JSON bytes to **stdout**
- No custom memory management required
- Works in any language that compiles to WASI (Rust, C, Go, AssemblyScript, etc.)
- `wasmtime-py` handles the host-side plumbing

---

## Insertion Points in `proxy.py`

### Non-streaming (`forward_chat_completion`)

```python
# Current
data = resp.json()

# With WASM
data = resp.json()
if wasm_module:
    data = await wasm_runtime.transform_response(wasm_module, data)
```

### Streaming (`forward_chat_completion_stream`)

The buffer already exists — WASM operates on the assembled response after streaming finishes, before logging:

```python
complete_text = "".join(full_response)
if wasm_module:
    complete_text = await wasm_runtime.transform_text(wasm_module, complete_text)
if complete_text:
    self._log_response(conversation_id, complete_text, model)
```

---

## Router Integration

WASM modules are selected by the router, paired with a backend decision. The `Decision` dataclass gains one new field:

```python
@dataclass
class Decision:
    model: str = ""
    needs_search: bool = False
    needs_rag: bool = False
    tools: list[str] = field(default_factory=list)
    reasoning: str = ""
    confidence: float = 1.0
    fallback: bool = False
    wasm_module: str = ""          # ← new: path or name of WASM transform module
```

The decision LLM can then select a module the same way it selects a backend route.

---

## Config Schema (`config.yaml`)

```yaml
wasm:
  enabled: false
  modules_dir: "./wasm_modules"      # Drop .wasm files here
  modules:
    my_filter:
      path: "./wasm_modules/my_filter.wasm"
      enabled: true
      description: "Example response filter"
    pii_redactor:
      path: "./wasm_modules/pii_redactor.wasm"
      enabled: false
      description: "Strip PII from responses"
  default_module: ""                 # Optional: always run this module
  timeout_ms: 500                    # Max time to wait for WASM transform
```

---

## New File: `beigebox/wasm_runtime.py`

Responsibilities:
- Load `.wasm` files at startup via `wasmtime-py`
- Expose `async transform_response(module_name, data: dict) -> dict`
- Expose `async transform_text(module_name, text: str) -> str`
- Enforce timeout — if WASM exceeds `timeout_ms`, pass through unmodified
- Log transform activity to wiretap

---

## Implementation Steps

### Step 1 — Schema + config
- Add `wasm_module: str = ""` to `Decision` dataclass in `decision.py`
- Add `wasm:` block to `config.yaml`
- Update decision LLM system prompt to include available WASM modules

### Step 2 — Runtime
- Create `beigebox/wasm_runtime.py` with loader and `transform_*` methods
- Add `wasmtime-py` to `requirements.txt`

### Step 3 — Wire into proxy
- Extend `_hybrid_route` to carry `wasm_module` from `Decision`
- Insert transform calls at both insertion points in `forward_chat_completion` and `forward_chat_completion_stream`

### Step 4 — Example module
- Write a minimal Rust/WASI passthrough module as a reference implementation
- Add to `wasm_modules/` with build instructions

---

## Design Principles

- **Zero latency impact on passthrough**: if no module is selected or WASM times out, request flows through unmodified
- **Language agnostic**: any WASI-compatible language works
- **Config-driven**: modules enabled/disabled per config flag, no code changes needed
- **Wiretap visible**: all transforms logged with before/after diff summary
