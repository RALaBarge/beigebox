# Session — March 5, 2026

## Topics covered

1. pdf-oxide-wasi — WASM/WASI binary wrapper for pdf_oxide
2. BrowserBox — browser API middleware Chrome extension

---

## pdf-oxide-wasi

### Context

The session started by reviewing `pdf_oxide` as a "WASM/I tool". Clarified the
distinction: pdf_oxide in BeigeBox is a native Rust CPython extension (PyO3),
not a WASM module. The WASM transform pipeline (wasm_runtime.py) is an
unrelated subsystem for post-stream LLM response transforms.

Discovered `/home/jinx/ai-stack/pdf-oxide-wasi` already existed with a full
NEXT SESSION brief written from a prior conversation. Executed it.

### What we built

A `wasm32-wasip1` WASI binary that wraps pdf_oxide:
- stdin = raw PDF bytes, stdout = markdown
- Flags: `--format text|markdown`, `--pages N-M`, `--tables-only`
- 3.5MB release binary (LTO + stripped)
- Repo: https://github.com/RALaBarge/pdf-oxide-wasi

### Key decisions

**Why WASI and not native CLI?**
pdf_oxide_cli already exists as a native binary. The WASI target adds a new
distribution format: single `.wasm` file, no OS-native deps, runs on any
machine with wasmtime/wasmer/Node/Deno. Particularly useful for agent
frameworks that already embed a WASM runtime (BeigeBox does — wasmtime).

**Why not browser WASM (wasm32-unknown-unknown)?**
pdf_oxide already ships a browser WASM build. The gap was specifically the
WASI/server-side target. WASI gives us real stdin/stdout, which browser WASM
doesn't have without a shim layer.

**Blocker encountered: nightly required**
pdf_oxide 0.3.14 uses `ceil_char_boundary`, which is still nightly-only in
Rust 1.90 (tracking issue #93743 — only `floor_char_boundary` was stabilized
in 1.73; `ceil_char_boundary` remains unstable). System rustc was apt-installed
with no rustup. Installed rustup with nightly toolchain to unblock.

**API mismatch**
The README draft assumed `PdfDocument::from_bytes()`. Actual API is
`PdfDocument::open_from_bytes(data: Vec<u8>)`. Compiler error surfaced this
immediately. Also: `to_markdown()` takes `&ConversionOptions` not a bool;
no `get_form_fields()` method exists — form fields are included via
`ConversionOptions { include_form_fields: true, ..Default::default() }`.

**Portability summary**
| Runtime | Works |
|---|---|
| wasmtime / wasmer | Yes |
| Node.js WASI | Yes |
| Deno | Yes |
| Browser (direct) | No — needs JS WASI shim |

**Upstream contribution**
Opened issue #212 on yfedoseev/pdf_oxide offering the WASI wrapper.
Used Ryan's own words from the edited issue-draft.md.
Noted the nightly dependency as a blocker for upstreaming as a first-class target.

MIT license added. Repo pushed to https://github.com/RALaBarge/pdf-oxide-wasi.

---

## BrowserBox

### Context

Conversation moved to: "what would it take to build an app that is a shim that
lets an LLM have access to most browser APIs?" Ryan clarified: "middleware where
we can point it at different things towards the browser and keep a consistent API
internally." Same pattern as BeigeBox but browser subsystems instead of LLM backends.

### Architecture decision: relay process

Chrome MV3 background service workers cannot act as WebSocket *servers*
(no `listen()` equivalent). Options considered:

1. `chrome.sockets.tcpServer` — manual WS handshake, complex
2. Native messaging — secure, no open port, but requires registered host binary
3. **Relay process (chosen)** — tiny Python WS server (`ws_relay.py`).
   Extension connects as "browser" role, agents connect as "agent" role.
   Relay forwards messages between them. ~80 lines, uses `websockets` library.

Relay chosen because:
- Sidesteps all MV3 service worker limitations
- Multiple agents can connect simultaneously
- Easy to run under BeigeBox's process group or standalone
- No Chrome-specific plumbing; any agent speaks plain WebSocket

### Message protocol

```
Call:     { "id": "uuid", "tool": "namespace.method", "input": "..." }
Success:  { "id": "uuid", "result": "..." }
Error:    { "id": "uuid", "error": "message" }
```

`id` field enables multiplexing. Tool names are namespaced (`storage.*`,
`fetch.*`, `dom.*`) so new API surfaces are additive without conflict.

### v1 surface (Storage + Fetch)

Ryan selected Storage and Network/fetch as v1 via in-session question.

**storage.*** — chrome.storage.local/session + chrome.cookies
**fetch.*** — fetch from background context (carries real session cookies),
512KB body cap to prevent context window flooding.

### DOM adapter (added same session)

DOM requires a content script — background service worker has no page access.
Bridge: relay → background.js → chrome.tabs.sendMessage → content.js (in tab).

content.js handles:
- `query` / `query_all` — CSS selector → element info JSON
- `get_text` / `get_html` — text/HTML extraction with size caps
- `get_url` / `get_title` — page metadata
- `click` / `fill` — interaction (fill fires input+change events)
- `scroll` — by selector or {x,y}
- `wait_for` — MutationObserver, up to 15s timeout
- `snapshot` — compact page summary (title, URL, headings, links, interactive
  elements with generated CSS selectors) — intended as first call to orient LLM

On-demand injection fallback: if content script not yet injected (e.g. extension
just loaded), background catches the "Could not establish connection" error and
uses `chrome.scripting.executeScript` to inject content.js, then retries.

### BeigeBox integration

`beigebox/tools/browserbox.py` — operator tool shim
- Connects to relay as "agent" role on each call (stateless, ~10s timeout)
- Sends `{"role":"agent"}` handshake, then the tool call JSON
- Matches response by `id` field
- Registered in tool registry under `tools.browserbox`
- Disabled by default in config.yaml

### File locations

```
/home/jinx/ai-stack/browserbox/
├── ws_relay.py          — relay process (run this first)
├── manifest.json        — MV3, permissions: storage/cookies/tabs/scripting
├── background.js        — WS client, dispatcher, reconnect loop
├── content.js           — DOM operations, injected into pages
├── adapters/
│   ├── storage.js
│   ├── fetch.js
│   └── dom.js           — bridge to content.js via sendMessage
└── popup/               — connection status UI

beigebox/beigebox/tools/browserbox.py
beigebox/config.yaml     — tools.browserbox section (disabled by default)
```

### What's next (discussed at end of session)

Remaining browser API surface, prioritized:

**High value:**
- `tabs.*` — open/close/switch/list tabs
- `nav.*` — back/forward/reload/navigate
- `screenshot` — `chrome.tabs.captureVisibleTab()` → base64 PNG (vision LLMs)
- `clip.*` — clipboard read/write

**Interesting:**
- `history.*` — search browser history
- `network.*` — intercept XHR/fetch (good for SPA scraping)
- `inject.*` — arbitrary JS/CSS injection into page
- `pdf.*` — pipe current PDF tab through pdf-oxide-wasi WASM binary

**Sensitive (needs thought):**
- `identity.*` — OAuth token retrieval via chrome.identity
- `downloads.*`
