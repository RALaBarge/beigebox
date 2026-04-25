---
name: host-audit
description: Use when the user wants to audit / profile / fingerprint one or more machines to learn OS, hardware, network interfaces, installed virtualization stacks, running containers/VMs, and listening services. Takes a list of targets — local, SSH alias, user@host, or user:password@host — and works against Linux and macOS regardless of authentication style. Produces a markdown report (default), JSON, or a CLAUDE.md-ready section.
---

# host-audit

`scripts/audit.sh` — profile machines given as a list. Unlike the `services-inventory` skill (which answers "what's running right now"), this skill gathers **who is this machine**: OS, hardware, network layout, installed stacks, configured containers/VMs, and listening services. Intended for filling out CLAUDE.md or building a one-time reference of an environment.

## When to invoke

- User asks to audit / inventory / profile / survey one or more hosts
- User is setting up a new CLAUDE.md and wants host facts populated
- User wants a single-pass "tell me what's on box X"

## Usage

```bash
# audit local machine
scripts/audit.sh local

# audit a remote via SSH alias / key auth
scripts/audit.sh user@server.example

# audit a remote using a password (sshpass)
scripts/audit.sh user:password@192.168.1.214

# label the output (appears in the report heading)
scripts/audit.sh debian=ryan@192.168.1.235

# multiple targets in one pass
scripts/audit.sh local debian=ryan@192.168.1.235 mac=useruser:1234@192.168.1.214

# output forms
scripts/audit.sh --format markdown TARGET ...    # default, human-readable
scripts/audit.sh --format json     TARGET ...    # machine-readable
scripts/audit.sh --format claude   TARGET ...    # pasteable into CLAUDE.md

# read targets from a file (one per line; label=spec supported)
scripts/audit.sh --file targets.example

# save a timestamped JSON snapshot per target (for version tracking)
scripts/audit.sh --save debian=ryan@192.168.1.235

# diff each target's latest snapshot against its previous one
scripts/audit.sh --diff debian mac

# list saved snapshots (all labels, or one)
scripts/audit.sh --list
scripts/audit.sh --list debian

# override snapshot directory
scripts/audit.sh --snapshot-dir /path/to/dir --save local
```

## Snapshots & schema

- **Schema**: `schema.json` (alongside `SKILL.md`) documents the shape of each
  saved snapshot. Current `schema_version: "1"`.
- **Location**: `$XDG_STATE_HOME/beigebox/host-audit/snapshots/<label>/<UTC>.json`
  (default: `~/.local/state/beigebox/host-audit/snapshots/`).
- **Layout**: one directory per label, one file per audit, `latest.json` symlink
  points at the most recent.
- **Diff**: `--diff` compares the latest snapshot to the previous one per label
  and reports changes in kernel, OS, hostname, memory, installed tools, docker
  containers, LXC containers, IPs, and listener ports. Unchanged fields are
  omitted.
- **Passwords**: sshpass passwords are **not** written into snapshots — only
  `user@host` is persisted in the `target` field.

## Target forms

| Form | Means |
|------|-------|
| `local` / `localhost` | probe the machine where the skill is invoked |
| `user@host` | SSH with whatever auth ssh normally uses (keys, agent) |
| `alias` | SSH alias from `~/.ssh/config` |
| `user:password@host` | SSH with `sshpass` (requires `sshpass` installed locally) |
| `label=TARGET` | Any of the above, with a display label |

## What it gathers

- OS family, distro + version, kernel, architecture, hostname
- CPU count, memory total, GPU (if detectable), root/home disk usage
- All network interfaces + IPv4 addresses (LAN, Tailscale, bridges, veth, etc.)
- Installed stacks: docker, podman, lxc (classic), lxd, incus, virsh/libvirt, systemd-nspawn, qemu, colima, lima, orbstack, multipass, brew
- Running docker containers (if user has socket access)
- LXC container dirs (if `/var/lib/lxc/` readable)
- LXD / Incus lists (if CLIs respond without sudo)
- libvirt domains (if virsh works without root)
- Listening TCP ports (ss on Linux, lsof on macOS)
- Home-directory hints: `~/.ollama`, `~/.open-webui`, etc.

## Requirements

- `jq` locally (required for any format)
- `ssh` locally for remote targets
- `sshpass` locally if any target uses `user:pass@host`
- For best results on remote: `bash`, `ss` or `lsof`, basic coreutils

## Safety & limits

- Nothing requiring sudo is attempted. If sudo is required to see something (e.g. LXC container details, full listener process names), the report flags it as "needs sudo".
- `user:pass@host` passes the password on sshpass's command line — visible in `ps` momentarily. Avoid for shared hosts; prefer key auth when possible.
- Each probe step has a timeout so a dead host fails fast.
- `--format claude` emits opinionated markdown (tables, headings) intended as a drop-in section; review before pasting into CLAUDE.md.
