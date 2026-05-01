# BeigeBox Orientation

Hand-curated, single source of truth. Edit when something changes; no auto-regeneration.

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
  Reference implementations: `storage/backends/` (vector storage) and `backends/` (LLM providers). When something doesn't follow this shape today, it's a known asymmetry — fix it instead of building around it. **Current asymmetries (as of 2026-04-30):** `storage/sqlite_store.py` (conversations/audit/keys are SQLite-only god-object, no factory); `web_auth.py` (providers are loosely Protocol-conforming but no factory); `wiretap.py` (SQLite + JSONL sinks hardcoded, no `WireSink` ABC). (`cache.py` was an asymmetry as of the morning of 2026-04-30; `SemanticCache` was deleted later that day, leaving only `ToolResultCache` — different problem entirely, no abstraction needed.) New work in any of those areas is the time to introduce the pattern, not to tactically extend the monolith.
