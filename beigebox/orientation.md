# BeigeBox Orientation

Hand-curated, single source of truth. Edit when something changes; no auto-regeneration.

## ⚠️ Routing rule

**External model calls go through BeigeBox** at `http://localhost:1337/v1` (OpenAI-compatible). Never hit OpenRouter / Anthropic / OpenAI directly from project code. BeigeBox forwards using the host's `OPENROUTER_API_KEY`. Verify with `python -m beigebox.cli ring`. Tail with `python -m beigebox.cli tap` — and there's a lot more: `sweep` (semantic search over past conversations), `flash` (stats/config), `models` (OpenRouter catalog), `rankings` (top model rankings), `operator` (sandboxed agent), `eval`, `experiment`, `dump`, `bench`, quarantine commands, etc. Run `python -m beigebox.cli --help` for the full list. Exception: Anthropic-model calls placed via Claude Code's `Agent` tool stay on bundled tokens.

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
| (Python app) | `*:8080` | unidentified user app |
| Apple ControlCenter | `*:5000`, `*:7000` | AirPlay receiver — ignore |

## Live lookup

For drift-free service inventories use the existing skill:

```
beigebox/skills/services-inventory/scripts/inventory.sh [--host <alias>] [--all-hosts] [--json]
```

Probes Docker / Podman / Incus / LXD / classic LXC / libvirt / nspawn / Colima / Multipass / VirtualBox / Tart / OrbStack / Parallels / VMware Fusion. Trust this output over this doc when they disagree.

## Conventions

- BeigeBox tools live at `beigebox/tools/*.py`. Skills live at `beigebox/skills/<name>/`. Skill dirs with hyphens are shell-only; skills with Python use underscore dirs.
- Notes accumulated retrospectively per-host live at `beigebox/host-notes/<canonical_key>/notes.md` (gitignored).
- Sudo on debian is **not** passwordless; never assume it is.
- Passwords for SSH aliases live in `~/.bash_aliases` as auto-clipboard helpers; for non-interactive use, `sshpass` is installed locally.
