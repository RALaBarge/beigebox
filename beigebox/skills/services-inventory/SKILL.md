---
name: services-inventory
description: Use when the user asks what services/containers/VMs are running, how to connect to something (URL, IP, shell), or wants an inventory of endpoints across local and remote hosts. Works on Linux and macOS. Probes Docker, Podman, Incus, LXD, classic LXC, libvirt, systemd-nspawn, OrbStack, Colima, Multipass, VirtualBox, Parallels, VMware Fusion, and Tart. Supports SSH to remote hosts and emits either a human table or JSON.
---

# services-inventory

Runs `scripts/inventory.sh` to list running containers/VMs and how to reach them across every supported backend on Linux and macOS. Works locally or aggregated over SSH.

## When to invoke

- User asks "what's running?" / "what services are up?" / "what's on port X?"
- User asks how to connect to a specific service (URL, IP, container shell)
- You need a current endpoint map before suggesting an action against a service
- User asks about a service by name and you don't already know its address

Prefer this over guessing from memory — service addresses drift.

## Usage

```bash
# local, human table (default)
scripts/inventory.sh

# local, JSON (use this when you need to parse / reason over fields)
scripts/inventory.sh --json

# remote host via SSH alias or user@host; label is optional
scripts/inventory.sh --host debian=user@debian.home

# multiple hosts; 'localhost' includes the local host in the aggregation
scripts/inventory.sh --hosts localhost,user@debian.home

# read hosts from config file
scripts/inventory.sh --all-hosts

# include raw listening ports (noisy, off by default)
scripts/inventory.sh --include-listeners
```

Config file: `~/.config/beigebox/inventory-hosts`
Format: one entry per line, `label ssh-target` or just `ssh-target`. Lines starting with `#` are comments.

## Output fields

- `host` — label for the host the entry came from (`localhost` for local)
- `backend` — `docker`, `podman`, `incus`, `lxd`, `lxc`, `libvirt`, `nspawn`, `orb`, `colima`, `multipass`, `vbox`, `parallels`, `vmware`, `tart`, `listener`
- `name` — container/VM name (or process for listeners)
- `state` — `running`, `stopped`, etc. Non-running entries are filtered by default.
- `addresses` — IPs and/or port mappings
- `connect` — ordered hints, most useful first: `http://localhost:PORT`, `ssh user@IP`, `incus shell NAME`, `virsh console NAME`, etc.
- `extra` — backend-specific context (image name, profile, etc.)

## Requirements

- `jq` (required; `apt install jq` or `brew install jq`)
- `ssh` (only if using remote hosts)
- For any given probe to return data, the relevant CLI must be installed and the daemon reachable by the calling user (e.g. Docker socket access, `libvirt` group membership for `qemu:///system`).

## Behavior notes

- Each probe is time-limited (~5s). Missing CLIs or unresponsive daemons are skipped silently.
- Remote probing pipes this script over SSH stdin — nothing needs to be pre-installed on the remote beyond bash and jq.
- `--include-listeners` uses `ss` (Linux) or `lsof` (macOS) and filters out known container-runtime helpers (`docker-proxy`, etc.).
- libvirt probing targets `qemu:///system`. To see other users' VMs you may need to be in the `libvirt` group.
