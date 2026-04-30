# Agents & Orchestration

> **v3 reframe.** The in-proxy agent (Operator) and the multi-turn harness orchestrator were deleted in v3. Agent loops moved out of the proxy and now run in whatever **MCP-speaking client** is driving — Claude Code, a custom SDK, an IDE plugin, etc. BeigeBox exposes its tool inventory at `/mcp` (and `/pen-mcp` for offensive-security tools); the client handles tool selection, the loop, and the conversation state.

## How agents drive BeigeBox now

```
┌──────────────────────┐                    ┌──────────────────────┐
│  Your agent client   │                    │  BeigeBox            │
│  (Claude Code, etc.) │ ── MCP /mcp ────▶  │  Tool registry       │
│                      │ ◀── tool result ── │  Tool implementations│
│                      │                    │                      │
│  loop:               │                    │                      │
│    pick tool         │                    │  /v1/chat/completions│
│    invoke via /mcp   │ ── chat ─────────▶ │  Backend router      │
│    decide next move  │ ◀── response ───── │  Provider (OR/Ollama)│
└──────────────────────┘                    └──────────────────────┘
```

The client owns: the loop, the tool-selection logic, the conversation state. BeigeBox owns: the tool implementations, the model proxying, the memory store, the wiretap.

## What's wired today

### `/mcp` — general tools
- `memory` — semantic recall over `conversations.db` + ingested docs
- `web_search`, `web_scraper`
- `workspace_file` — read/write under `/workspace/{in,out}/`
- `system_info` — sandboxed shell-allowlist
- `cdp` — headless Chromium control
- `browserbox` — higher-level browser automation
- `aura_recon`, `atlassian`, `sf_ingest`
- `plan_manager` — `workspace/out/plan.md` lifecycle
- `dice`, `doc_parser`, `repo`, `units`, `wiretap_summary`, `zip_inspector`
- `pdf_reader` — Python pdf_oxide binding (separate from the WASM PDF transform path)
- `python_interpreter` — `bwrap`-sandboxed
- A handful of others — see `beigebox/tools/registry.py` for the live list.

### `/pen-mcp` — offensive-security tools (off by default)
53 wrapped *nix offensive tools (nmap, nuclei, sqlmap, ffuf, hydra, impacket, …) on a separate registry so they don't pollute the default tool surface. Destructive wrappers require an explicit `"authorization": true` field in the input. See [security_mcp/README.md](../beigebox/security_mcp/README.md).

## Multi-LLM features in the built-in web UI

Even with agent loops moved out of the proxy, the **integrated-graphics** path keeps three multi-LLM patterns so the bundled web UI is more than a single-model chat:

- **Council** (`POST /api/v1/council/propose` + `/execute`) — proposer + voter pattern. Web UI: Chat → "Council" sub-tab.
- **Ensemble** (`POST /api/v1/harness/ensemble`) — parallel models + judge. Web UI: Harness tab → "Ensemble" mode.
- **Wiggam** (`POST /api/v1/harness/wiggam`) — multi-agent planning consensus. API-only.

These are intentionally *server-side* multi-LLM features (you POST a query, BeigeBox runs the multi-LLM coordination and streams events back). They're independent of any external agent client.

## Test-driven self-improvement loop

**Ralph** (`POST /api/v1/harness/ralph`) — gated on `harness.ralph_enabled: true` and admin-key. Reads a spec, runs an iterative agent that edits files until a `test_cmd` passes. Security-hardened in v3 (no `shell=True`, argv-only execution).

## Cross-session memory

Two ways to use it:

```bash
# Direct CLI from the host
beigebox sweep "what did we discuss about chemistry"

# From any MCP client over /mcp
{"method":"tools/call","params":{"name":"memory","arguments":{"input":"chemistry"}}}
```

The vector store is Postgres + pgvector (migrated from chroma). Conversation messages are auto-indexed.

## Multi-turn conversations

Link client requests via `session_id` (or `conversation_id`) to maintain wiretap correlation. The proxy doesn't manage context windows for you anymore (auto-summarize survives but the agent loop owns its own context); session IDs exist purely for log + memory correlation.

```json
{
  "model": "x-ai/grok-4-fast",
  "messages": [{"role": "user", "content": "What did we talk about yesterday?"}],
  "conversation_id": "sess-abc123"
}
```

The MCP `memory` tool can use `conversation_id` to scope its recall.

---

See [Architecture](architecture.md) for the full pipeline and subsystem map.
