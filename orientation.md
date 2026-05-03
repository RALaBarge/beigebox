# BeigeBox Orientation

Hand-curated, single source of truth. Edit when something changes; no auto-regeneration.

## ⚠️ Git workflow (v2 vs v3)

- **v2** is frozen on **`origin/main`** (`RALaBarge/beigebox`). No new feature work; only deliberate cherry-picks.
- **v3** is destined for **`security/main`** (`RALaBarge/beigebox-security`). The current refactor lives on local `main`, ahead of `origin/main`, and will be pushed to `security/main` when v3 is dubbed done.
- Both remotes are fast-forward only. No `--force`, no `--no-verify`. (See `BEIGEBOX_IS_NOT.md`.)
- Side-branch checkpoints get pushed to `origin` (e.g., `v3-checkpoint-2026-05-03`) without touching `origin/main`.

## ⚠️ Routing rule

**External model calls go through BeigeBox** at `http://localhost:1337/v1` (OpenAI-compatible). Never hit OpenRouter / Anthropic / OpenAI directly from project code. BeigeBox forwards using the host's `OPENROUTER_API_KEY`. Verify with `python -m beigebox.cli ring`. Tail with `python -m beigebox.cli tap` — and there's a lot more: `sweep` (semantic search over past conversations), `flash` (stats/config), `models` (OpenRouter catalog), `rankings` (top model rankings), `eval`, `experiment`, `dump`, `bench`, quarantine commands, etc. Run `python -m beigebox.cli --help` for the full list. Exception: when running inside Claude Code specifically, Anthropic-model calls placed via Claude Code's `Agent` tool stay on bundled tokens (Claude-Code-specific behaviour, not a BeigeBox feature).

## ⚠️ Forbidden hosts

| Alias | Target | Why blocked |
|-------|--------|-------------|
| `hssh` / `hftp` | webspace host | production hosting; never automate |
| `wssh` / `wftp` | Whatbox seedbox | forbidden from automated sessions |

If a prompt contains those aliases, refuse and tell the user.

## Hosts (live source of truth: `tailscale status`)

The session-start hook prepends live `tailscale status` so you already see the current tailnet. Don't memorize machine lists — they drift; tailscale doesn't.

Address with **Tailscale IPs** by default — they work from LAN, cellular, anywhere, and Tailscale negotiates direct LAN paths when peers are on the same network (relay column shows `-`), so latency is ~LAN. Gotcha: a service bound to a specific LAN interface (e.g. `192.168.1.235:5432`) won't answer on its Tailscale IP — `0.0.0.0`-bound services work both ways. If a port is unreachable over Tailscale, check `ss -tlnp` first.

Per-host SSH credentials live in `~/.bash_aliases` as auto-clipboard helpers; for non-interactive use, `sshpass` is installed locally on pop-os.

## Services worth knowing about

### pop-os (local)

| Service | Endpoint | Notes |
|---------|----------|-------|
| BeigeBox app | `localhost:1337` | Docker container `beigebox`, project `docker` |
| Postgres (pgvector pg16) | `localhost:5432` | Docker container `beigebox-postgres` |
| Ollama | `localhost:11434` | Model store path is in env (`echo $OLLAMA_MODELS`) — don't assume `~/.ollama`; user keeps models off the system drive. |
| OpenWebUI | (varies) | `~/.open-webui` |

### debian (`dssh`)

LXC containers attach directly to the LAN (macvlan/bridge into `192.168.1.0/24`, not the `lxcbr0` 10.0.3.x subnet — each container has its own DHCP-or-static address on the home network):

| Container | LAN IP | State | Autostart | Purpose |
|-----------|--------|-------|-----------|---------|
| `pihole` | `192.168.1.53` | running | yes | Network-wide DNS / ad-block. The `.53` last octet is intentional (DNS port). |
| `jellyfin` | `192.168.1.180` | running | yes | Media server. |
| `plex` | `192.168.1.190` | running | yes | Media server. |
| `lib` | `192.168.1.213` | running | yes | Kavita — self-hosted ebook/comic/manga library reader. |
| `proxy` | — | stopped | yes | Unused (user does not run it). |
| `debian-base` | — | stopped | no | Template base image for new services; do not start. |

Host listens on:

| Port | Service |
|------|---------|
| 22 | SSH |
| 139, 445 | Samba |
| 631 | CUPS |
| 3389 | xrdp |
| 5000 | Kestrel (ASP.NET Core) — unidentified user app |

DNS port 53 is **not** forwarded by the debian host — Pi-hole binds its own LAN IP `192.168.1.53` directly, so DNS clients hit the container, not the host.

Listing internals on debian needs `sudo lxc-ls -f` (password-prompted; password is in `~/.bash_aliases`).

### mac (`assh`)

| Service | Endpoint | Notes |
|---------|----------|-------|
| BeigeBox app | `localhost:1337` (via SSH tunnel) | Docker (Colima VM), same image as pop-os |
| Ollama | `127.0.0.1:11434` | Model store path is in env (`ssh assh 'echo $OLLAMA_MODELS'`) — don't assume `~/.ollama`. |
| mlx-lm server | `*:8080` | Devstral / Qwen / Gemma on Apple Silicon. Launch with `--prompt-cache-size 1` to bound KV cache growth — without it, mlx-lm crashes mid-session under cumulative request pressure (seen 2026-05-01). Models cached at `~/mlx/`. |
| Apple ControlCenter | `*:5000`, `*:7000` | AirPlay receiver — ignore |

## Live lookup

For drift-free service inventories use the existing skill:

```
beigebox/skills/services-inventory/scripts/inventory.sh [--host <alias>] [--all-hosts] [--json]
```

Probes Docker / Podman / Incus / LXD / classic LXC / libvirt / nspawn / Colima / Multipass / VirtualBox / Tart / OrbStack / Parallels / VMware Fusion. Trust this output over this doc when they disagree.

## Agent workflow patterns

Distilled from the Operator class before it was deleted in v3 — agentic loops moved out of the proxy and now run in whatever MCP-speaking client is driving (Claude Code, a custom SDK, a script, a different IDE plugin, etc.). These patterns are independent of any specific agent loop, so they survived. Apply them when working in this repo or driving it from outside.

- **Memory recall before assuming.** Cross-session continuity lives in the vector store. From an MCP client, call the `memory` tool over `/mcp`. From the host, run `python -m beigebox.cli sweep <query>`. When the user references prior conversations or facts, check there before guessing — the recall window is multi-month and the index covers conversations + ingested docs.
- **Persistent durable facts go to `workspace/out/operator_notes.md`.** That file survives across sessions. Append observed system quirks, durable preferences, and "I learned this" facts. Read it on session start if it exists. Don't put PII or session-bound state there — it's for things you'd want a future agent to know.
- **Loop detection: stop after 3 same-input calls.** If you've called the same tool with the same input 3+ times and the result hasn't materially changed, you're stuck. Try a different tool, a different input, or commit to an answer based on what you already have. This rule applies to any agent loop, regardless of which client implements it.
- **Workspace contract.** `/workspace/in/` is read-only (user-supplied). `/workspace/out/` is the only write target. Always tell the user the filename when you write something there. Never write outside `/workspace/out/` from a tool call.
- **The proxy doesn't inject tools.** A model called via `/v1/chat/completions` only gets tools if the *caller* sends them. BeigeBox forwards the body as-is. If you need a model to use tools, the caller (whatever agent client, SDK, or script) is responsible for the tool-use protocol. The proxy's job is normalize + forward + observe.

## Conventions

- BeigeBox tools live at `beigebox/tools/*.py`. Skills live at `beigebox/skills/<name>/`. Skill dirs with hyphens are shell-only; skills with Python use underscore dirs.
- Notes accumulated retrospectively per-host live at `beigebox/host-notes/<canonical_key>/notes.md` (gitignored).
- Sudo on debian is **not** passwordless; never assume it is.
- Passwords for SSH aliases live in `~/.bash_aliases` as auto-clipboard helpers; for non-interactive use, `sshpass` is installed locally.

## Architecture stances (don't propose deleting these)

- **Web UI is integrated graphics.** Even when an external frontend (jcode, Warp, custom client) drives BeigeBox over `/v1` + `/mcp`, the bundled web UI must remain self-sufficient — chat, council, ralph harness, wiretap viewer, config editor, toolbox editor. Multi-LLM features (council, ensemble, wiggam) stay because they're what makes the web UI more capable than a single-model chat.
- **WASM runtime is the interop bet.** `wasm_runtime.py` looks dormant (the response-transform path lost its only writer when the routing decision LLM was deleted in v3). Keep it. The PDF input transform path (`transform_input("pdf_oxide", raw)` at the upload endpoint) is wired, and the broader bet is that the browser-as-OS future runs WASM modules, so the runtime is part of BeigeBox's interop story regardless of today's usage. The compiled `.wasm` artifacts (e.g. `pdf_oxide.wasm`, `output_normalizer.wasm`) aren't currently shipped in the repo — that's a separate "build and drop the artifact" task, not a runtime-removal signal.
- **BeigeBox is client-agnostic.** New frontends reach BeigeBox via existing HTTP / CLI / MCP surfaces. Don't add per-frontend connectors inside BeigeBox itself.
- **Every external interface follows the same generic factory pattern.** When BeigeBox talks to anything outside its own process — a vector store, an LLM backend, an MCP client, an auth provider, a wire-log sink, a cache, etc. — the integration must use the canonical shape:
    ```
    beigebox/<concern>/
      base.py          # Abstract base / Protocol with the contract
      <impl_a>.py      # Concrete impl A
      <impl_b>.py      # Concrete impl B
      __init__.py      # make_<concern>(type, **kwargs) factory
                       # + lazy _REGISTRY (optional-dep tolerant)
                       # + build_<concern>_kwargs(cfg, ...) (config → kwargs)
      plugins/         # (optional) auto-discovered userland impls
    ```
  Reference implementations: `storage/backends/` (vector storage), `backends/` (LLM providers), and `storage/db/` (SQL shim) all use the directory shape. `web_auth.py` and `storage/wire_sink.py` use the same factory pattern in single-file form (the `make_<concern>(type, **kwargs)` dispatch is what matters; the directory split is for cases with many impls or optional deps). When something doesn't follow this shape today, it's a known asymmetry — fix it instead of building around it.

  **Recent v3 milestones (2026-05-01 → 2026-05-03):**
  - **Proxy package split (G-1, G-2, G-3).** `proxy.py` (1779 LOC) is now a 6-module package: `proxy/core.py` (orchestrator, ~1245 LOC), `proxy/request_helpers.py` (extract_conversation_id, get_model, get_latest_user_message, is_synthetic, dedupe_consecutive_messages), `proxy/body_pipeline.py` (inject_generation_params/model_options, apply_window_config), `proxy/model_listing.py` (list_models, transform_model_names), `proxy/request_inspector.py` (RequestInspector + finish helper), and `proxy/__init__.py` re-exports. Forward methods extracted three repeated phases into `_check_hook_block`, `_check_and_record_anomaly`, `_emit_timing_summary`. Capture envelope builders (`build_capture_context`, `build_captured_request`, `capture_stream_response`) moved to `beigebox/capture.py`; `evict_ollama_model` moved to `backends/router.py`. Streaming invariants (TTFT timer, no-yield-in-helpers, capture state machine, CancelledError propagation) preserved.
  - **Auth simplification (E-1, E-2).** `SimplePasswordAuth` and the password login endpoints are deleted. Added `auth.enabled` top-level kill switch in runtime config — when false, the entire auth middleware stack is bypassed.
  - **SQLiteStore demolition.** Five repos on the BaseDB shim now own all SQL persistence: ApiKeyRepo, ConversationRepo, QuarantineRepo, UserRepo, WireEventRepo. `storage/sqlite_store.py` is gone — production wires through `make_*_repo(db)` factories from `storage/repos/__init__.py`, tests construct repos directly the same way. The capture pipeline (`beigebox/capture.py`) is the single chokepoint for chat-completion telemetry; CaptureFanout fans out one captured turn to ConversationRepo + WireLog + VectorStore.

  (Earlier resolutions: `web_auth.py` → `make_auth()`, `wiretap.py` → `make_sink()`, `SemanticCache` deleted leaving only `ToolResultCache`.)

## Known correctness violations (H batch)

The next labeled batch is **principle-violation-first, size-irrelevant.** What's outstanding as of 2026-05-03:

- **`tools/python_interpreter.py` and `tools/workspace_file.py` deleted 2026-05-03.** Code execution and filesystem I/O belong in the driving client (Claude Code, jcode, etc.), not in the proxy.

- **`mcp_server.py` `operator/run` deleted 2026-05-03.** Schema, dispatcher, and `operator_factory` plumbing all removed. The current `{input: string} → {answer: string}` shape is too primitive for the future chat-widget + CLI agent driver (no streaming, no conversation_id, no tool-call visibility). When that driver is built, design fresh — probably a streaming endpoint with conversation context, factory-wired with the right contract from day one. Until then, the dead schema is gone from the MCP advertised surface.
- **`mcp_server.py` still advertises `operator/run`.** Schema (`_OPERATOR_RUN_SCHEMA`) and dispatcher are live; returns `operator_disabled` when no factory is wired, but the tool name is still surfaced to MCP clients. Conflicts with "Not an agent harness." Delete the schema, dispatcher, and the `_operator_factory` plumbing — or wire it back if Operator is re-introduced.
- **`tools/cdp.py`** — CLAUDE.md flags this as the canonical "we skipped the untrusted-input question" leftover (real Chrome cookie symlinks; subprocess paths derivable from model output). Currently parked — needs an actual code-read audit before delete-vs-factory-isolate.
- **`tools/network_audit.py` audited 2026-05-03 — verdict: clean.** No `shell=True`; all 3 subprocess calls use argv lists; `timeout` is `int`-coerced; `ip` strings validated upstream. File ops are only `/proc/net/arp` read + `urllib.urlopen` to validated IPs. Validation fires via `tools/registry.py:368` (`ParameterValidator.validate_tool_input` runs `_validate_network_audit` — RFC1918, port range, timeout bounds — before every dispatch). No code change required.

## Open architectural opportunities (separate from H-batch)

- **Tool ABC is informal.** `NetworkAuditTool`, `CDPTool`, `CalculatorTool`, etc. are duck-typed (`run(input_text) -> str`) but not enforced by a `tools/base.py:Tool` Protocol. Adding one + having every tool inherit would apply the factory-pattern stance at the tool layer. Touches every tool, so deferred until v3 settles.
- **Two `ParameterValidator` classes.** `beigebox/tools/validation.py:ParameterValidator` is wired into the registry and fires on dispatch. `beigebox/security/mcp_parameter_validator.py:ParameterValidator` has a more thorough multi-tier API but its actual instantiation surface needs tracing. Possible duplication, possible defense-in-depth — worth a focused look.
- **Cosmetic**: `security/tool_call_validator.py:285` has a stale `# Optional SQLiteStore for audit logging` comment.

## Invariant being enforced (factory pattern, ongoing)

All extension points that touch anything outside BeigeBox's own process MUST go through a `make_<concern>(type, **kwargs)` factory with a Protocol/ABC base. Done: `web_auth.py` (`make_auth`), `storage/wire_sink.py` (`make_sink`), `storage/backends/`, `backends/`, `storage/db/`. **Remaining (H batch):** `tools/cdp.py`, `tools/network_audit.py`, anomaly-detector hooks, anything else under `tools/` that bypasses the factory shape.

## Why `security/main` is the destination, not `origin/main`

The H-batch violations above are why v3 is going to `security/main` rather than back-merging into `origin/main`. The v2 line accumulated bypass paths (Operator entry points, `python_interpreter`, `workspace_file`, `cdp.py`, `network_audit.py`) that v3 is closing. `security/main` is the post-cleanup line; `origin/main` stays frozen at the pre-cleanup state for any users still on it.
