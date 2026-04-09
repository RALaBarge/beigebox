# BeigeBox Docker Setup Wizard — Design Proposal

## Problem Statement
Users pulling the repo fresh on macOS hit multiple discoverability issues:
1. **Model mismatch** — docker-compose pulls `gemma3:4b`, but config expects `qwen3:4b` → "All backends failed"
2. **OLLAMA_DATA confusion** — Placeholder `/home/youruser/.ollama` doesn't auto-expand, leads to "mounts denied"
3. **Profile selection** — No guidance on which profiles to enable (voice, cdp, engines, sandbox)
4. **Buried settings** — Config spread across docker-compose.yaml, config.docker.yaml, .env, launch.sh with no single source of truth

## Solution: FIRST_RUN.sh + ~/.beigebox/config

### Architecture

**Interactive Setup Wizard** (`docker/FIRST_RUN.sh`):
1. Auto-detects platform (macOS vs Linux, arm64 vs x86_64)
2. Menu-driven profile selection:
   - Recommends `apple` for macOS ARM64
   - Shows `voice`, `cdp`, `engines-vllm`, `engines-cpp`, `sandbox`
   - Warns about conflicts (can't use `voice` + `apple` together)
3. OLLAMA_DATA path selection:
   - Default: `/Users/$(whoami)/.ollama` (macOS) or `/home/$(whoami)/.ollama` (Linux)
   - Allows custom path input
4. Writes persistent config to `~/.beigebox/config`:
   ```bash
   PLATFORM=Darwin
   ARCH=arm64
   PROFILES=apple,cdp
   OLLAMA_DATA=/Users/ryan/.ollama
   OLLAMA_PORT=11434
   BEIGEBOX_PORT=1337
   ```
5. Updates `docker/.env` with OLLAMA_DATA and REQUIRE_HASHES=false (dev default)

**Updated launch.sh**:
1. Sources `~/.beigebox/config` on startup
2. Automatically applies saved profiles via `--profile` flags
3. Doesn't require manual args after FIRST_RUN
4. Falls back gracefully if config missing (backward-compatible)

### Benefits
- **One-time setup** — Users run FIRST_RUN once, then `./launch.sh` just works
- **Platform-aware** — Auto-recommends profiles based on arch
- **Persistent** — Choices saved in user home dir, not in repo
- **Discoverable** — Interactive menus show all options with descriptions
- **Debuggable** — Config file is plain-text, easy to edit/troubleshoot
- **Predictable** — Settings live in one place (`~/.beigebox/config`), not scattered

### File Structure (unchanged)
```
docker/
  ├── FIRST_RUN.sh          ← NEW: Interactive setup
  ├── launch.sh             ← UPDATED: Sources ~/.beigebox/config
  ├── docker-compose.yaml
  ├── config.docker.yaml
  ├── env.example
  ├── Dockerfile
  └── Dockerfile.claude
```

### User Flow

**Fresh clone:**
```bash
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox/docker
./FIRST_RUN.sh                    # Interactive menu, saves config
./launch.sh up -d                 # Just works, auto-applies saved profiles
# Open http://localhost:1337
```

**Existing users:**
- No change to launch.sh behavior (auto-detects config, falls back if missing)
- Can opt-in by running FIRST_RUN.sh
- Or continue with manual `--profile` flags

### Model Pull Alignment
Future: Read model names from `config.docker.yaml` instead of hardcoding in compose.
Currently: Both files updated manually, but FIRST_RUN establishes the pattern for centralized config.

### Open Questions
1. Should FIRST_RUN ask about Docker resource limits (memory, CPU)?
2. Should it validate Docker daemon is running before proceeding?
3. Should it offer to enable/disable features (operator, harness, cost_tracking)?
4. Should profiles survive config edits, or re-run FIRST_RUN to change?

## Next Steps
1. Merge branch `docker-first-run-wizard` to main
2. Update README with "Quick Start" → "Run `docker/FIRST_RUN.sh`"
3. (Future) Consolidate model names across compose + config files
