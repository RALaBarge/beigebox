# macOS Setup Changes — Quick Reference

## Problem
Docker Desktop on macOS can't pass Metal GPU or provide enough unified memory (~7GB limit) to run large inference models (18GB+ needed).

---

## Solution: Move Ollama to Host

### ✅ What You Need to Do on macOS

1. **Install Ollama natively**
   ```bash
   brew install ollama
   brew services start ollama
   ```

2. **Pre-pull models once**
   ```bash
   ollama pull qwen3:30b-a3b      # ~18GB (main inference model)
   ollama pull qwen3:4b           # ~3GB (fast routing model)
   ollama pull nomic-embed-text   # ~274MB (embeddings)
   ```

3. **Verify it's accessible**
   ```bash
   curl http://localhost:11434/api/tags
   ```

4. **(Optional) BrowserBox for Salesforce tools**
   ```bash
   docker run -p 9009:3000 browserless/chrome:latest
   ```

---

## Infrastructure Changes Made

### Docker Compose (`docker/docker-compose.yaml`)
- **Removed** in-container `ollama` service
- **Removed** `ollama-model-pull` initialization service
- **Kept** beigebox, CDP, supporting services
- **Added** `extra_hosts: host.docker.internal:host-gateway` for container→host routing

### Config File (`docker/config.docker.yaml`)
- Changed backend URL: `http://ollama:11434` → `http://host.docker.internal:11434`
- Changed BrowserBox URL: `ws://localhost:9009` → `ws://host.docker.internal:9009`
- Updated Aura Recon, SF Ingest, Atlassian tool endpoints to use `host.docker.internal`

### Requirements Lock (`docker/requirements.lock`)
- ⚠️ Generated on Linux x86_64, but macOS needs ARM64 wheels
- **Workaround**: Disable hash checking before build
  ```bash
  echo "REQUIRE_HASHES=false" >> docker/.env
  ```
- **Or regenerate** for your platform (takes ~5min)

### Missing .dockerignore
- ❌ Not created yet (causes slow Docker builds)
- Should exclude: `data/`, `workspace/`, `.git/`, logs, test artifacts
- **Impact**: 2-3GB build context uploaded → slow/stuck builds

---

## Feature Tools Added (Cross-Platform ✅)

All of these work on Linux AND macOS, just need BrowserBox:

1. **SF Ingest** (`beigebox/tools/sf_ingest.py`)
   - Crawl Salesforce list-views, discover cases, extract fields
   - Outputs to markdown for RAG embedding
   - Uses Aura framework via BrowserBox

2. **Aura Recon** (`beigebox/tools/aura_recon.py`)
   - Sniff live Salesforce XHR traffic
   - Extract working Aura action descriptors
   - Save/replay known descriptors

3. **Atlassian Tool** (`beigebox/tools/atlassian.py`)
   - Query Jira + Confluence REST APIs
   - Credentials from env vars (portable)

4. **BrowserBox Client** (`beigebox/tools/_bb_client.py`)
   - Helper for SF Ingest + Aura Recon
   - Talks to BrowserBox WebSocket endpoint
   - Strips Salesforce anti-hijack prefixes

5. **Ingest Scripts** (`scripts/sf_ingest_run.py`, `scripts/jira_ingest_run.py`)
   - CLI tools for one-shot doc ingestion
   - Output to workspace/out/rag/ for ChromaDB

---

## Files Changed (Infrastructure Only)

```
docker/docker-compose.yaml      ← Ollama removed, host routing added
docker/config.docker.yaml       ← Backend URLs changed to host.docker.internal
.gitignore                       ← Minor additions
```

## Files Added (Features + macOS Docs)

```
beigebox/tools/sf_ingest.py              ← 600 LOC Salesforce ingestion
beigebox/tools/aura_recon.py             ← 360 LOC Aura descriptor sniffing
beigebox/tools/atlassian.py              ← 400 LOC Jira/Confluence REST
beigebox/tools/_bb_client.py             ← 95 LOC BrowserBox helper
beigebox/tools/registry.py               ← Updated to register new tools
scripts/sf_ingest_run.py                 ← 255 LOC CLI ingest
scripts/jira_ingest_run.py               ← 460 LOC CLI ingest
MACOS_VALIDATION_REPORT.md               ← 150+ lines of findings
docker/config.docker.yaml                ← Updated config with BrowserBox URLs
2600/skills/tone-ryan/SKILL.md           ← New skill
beigebox/web/index.html                  ← UI updates
beigebox/wiretap.py, cli.py, etc.        ← Various improvements
```

---

## Testing Back & Forth

### Running macOS Config
```bash
git checkout macos
# Make sure Ollama is running:
brew services start ollama

# Build + run
docker compose --profile default up -d beigebox
curl http://localhost:1337/health
```

### Running Linux Config (on Linux machine)
```bash
git checkout main
# Ollama runs in container, no host setup needed

docker compose up -d ollama beigebox
curl http://localhost:1337/health
```

### Backing Out macOS Changes (to test Linux config on macOS)
```bash
# Use the .old files:
cp docker/docker-compose.linux.old.yaml docker/docker-compose.yaml
cp docker/config.docker.linux.old.yaml docker/config.docker.yaml

# But you'll need to uncomment the ollama service in docker-compose.yaml
# and run:
docker compose up -d ollama beigebox  # This will fail if you don't have 7GB RAM free
```

---

## Why This Was Necessary

| Constraint | Docker on macOS | Native Host Ollama |
|---|---|---|
| Metal GPU access | ❌ VM can't pass-through Metal | ✅ Direct access |
| RAM available | 7GB max (VM limit) | 16GB+ (system RAM) |
| Model load time | Slower (Rosetta emulation) | Native ARM64 |
| Power efficiency | Battery drain (VM overhead) | Better (less VM) |

---

## Architecture Diagram

```
Linux (main branch):
┌─────────────────────────────────┐
│     Docker Desktop (or Engine)   │
│  ┌──────────────────────────────┐│
│  │  beigebox container  ┌──────┐││
│  │  ↓                   │      │││
│  │  http://ollama:11434 │      │││
│  │  ↓                   │      │││
│  │  ┌─────────────────┐ │      │││
│  │  │ ollama service  │ │      │││
│  │  │ (in container)  │ │      │││
│  │  └─────────────────┘ │      │││
│  └──────────────────────┘      ││
└─────────────────────────────────┘

macOS (macos branch):
┌──────────────────────────────────┐
│     Docker Desktop (macOS)        │
│  ┌────────────────────────────┐  │
│  │  beigebox container        │  │
│  │  ↓                         │  │
│  │  http://host.docker.internal:11434
│  │  ↓                         │  │
│  └────────────────────────────┘  │
│           ↓ (host.docker.internal bridge)
│  ┌────────────────────────────┐  │
│  │ Native Ollama on macOS host│  │
│  │ (brew services start)      │  │
│  │ ✓ Metal GPU access         │  │
│  │ ✓ 16GB+ unified memory     │  │
│  │ ✓ ARM64 native             │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

---

## Files Saved as .old for Reference

- `docker/docker-compose.linux.old.yaml` — Original Linux in-container Ollama setup
- `docker/config.docker.linux.old.yaml` — Original Linux config

Use these if you need to understand the "before" state or revert to Linux testing.

