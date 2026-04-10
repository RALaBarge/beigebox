# macOS Startup Validation Report

**Date**: 2026-04-08  
**Agent**: Spawned through beigebox proxy (Tap-logged)  
**Scope**: End-to-end macOS Apple Silicon startup validation

> **⚠ SUPERSEDED 2026-04-09:** Ollama no longer runs in the compose stack on macOS.
> It runs natively on the host (`brew install ollama && brew services start ollama`)
> for Metal acceleration and full unified memory access — required to load models
> larger than ~7B at Q4. The `ollama:11434` references below are from the previous
> in-container topology. Current backend URL: `http://host.docker.internal:11434`.
> See `docker/docker-compose.yaml` and `docker/config.docker.yaml` for live config.

---

## Validation Checklist

### 1. Git Submodules — [PASS]
- No `.gitmodules` file
- No `skills/` directory present
- `git submodule status` returns empty
- **Fully removed**, no checksum issues on macOS

### 2. requirements.lock Platform — [FAIL] 🔴

**Issue**: Lock file generated on Linux x86_64 with `uv pip compile --generate-hashes`. Contains 3422 hashes with **zero platform markers** (`sys_platform`, `platform_machine`).

**Impact**: On macOS ARM64, pip will attempt to install x86_64 wheels, causing:
- Hash mismatch (different wheels → different hashes)
- Unresolvable platform mismatches
- Failed build on first run

**Current Workaround**: `env.example` documents regeneration (lines 89–105), but default `REQUIRE_HASHES=true` breaks the flow unless user reads comments.

**Fix Required**: 
- [ ] Flip default to `REQUIRE_HASHES=false` in env.example (dev-friendly)
- [ ] OR ship `requirements.macos.lock` alongside `requirements.lock`
- [ ] OR add platform markers during `uv pip compile` step

### 3. REQUIRE_HASHES Setting — [WARN] 🟡

**Status**: Correctly wired (env.example → Dockerfile ARG → docker-compose.yaml).

**Issue**: Default `REQUIRE_HASHES=true` with x86_64-only lock file means Mac users hit the blocker immediately.

**Documentation**: Present in env.example (lines 89–105) with clear dev/prod guidance, but the **default choice is unsafe for macOS first-run**.

**Fix**: Change default to `REQUIRE_HASHES=false` or regenerate lock with platform markers.

### 4. Dockerfile Linux Assumptions — [PASS]

- Base image `python@sha256:...` is multi-arch manifest — buildx on Apple Silicon automatically pulls linux/arm64 variant
- No `sed -i ''` or other BSD-isms
- Uses `printf` (portable)
- `apt-get`, `bubblewrap`, `busybox-static` all work in-container regardless of host
- No hard-coded host paths

### 5. docker-compose.yaml URLs — [PASS]

- Internal services use container DNS: `ollama:11434` ✓
- `extra_hosts: host.docker.internal:host-gateway` works natively on Docker Desktop for Mac ✓
- Healthchecks use `localhost` inside containers (correct) ✓
- No hard-coded host IPs

### 6. config.docker.yaml Backend URLs — [PASS]

- Backend: `http://ollama:11434` (container hostname) ✓
- Embedding: `backend: ollama` with `backend_url: http://ollama:11434` ✓
- Classifier: points to container DNS ✓
- All Phase-2 schema aligned ✓

### 7. launch.sh Compatibility — [WARN] 🟡

**Good:**
- `set -euo pipefail` correct
- `uname -m` detection of `arm64`/`aarch64` correct
- Digest-pinning path gated to `FINAL_HAS_APPLE=true` (Mac-only)

**Issue**: Line 45 uses `sed -i '' "s|...|...|g"` — BSD/macOS syntax.
- Works on macOS ✓
- Would fail on Linux if apple profile used there ⚠
- Cosmetic: script self-refers as `[up.sh]` in logs but file is `launch.sh` (renamed in commit 0655d0a6)

### 8. env.example Variables — [PASS mostly]

**Complete**: All required vars documented ✓

**Issue**: `OLLAMA_DATA=/home/youruser/.ollama` is Linux-shaped path.
- Mac users must change to `/Users/you/.ollama` or accept a named volume fallback
- **Not a hard blocker** (fallback exists), but confusing for first-time Mac users

### 9. .dockerignore — [FAIL] 🔴

**Status**: **Does not exist at repo root.**

**Impact**: Build context uploads entire repo:
- `data/`, `workspace/`, `.git/`, `2600/`, archives, dumps, test outputs
- Build is very slow (many GB sent to Docker daemon)
- Risk of OOM in Docker Desktop's default 2 GB VM on Mac
- Potential secret leakage if logs/backups accidentally committed

**Required**: Create `.dockerignore` with:
```
data/
workspace/
.git/
.gitmodules
2600/
logs/
*.md
*.txt
*.jsonl
*.log
__pycache__/
.pytest_cache/
.venv/
.vscode/
.idea/
*.pyc
*.pyo
*.egg-info/
dist/
build/
```

### 10. Build Order — [PASS]

- `beigebox` depends_on `ollama` with `service_started` (no circular deadlock) ✓
- Ollama model pull handled out-of-band ✓
- No ordering issues ✓

---

## Summary: Would Fresh macOS Clone Work?

**Short Answer**: **No**, not cleanly. Three blocker categories:

### 🔴 Blocker 1: requirements.lock Hash Mismatch

**Will happen on first `docker compose build`** if `REQUIRE_HASHES=true` (default).

```bash
pip install --require-hashes -r docker/requirements.lock
# ERROR: hashes do not match for numpy: got sha256:ABCD... expected sha256:DCBA...
# (Linux x86_64 wheels != macOS arm64 wheels)
```

**Workaround**: `echo "REQUIRE_HASHES=false" >> docker/.env` (requires reading the docs)

**Fix**: Flip default or regenerate lock with platform markers.

### 🔴 Blocker 2: Missing .dockerignore

**Will cause slow/stuck Docker build** if context is large (several GB).

```bash
Sending build context to Docker daemon  2.5GB  # ← too much
```

May exceed Docker Desktop's 2 GB VM memory, causing a silent failure or timeout.

**Fix**: Add `.dockerignore` immediately.

### 🟡 Blocker 3: Voice Profile Steering (User Confusion)

If a Mac user follows the README QUICK REFERENCE and runs:

```bash
docker compose --profile voice up -d
```

Instead of:

```bash
./launch.sh --profile voice up -d
```

They get x86_64 whisper/kokoro images under Rosetta emulation → sluggish or broken voice I/O.

**Fix**: Update README/env.example QUICK REFERENCE to direct Mac users to `./launch.sh` for voice profile.

---

## Critical Fixes (Priority Order)

| # | Issue | File | Fix | Impact |
|---|---|---|---|---|
| 1 | requirements.lock platform mismatch | `docker/requirements.lock`, `docker/env.example` | Flip `REQUIRE_HASHES=false` default OR regenerate for ARM64 | **Build will fail on first run** |
| 2 | Missing .dockerignore | `.dockerignore` (root) | Create file with standard ignores | **Slow/stuck build, OOM risk** |
| 3 | OLLAMA_DATA path is Linux-shaped | `docker/env.example` | Change default to `/Users/` or document the named volume fallback | **User confusion, silent fallback** |
| 4 | Voice profile not steered to launch.sh | `README.md`, `docker/env.example` | Update QUICK REFERENCE to use `./launch.sh` for voice/profiles | **User gets wrong images** |

---

## Files Affected

```
/home/jinx/ai-stack/beigebox/
  docker/
    Dockerfile            ✓ PASS
    docker-compose.yaml   ✓ PASS (post-trinity-fixes)
    config.docker.yaml    ✓ PASS (post-trinity-fixes)
    env.example           ⚠ WARN (defaults, path)
    launch.sh             ⚠ WARN (log stale names)
  .dockerignore           🔴 MISSING
  requirements.lock       🔴 WRONG PLATFORM
  README.md               ⚠ QUICK REFERENCE unclear on Mac
```

---

## Recommended Action

After trinity fixes are merged:

1. Create `.dockerignore` (1 min)
2. Change `REQUIRE_HASHES=false` default in env.example OR regenerate lock (5 min + optional)
3. Update README QUICK REFERENCE section to direct Mac users to `./launch.sh` (2 min)
4. Document `OLLAMA_DATA` macOS path in env.example (1 min)

**Total**: ~10 minutes of work to unblock clean macOS startup.

