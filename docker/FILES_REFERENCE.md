# Docker Folder Files Reference

Quick guide to what each file does.

---

## Setup & Launch (User-Facing)

### `launch.sh` — Docker Launcher + Setup Wizard ⭐
**Purpose:** Starts the Docker stack, with automatic setup on first run  
**When to use:** Every time you want to start/stop BeigeBox (setup runs automatically first time)  
**What it does:**
1. **First run:** Interactive setup wizard
   - Auto-detects your platform (macOS/Linux, ARM64/x86_64)
   - Asks 2 questions: features (voice, CDP, etc.) and model storage location
   - Creates `~/.beigebox/config` (persistent, in user home)
   - Updates `docker/.env` with your choices
   - Then launches the stack
2. **Later runs:** Reads `~/.beigebox/config` and launches the stack directly

**Key features:**
- One-command setup: no separate FIRST_RUN.sh needed
- Platform-aware (auto-detects Apple Silicon, swaps voice → apple profile on ARM64)
- Preserves existing config (won't overwrite unless you use `--reset`)

**Use it:**
```bash
./launch.sh up -d              # First run: setup wizard, then start. Later: just start.
./launch.sh down               # Stop
./launch.sh --reset up -d      # Re-run setup wizard, then restart
./launch.sh --profile cdp up -d # Temp override (doesn't save)
```

---

## Configuration

### `config.docker.yaml` — BeigeBox App Config ⭐
**Purpose:** All BeigeBox settings (models, features, tools, backends)  
**Location:** Inside the container (`/app/config.yaml`)  
**What's in it:**
- **Models:** default, routing, agentic, summary (all locked to llama3.2:3b)
- **Features:** decision_llm, operator, harness, semantic_cache, etc. (toggle on/off)
- **Backends:** Ollama, OpenRouter, multi-backend routing config
- **Tools:** web_search, web_scraper, calculator, cdp, etc.
- **Operator:** max_iterations, tool_profiles, shell config
- **Timeouts:** All in one place

**How to change:**
- **Persistent:** Edit `data/runtime_config.yaml` (hot-reload, survives restarts)
- **Temporary:** Use `/api/v1/config` API call
- **One-time:** `./FIRST_RUN.sh --reset`

---

### `env.example` — Environment Template
**Purpose:** Template for `.env` file  
**What it shows:**
```bash
BEIGEBOX_PORT=1337
OLLAMA_PORT=11434
OLLAMA_DATA=/path/to/models
API_KEYS=...
```

**When to use:** Never. FIRST_RUN.sh creates `.env` from this automatically.

---

### `.env` — Runtime Environment Variables
**Purpose:** Docker Compose variables (ports, API keys, paths)  
**Who creates it:** FIRST_RUN.sh (auto-populated from your answers)  
**What's in it:**
```bash
BEIGEBOX_PORT=1337
OLLAMA_PORT=11434
OLLAMA_DATA=/Users/you/.ollama
OPENROUTER_API_KEY=sk-or-v1-...
```

**Important:** Gitignored (won't appear in git). Each machine has its own.

---

### `config.yaml` — Root-Level Config (Old/Deprecated)
**Status:** ⚠️ Shouldn't be here in docker/  
**Note:** Should be in root directory, not docker/  
**What's in it:** Same as `config.docker.yaml`  
**Why it's here:** Legacy, from old setup method

**Action:** Can be deleted from docker/ folder

---

## Stack Definition

### `docker-compose.yaml` — Service Definitions ⭐
**Purpose:** Defines all Docker services and how they connect  
**What it defines:**
- **beigebox** — The proxy/middleware (port 1337)
- **ollama** — LLM inference (commented out, runs on host instead)
- **postgres** — Vector database (port 5432)
- **whisper** — Speech-to-text (optional, profile: voice)
- **kokoro** — Text-to-speech (optional, profile: voice)
- **chrome** — Browser automation (optional, profile: cdp)
- **vllm/llama-cpp/executorch** — Alt inference engines (optional)

**Profiles:**
- `voice` — Speech I/O (Whisper + Kokoro)
- `apple` — macOS native (arm64) versions
- `cdp` — Browser automation
- `engines-cpp`, `engines-vllm` — Alternative inference
- `sandbox` — Claude Code sidecar

**When to modify:** Rarely. Only if adding new services or changing ports permanently.

---

## Build

### `Dockerfile` — BeigeBox Container Image
**Purpose:** Builds the main BeigeBox container  
**What it does:**
1. Base image: Python 3.12
2. Installs dependencies from `requirements.lock`
3. Copies beigebox code
4. Exposes port 8000 (internally)

**When to rebuild:** After code changes
```bash
./launch.sh up -d --build
```

---

### `Dockerfile.claude` — Claude Code Sidecar
**Purpose:** Optional sandbox container for Claude Code with BeigeBox access  
**What it has:**
- Claude Code CLI
- Full workspace mount
- Access to BeigeBox API (http://beigebox:8000)

**When to use:** If you want Claude Code running in a container alongside BeigeBox

**Launch:** `./launch.sh --profile sandbox up -d`

---

### `requirements.lock` — Pinned Dependencies (298KB!)
**Purpose:** Exact versions of all Python packages  
**Format:** pip-compatible lock file  
**Why it's big:** Includes transitive dependencies (requests, httpx, pydantic, etc.)

**Important:** This is generated, not edited manually. If you add new deps:
```bash
pip install <new-package>
pip freeze > requirements.lock
```

---

## Testing

### `runSmokeTests.sh` — Smoke Tests (19KB)
**Purpose:** Quick sanity checks that the stack is working  
**What it tests:**
- BeigeBox health check (curl /health)
- Model availability (curl /v1/models)
- Chat endpoint (curl /v1/chat/completions)
- Both streaming and non-streaming

**When to run:**
```bash
./runSmokeTests.sh
```

**Use case:** After deploy, verify everything works before going live

---

## Documentation

### `README.md` — User Guide ⭐
**Purpose:** Quick start, profiles, troubleshooting  
**Covers:**
- How to run FIRST_RUN.sh
- How to use launch.sh
- What each file does
- Profile options (voice, CDP, etc.)
- Common errors and fixes

**Audience:** Users deploying BeigeBox locally

---

### `CONFIGURATION.md` — Configuration Architecture
**Purpose:** Deep dive into how configuration works  
**Covers:**
- Single source of truth (`~/.beigebox/config`)
- Hot-reload system (`data/runtime_config.yaml`)
- Config loading order
- How to customize ports/profiles/features

**Audience:** Users who want to understand the system

---

### `DRIFT_FIX_SUMMARY.md` — Port Configuration Fix Details
**Purpose:** Explains the port 1337 issue and how it was fixed  
**Covers:**
- What the problem was (settings kept changing)
- Why it happened (multiple sources of truth)
- How it was fixed (FIRST_RUN.sh preservation)
- Before/after comparison

**Audience:** Technical users wanting to understand the fix

---

## Old/Deprecated (Can Delete from docker/)

### `SETUP_PROPOSAL.md` — Design Doc
**Status:** ❌ Old design document  
**Note:** Archived to `d0cs/SETUP_PROPOSAL.md`  
**Action:** Can be deleted from docker/

---

### `VALIDATION_REPORT.md` — Old Test Output
**Status:** ❌ Stale test results  
**Note:** Archived to `d0cs/VALIDATION_REPORT.md`  
**Action:** Can be deleted from docker/

---

## Utility

### `claude.sh` — Claude Code Launcher
**Purpose:** Launch Claude Code in the sandbox sidecar  
**Usage:**
```bash
./claude.sh              # Drop into shell
./claude.sh --build      # Rebuild image first
```

**When to use:** If running the sandbox profile  
**Note:** Requires `./launch.sh --profile sandbox up -d` first

---

## Archive

### `.setup/FIRST_RUN.sh` — Legacy Setup Wizard
**Status:** Archived (functionality moved into `launch.sh`)  
**Location:** `docker/.setup/FIRST_RUN.sh` (hidden by convention)  
**When to use:** Never (use `./launch.sh` instead)  
**Why it's here:** Preserved for reference and manual recovery (not recommended)

---

## Summary Table

| File | Type | User-Facing? | Edit? | Delete? |
|------|------|---|---|---|
| launch.sh | Script | ✅ Yes | ❌ No | ❌ No |
| config.docker.yaml | Config | ⚠️ Via API | ⚠️ Rarely | ❌ No |
| docker-compose.yaml | Config | ❌ No | ❌ No | ❌ No |
| Dockerfile | Build | ❌ No | ❌ No | ❌ No |
| requirements.lock | Build | ❌ No | ❌ No | ❌ No |
| env.example | Template | ❌ No | ❌ No | ✅ Maybe |
| README.md | Docs | ✅ Yes | ⚠️ Yes | ❌ No |
| runSmokeTests.sh | Test | ✅ Maybe | ❌ No | ❌ No |
| claude.sh | Utility | ⚠️ Optional | ❌ No | ✅ If no sandbox |

---

## Quick Decision Tree

**"I want to..."**

- **Start/stop BeigeBox** → Use `./launch.sh up -d` / `./launch.sh down`
- **Set up for first time** → Just run `./launch.sh up -d` (wizard runs automatically)
- **Reconfigure setup** → Run `./launch.sh --reset up -d`
- **Change models** → Edit `data/runtime_config.yaml` (hot-reload) or `config.docker.yaml` (restart)
- **Add voice/CDP** → Use `./launch.sh --reset up -d` or manually edit `config.docker.yaml`
- **Test if it's working** → Run `runSmokeTests.sh`
- **Understand how it works** → Read `README.md` or `CONFIGURATION.md`
- **Launch Claude Code** → Run `./claude.sh` (requires sandbox profile)
