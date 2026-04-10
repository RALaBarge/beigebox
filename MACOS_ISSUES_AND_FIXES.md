# macOS Issues & Fixes — Detailed Breakdown

## 🔴 Critical Issues (Must Fix Before Running)

### 1. Ollama Cannot Run in Docker on macOS
**Root Cause**: Docker Desktop's Linux VM can't pass Metal GPU; caps RAM at ~7GB  
**Impact**: Can't load models >7B params (qwen3:30b needs 18GB)  
**Solution**: Run Ollama natively on host via `brew install ollama`  
**Files Changed**:
- `docker/docker-compose.yaml` — removed in-container ollama service
- `docker/config.docker.yaml` — changed backend URL to `http://host.docker.internal:11434`

**User Action**:
```bash
brew install ollama
brew services start ollama
ollama pull qwen3:30b-a3b qwen3:4b nomic-embed-text
```

---

### 2. requirements.lock Has Wrong Platform Hashes
**Root Cause**: Lock file was compiled on Linux x86_64; pip tries to install x86_64 wheels on ARM64 macOS  
**Impact**: `docker compose build` fails with hash mismatch on first run  
**Symptom**: 
```
ERROR: hashes do not match for numpy: got sha256:ABCD... expected sha256:DCBA...
```
**Solution**: Either:
- Option A: Disable hash checking (quick, for testing)
  ```bash
  echo "REQUIRE_HASHES=false" >> docker/.env
  ```
- Option B: Regenerate lock for ARM64 (proper, for production)
  ```bash
  cd docker
  python -m pip install uv
  uv pip compile --generate-hashes -o requirements.lock ../pyproject.toml
  ```

**Files Changed**: None yet (requires user action)  
**Recommendation**: Use Option A for rapid testing, Option B for production

---

### 3. Missing .dockerignore Breaks Builds
**Root Cause**: Docker build context uploads entire repo (2-3GB) instead of excluding non-essential files  
**Impact**: 
- Slow build (minutes instead of seconds)
- May exceed Docker Desktop's 2GB VM memory → silent timeout/failure
- Risk of uploading sensitive logs/backups

**Solution**: Create `.dockerignore` at repo root with standard exclusions

**Files Changed**: Create new `.dockerignore`

**Content**:
```
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
node_modules/
```

---

## 🟡 Warnings (Work, But Suboptimal)

### 4. Backend URLs Point to host.docker.internal (macOS Only)
**Root Cause**: macOS architecture requires host-bridge networking  
**Impact**: URLs won't work on Linux (host.docker.internal doesn't exist)  
**Workaround**: Use conditional config or separate yaml files per platform  
**Files Changed**: 
- `docker/config.docker.yaml` — all backend URLs use `host.docker.internal`

**Fix Options**:
- Use separate `docker/config.docker.macos.yaml` (current approach)
- Or implement platform detection in `launch.sh`
- Or add platform markers to a single config file

---

### 5. BrowserBox on host.docker.internal (for Salesforce Tools)
**Root Cause**: New tools (SF Ingest, Aura Recon) require BrowserBox running externally  
**Impact**: Salesforce tools won't work unless user sets up BrowserBox separately  
**Workaround**: Document that BrowserBox must be running on host

**User Action**:
```bash
docker run -p 9009:3000 browserless/chrome:latest &
```

**Files Changed**:
- `docker/config.docker.yaml` — `aura_recon.ws_url`, `sf_ingest.ws_url` point to `host.docker.internal:9009`

---

### 6. OLLAMA_DATA Path is Linux-Shaped
**Root Cause**: `env.example` hardcodes `/home/youruser/.ollama` (Linux path)  
**Impact**: macOS users confused by non-existent path; silent fallback to named volume  
**Workaround**: Use named volume (default) or document `/Users/youruser/.ollama`

**Files Changed**: `docker/env.example` (minor path documentation)

---

### 7. Git Submodules Removed
**Root Cause**: Submodule checksums differ on macOS (CRLF/LF line endings)  
**Impact**: `git submodule update` would fail on macOS checkout  
**Solution**: Deleted `.gitmodules` entirely  
**Files Changed**:
- `.gitmodules` — deleted
- `2600/` — now tracked as regular files (no submodule)
- `beigebox/tools/registry.py` — updated imports (tools now local)

**Benefit**: Portable across platforms, no platform-specific git state

---

## ✅ Changes That Work on Both Platforms

### 8. New Feature Tools Added
**Tools**:
- `sf_ingest.py` — Salesforce list-view crawler + case fetcher
- `aura_recon.py` — Salesforce Aura descriptor sniffing
- `atlassian.py` — Jira + Confluence REST API client
- `_bb_client.py` — BrowserBox WebSocket helper

**Impact**: None on existing code; all gated by `enabled: true/false` in config

**Files Changed**:
- `beigebox/tools/sf_ingest.py` — new, 600 LOC
- `beigebox/tools/aura_recon.py` — new, 360 LOC
- `beigebox/tools/atlassian.py` — new, 400 LOC
- `beigebox/tools/_bb_client.py` — new, 95 LOC
- `beigebox/tools/registry.py` — updated to register tools

**Usage**: Operator can call `{"tool": "sf_ingest", "method": "discover_native"}` etc.

---

### 9. Ingest Scripts Added
**Scripts**:
- `scripts/sf_ingest_run.py` — CLI for bulk Salesforce case ingestion
- `scripts/jira_ingest_run.py` — CLI for bulk Jira ticket ingestion

**Impact**: Optional standalone tools; don't affect server startup

**Files Changed**:
- `scripts/sf_ingest_run.py` — new, 255 LOC
- `scripts/jira_ingest_run.py` — new, 460 LOC

---

### 10. Web UI Improvements
**Changes**:
- Tap UI improvements (log filtering, display)
- Wiretap streaming fixes
- CLI help text updates (more role filters)

**Impact**: UX improvements only; backward compatible

**Files Changed**:
- `beigebox/web/index.html` — UI updates
- `beigebox/wiretap.py` — streaming fixes
- `beigebox/cli.py` — help text updates
- `beigebox/metrics.py`, `beigebox/bench.py` — logging/display tweaks

---

### 11. Documentation Updates
**Changes**:
- Architecture docs updated with new tools
- Routing docs clarified
- Configuration docs expanded

**Impact**: Info only; no code changes

**Files Changed**:
- `d0cs/architecture.md`
- `d0cs/routing.md`
- `d0cs/configuration.md`
- `d0cs/cli.md`

---

### 12. Customer-Specific References Removed
**Changes**:
- Hard-coded "kantata", "mavenlink" org names removed
- Moved to config-driven approach
- Gitignore genericized

**Impact**: Cleaner, more portable codebase

**Files Changed**:
- `beigebox/tools/sf_ingest.py` — `_INTERNAL_ORG_NAMES` now config-driven
- `.gitignore` — generic comments + `ingest_to_chroma.py` excluded
- `beigebox/tools/registry.py` — passes config to SfIngestTool

---

## Summary Table

| Issue | Severity | Type | Fix | User Action? |
|-------|----------|------|-----|--------------|
| Ollama in-container fails | 🔴 Critical | Architecture | Move to host | Yes: `brew install ollama` |
| requirements.lock hash mismatch | 🔴 Critical | Build | Disable hashes or regenerate | Yes: set env var |
| Missing .dockerignore | 🔴 Critical | Build | Create file | Developer: add file |
| Backend URLs use host.docker.internal | 🟡 Warning | Config | Platform-conditional config | No: works as-is |
| BrowserBox must run on host | 🟡 Warning | Infra | Document requirement | Yes: run Docker container |
| OLLAMA_DATA path is Linux | 🟡 Warning | Config | Document macOS path | No: named volume fallback |
| Git submodules broke macOS | 🟡 Warning | Git | Remove submodules | No: already fixed |
| New tools (SF, Aura, etc.) | ✅ Feature | Code | Register in registry | No: optional, config-driven |
| Ingest scripts | ✅ Feature | Code | Standalone tools | No: optional |
| Web UI improvements | ✅ Feature | UI | Better Tap display | No: backward compatible |
| Docs updated | ✅ Feature | Docs | Architecture clarity | No: informational |
| Customer refs removed | ✅ Cleanup | Code | Config-driven approach | No: cleaner code |

---

## Recommended Merge Order

1. **First (must fix)**: 
   - New feature tools (SF Ingest, Aura Recon, Atlassian, BrowserBox client)
   - Ingest scripts
   - Registry updates
   - Remove submodules / gitignore cleanup

2. **Second (infrastructure)**: 
   - Create `.dockerignore`
   - Document REQUIRE_HASHES=false workaround
   - Separate docker-compose/config files (macOS vs Linux)

3. **Third (nice-to-have)**: 
   - Web UI improvements
   - Documentation updates
   - Validation reports

---

## Platform-Specific Checklist

### Running on macOS
- [ ] `brew install ollama && brew services start ollama`
- [ ] `ollama pull qwen3:30b-a3b qwen3:4b nomic-embed-text`
- [ ] `echo "REQUIRE_HASHES=false" >> docker/.env` (if building Docker image)
- [ ] `.dockerignore` exists at repo root
- [ ] `docker compose up -d beigebox`
- [ ] (Optional) `docker run -p 9009:3000 browserless/chrome:latest` (for Salesforce tools)

### Running on Linux
- [ ] `docker compose up -d ollama` (starts in-container service)
- [ ] `docker compose up -d beigebox`
- [ ] `.dockerignore` exists (optional but recommended)
- [ ] All other setup same as main branch

---

## Files to Archive (.old)

For easy back-and-forth testing:
- `docker/docker-compose.linux.old.yaml` — Linux in-container ollama version
- `docker/config.docker.linux.old.yaml` — Linux config version

Copy these when switching between macOS and Linux testing.

