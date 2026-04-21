# Docker Configuration Architecture

This document explains how BeigeBox configuration works and where settings live.

## Single Source of Truth: `~/.beigebox/config`

Your personal configuration is stored in **`$HOME/.beigebox/config`** (created by `FIRST_RUN.sh`). This is the **only place** you should edit settings for:
- Backend port assignments (OLLAMA_PORT, WHISPER_PORT, KOKORO_PORT)
- Ollama model directory (OLLAMA_DATA)
- Docker profiles (PROFILES)
- Platform detection (PLATFORM, ARCH, IS_MACOS, IS_ARM64)

**Note:** BeigeBox web UI is always on port **1337** (fixed, not customizable).

**Why this location?**
- Lives in your home directory, not in the repo
- Survives git operations (clones, resets, pulls)
- Won't be overwritten by future code changes
- Can be safely backed up, shared between machines, etc.

## Configuration Flow

```
┌─────────────────────────────────────────────────────────┐
│  User: runs ./FIRST_RUN.sh (or edits config manually)   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │  ~/.beigebox/config (SOURCE) │
        │  ───────────────────────────  │
        │  BEIGEBOX_PORT=1337          │
        │  OLLAMA_PORT=11434           │
        │  WHISPER_PORT=9000           │
        │  KOKORO_PORT=8880            │
        │  OLLAMA_DATA=/path/to/ollama │
        │  PROFILES=apple,cdp          │
        └──────────────┬───────────────┘
                       │
        ┌──────────────┴──────────────┐
        │                             │
        ▼                             ▼
  ./launch.sh sources it      FIRST_RUN.sh syncs to
  and exports to docker-compose   docker/.env
        │                             │
        ▼                             ▼
    docker-compose.yaml reads docker/.env
    ${BEIGEBOX_PORT:-1337}      (for docker-compose env_file)
        │
        ▼
  Container sees: -p 1337:8000
```

## File Responsibilities

### `~/.beigebox/config` — USER OWNED
**Your personal settings. Safe to edit directly.**

Example:
```bash
BEIGEBOX_PORT=8080           # Custom port
OLLAMA_PORT=11434
OLLAMA_DATA=/home/you/.ollama
PROFILES=apple,cdp
```

**When you change this file:**
- Just run `./launch.sh` again
- No restart needed; changes take effect on next launch

### `docker/.env` — AUTO-SYNCED
**Synced from `~/.beigebox/config` by `FIRST_RUN.sh` and `launch.sh`.**

DO NOT EDIT DIRECTLY. This file is:
- Gitignored (won't interfere with git operations)
- Overwritten on each `FIRST_RUN.sh` run
- Used as a fallback if `~/.beigebox/config` is missing

### `docker/docker-compose.yaml` — REPO CODE
**Defines the stack structure. Don't edit ports here.**

It uses environment variables:
```yaml
ports:
  - "${BEIGEBOX_PORT:-1337}:8000"
```

The `:-1337` is just a **fallback default**. The real value comes from `.env` (which comes from `~/.beigebox/config`).

## Common Tasks

### Change a backend port (Ollama, Whisper, Kokoro)

**Option 1: Edit config directly (fastest)**
```bash
sed -i 's/OLLAMA_PORT=.*/OLLAMA_PORT=11435/' ~/.beigebox/config
./launch.sh down
./launch.sh up -d
```

**Option 2: Re-run setup**
```bash
./FIRST_RUN.sh --reset
# Answer questions, set backend ports as needed
./launch.sh up -d
```

**Note:** BeigeBox web UI is always on port 1337 (fixed).

### Reconfigure everything (profiles, paths, ports)
```bash
./FIRST_RUN.sh --reset
```

This re-runs the interactive wizard. Your old config is overwritten (no backup).

### Check current configuration
```bash
cat ~/.beigebox/config
```

Or just run `./launch.sh` — it prints the config it loaded:
```
[launch.sh] Loaded config from /home/you/.beigebox/config
[launch.sh] Using: BeigeBox=1337, Ollama=11434, Whisper=9000, Kokoro=8880
```

### Share config between machines
Copy `~/.beigebox/config` to another machine:
```bash
scp ~/.beigebox/config other-machine:~/.beigebox/config
ssh other-machine "cd beigebox/docker && ./launch.sh up -d"
```

## Troubleshooting

### "Port 1337 is already in use"
BeigeBox web UI is fixed on port 1337. You'll need to:

```bash
# 1. Stop the other service using port 1337, OR
lsof -i :1337

# 2. Free up the port and restart BeigeBox
./launch.sh down
./launch.sh up -d
```

If you absolutely need a different port for the web UI, you can edit `docker/docker-compose.yaml` directly:
```yaml
beigebox:
  ports:
    - "8080:8000"   # Change 8080 to your desired port
```

### "My port changes keep reverting to 1337"
This means you edited `docker/.env` directly instead of `~/.beigebox/config`.

**Don't edit `docker/.env`** — it's auto-synced from `~/.beigebox/config`.

Instead:
```bash
# Edit the source of truth
nano ~/.beigebox/config

# Re-sync
./FIRST_RUN.sh
# or just re-run launch.sh
./launch.sh down
./launch.sh up -d
```

### "My config was lost after re-running FIRST_RUN.sh"
If you ran `./FIRST_RUN.sh --reset`, it overwrites `~/.beigebox/config` with defaults.

**Prevention:** Just run `./FIRST_RUN.sh` (without `--reset`). It will detect your existing config and offer to keep it.

## Summary

| File | Owned By | Editable? | Purpose |
|------|----------|-----------|---------|
| `~/.beigebox/config` | **You** | ✅ Yes, directly | SOURCE OF TRUTH for your settings |
| `docker/.env` | System | ⚠️ Avoid; auto-synced | Environment variables for docker-compose |
| `docker/docker-compose.yaml` | Repo | ❌ No; don't edit ports | Stack definition; uses env vars as fallback |

**Golden Rule:** If you want to change something, edit `~/.beigebox/config`. That's it.
