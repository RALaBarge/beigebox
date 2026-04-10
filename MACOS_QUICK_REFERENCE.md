# macOS Setup — Things We Had to Do

## Host Setup (One-Time)

```bash
# 1. Install Ollama
brew install ollama
brew services start ollama

# 2. Download models (one-time, ~22GB total)
ollama pull qwen3:30b-a3b      # 18GB - primary inference
ollama pull qwen3:4b           # 3GB - fast routing
ollama pull nomic-embed-text   # 274MB - embeddings

# 3. Verify it's running
curl http://localhost:11434/api/tags

# 4. (Optional) BrowserBox for Salesforce tools
docker run -p 9009:3000 browserless/chrome:latest &
```

---

## Docker Build Setup

### Before First Build
```bash
# 1. Disable hash checking (macOS has different wheels than Linux)
echo "REQUIRE_HASHES=false" >> docker/.env

# 2. Create .dockerignore to speed up builds
cat > .dockerignore << 'EOF'
data/
workspace/
.git/
.gitmodules
2600/
logs/
oss/
*.md
*.txt
*.jsonl
*.log
__pycache__/
.pytest_cache/
.venv/
.vscode/
.idea/
.env
.env.local
*.pyc
*.pyo
*.egg-info/
dist/
build/
.DS_Store
EOF
```

---

## Infrastructure Changes

| Change | File | Old Value | New Value | Why? |
|--------|------|-----------|-----------|------|
| Backend URL | `docker/config.docker.yaml` | `http://ollama:11434` | `http://host.docker.internal:11434` | Docker can't reach container service from host; use bridge |
| Ollama Service | `docker/docker-compose.yaml` | In-container service | Removed (commented out) | Host Ollama can access Metal GPU + 16GB RAM |
| BrowserBox URL | `docker/config.docker.yaml` | N/A | `ws://host.docker.internal:9009` | For Salesforce tools (Aura Recon, SF Ingest) |
| Model Pull | `docker/docker-compose.yaml` | `ollama-model-pull` service | Removed (commented out) | Models now pulled by user via `ollama pull` |

---

## Files to Keep for Testing

### For Quick Switching Between macOS & Linux

```bash
# Saved: Linux baseline configs (in-container Ollama)
docker/docker-compose.linux.old.yaml
docker/config.docker.linux.old.yaml

# Current: macOS configs (host-native Ollama)
docker/docker-compose.yaml
docker/config.docker.yaml

# To test Linux config on macOS machine:
cp docker/docker-compose.linux.old.yaml docker/docker-compose.yaml
cp docker/config.docker.linux.old.yaml docker/config.docker.yaml
# (Then uncomment ollama service in docker-compose.yaml)
# (But you'll need ~7GB RAM available in Docker)

# To go back to macOS config:
git checkout docker/docker-compose.yaml docker/config.docker.yaml
```

---

## New Features (All Cross-Platform)

| Tool | Purpose | Config Key | Enabled? |
|------|---------|-----------|----------|
| **SF Ingest** | Crawl Salesforce list-views, discover cases | `tools.sf_ingest.enabled` | Yes (in docker/config.docker.yaml) |
| **Aura Recon** | Sniff Salesforce Aura XHR traffic | `tools.aura_recon.enabled` | Yes |
| **Atlassian** | Query Jira + Confluence REST APIs | `tools.atlassian.enabled` | Yes |
| **BrowserBox Client** | Helper for above tools | N/A (internal) | Always (if tools enabled) |
| **SF Ingest CLI** | Bulk Salesforce case ingestion script | `scripts/sf_ingest_run.py` | Standalone |
| **Jira Ingest CLI** | Bulk Jira ingestion script | `scripts/jira_ingest_run.py` | Standalone |

---

## Critical Checks Before Running

```bash
# 1. Ollama is running and accessible
curl http://localhost:11434/api/tags
# Expected: {"models": [{"name": "qwen3:30b-a3b", ...}, ...]}

# 2. Docker can reach host
docker run --rm curlimages/curl:latest curl http://host.docker.internal:11434/api/tags
# Expected: {"models": [...]}

# 3. .dockerignore exists
ls .dockerignore
# Expected: file exists

# 4. REQUIRE_HASHES is set to false (if building Docker)
grep REQUIRE_HASHES docker/.env
# Expected: REQUIRE_HASHES=false

# 5. (Optional) BrowserBox accessible
curl http://localhost:9009/api/version
# Expected: {"version": "..."}
```

---

## Common Issues & Quick Fixes

| Problem | Symptom | Fix |
|---------|---------|-----|
| Ollama not running | `curl: (7) Failed to connect` | `brew services restart ollama` |
| Host.docker.internal unreachable | Connection refused from container | Verify `docker run ... curl http://host.docker.internal:...` works |
| Build fails with hash errors | `ERROR: hashes do not match for numpy` | Set `REQUIRE_HASHES=false` in `.env` |
| Docker build hangs/times out | Stuck at "Sending build context" | Check `.dockerignore` exists; delete `data/` and `workspace/` local files |
| BrowserBox not accessible | Aura Recon fails to connect | `docker run -p 9009:3000 browserless/chrome:latest` |
| Models not downloaded | `ollama list` shows nothing | Run `ollama pull qwen3:30b-a3b qwen3:4b nomic-embed-text` |

---

## Testing macOS Branch Back & Forth

### macOS Tests
```bash
# Switch to macOS branch
git checkout macos

# Ensure Ollama running
brew services start ollama

# Build & run
docker compose --profile default up -d beigebox

# Test
curl http://localhost:1337/health
```

### Linux Tests (if you have access to Linux machine)
```bash
# Switch to main branch
git checkout main

# Ollama in Docker
docker compose up -d ollama beigebox

# Test
curl http://localhost:1337/health
```

### Rolling Back Infra Changes (to test Linux config on macOS)
```bash
# Copy Linux baseline configs
cp docker/docker-compose.linux.old.yaml docker/docker-compose.yaml
cp docker/config.docker.linux.old.yaml docker/config.docker.yaml

# Uncomment the ollama service in docker-compose.yaml
nano docker/docker-compose.yaml
# Find "# ollama:" and uncomment that section

# Try to run (will probably fail — Docker can't load big models)
docker compose up -d ollama beigebox

# Go back to macOS config
git checkout docker/docker-compose.yaml docker/config.docker.yaml
```

---

## Documentation Files Created

| File | Purpose | Audience |
|------|---------|----------|
| `MACOS_SETUP_GUIDE.md` | Detailed explanation of all changes | You (reference) |
| `MACOS_ISSUES_AND_FIXES.md` | Categorized breakdown (critical/warning/feature) | You (troubleshooting) |
| `MACOS_CHANGES_SUMMARY.md` | Quick summary + architecture diagram | Anyone reviewing the branch |
| `MACOS_QUICK_REFERENCE.md` | This file — checklists & commands | You (quick lookup) |
| `.old files` | Linux baseline configs for comparison | You (testing) |
| `docker/docker-compose.linux.old.yaml` | Original in-container Ollama setup | Reference |
| `docker/config.docker.linux.old.yaml` | Original Linux config | Reference |

---

## TL;DR — 60-Second Setup

```bash
# 1. Install Ollama on macOS
brew install ollama && brew services start ollama

# 2. Pull models (takes ~5-10 min)
ollama pull qwen3:30b-a3b qwen3:4b nomic-embed-text

# 3. Prepare Docker
echo "REQUIRE_HASHES=false" >> docker/.env

# 4. Run BeigeBox
git checkout macos  # You're already here
docker compose up -d beigebox

# 5. Verify
curl http://localhost:1337/health
```

Done! BeigeBox is now running with host-native Ollama, Metal GPU acceleration, and 16GB unified memory.

