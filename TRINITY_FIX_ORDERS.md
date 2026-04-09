# Trinity Fix Orders — Docker Folder Validation

**Date**: 2026-04-08  
**Context**: 8-agent validation identified 3 FAIL + 5 WARN issues. Trinity agents assigned to fix with high-level descriptions.  
**Report Location**: `docker/VALIDATION_REPORT.md` (full details)

---

## Trinity-1: Regenerate docker-compose.example.yaml

**Priority**: P0 (FAIL — breaking change in port mapping)

**Goal**: Make `docker-compose.example.yaml` representative of actual `docker-compose.yaml`

**What to fix**:
1. Update beigebox port mapping: `1337:8001` → `1337:8000`
2. Update beigebox healthcheck port: `8001` → `8000`
3. Replace whisper/kokoro images with correct ones:
   - Old: `openai/whisper-api:latest`, `hexgrad/kokoro:latest` (nonexistent)
   - New: `fedirz/faster-whisper-server:latest-cpu@sha256:...`, `ghcr.io/remsky/kokoro-fastapi-cpu:latest-arm64@sha256:...` (match actual, pinned)
4. Fix chrome image name and port: use `browserless/chrome@sha256:...`, port `9222:3000`, add `cap_add: SYS_ADMIN`
5. Add missing services: `whisper-apple`, `kokoro-apple`, `executorch`, `claude-bot`
6. Add missing profiles: `apple`, `engines-executorch`, `sandbox` attached to correct services
7. Add second network: `tools` and `inference` (not just `llm`)
8. Add all env vars: GOOGLE_API_KEY, GOOGLE_CSE_ID, BROWSERBOX_WS_URL, LOG_LEVEL, PYTHONUNBUFFERED
9. Add beigebox volumes: data, hooks, workspace/out, workspace/mounts, beigebox/web, wasm_modules, logs, tmpfs
10. Add build arg to beigebox: `REQUIRE_HASHES: "true"` and `extra_hosts`
11. Update models in ollama-model-pull to match actual (gemma3:4b, not qwen3:4b)
12. Pin all remaining floating tags (@sha256: digests)

**Reference**: Compare with `/home/jinx/ai-stack/beigebox/docker/docker-compose.yaml` to match current reality

---

## Trinity-2: Align config.docker.yaml to Phase-2 schema + add features block

**Priority**: P0 (FAIL — config loader may skip features)

**Goal**: Migrate `config.docker.yaml` from pre-refactor to post-Phase-2 schema; ensure it activates all subsystems

**What to fix**:
1. Add `features:` block at top (copy from `config.yaml` but keep docker-appropriate values):
   - Enable: backends, decision_llm, classifier, operator, harness, tools, semantic_cache, wasm
   - Disable: amf_mesh, voice (unless needed)
2. Migrate storage keys:
   - Old: `storage.sqlite_path`, `storage.chroma_path`
   - New: `storage.path` (base dir), `storage.vector_store_path`
3. Migrate embedding:
   - Old: `embedding.backend_url: "http://ollama:11434"`
   - New: `embedding.backend: ollama`, add `embedding.backend_url` if loader still needs it
4. Migrate tools schema from dict-form to list-form (or keep dict but ensure loader handles both):
   - Instead of `tools.web_search.enabled: true`, use `tools.registry: [web_search, ...]`
   - Keep per-tool config if needed (web_search.provider, max_results, etc.)
5. Fix decision_llm schema:
   - Old: `decision_llm.model: llama3.2:3b` (hardcoded)
   - New: Reference `models.profiles.routing` (Phase 2 approach) or keep llama3.2:3b if that's the docker choice
6. **Fix opener_strip wasm consistency**:
   - Change `wasm.modules.opener_strip.enabled: false` → `true`
   - OR remove `wasm_module: opener_strip` from decision_llm routes (default/large/fast)
7. Align default models (gemma3:4b vs qwen3:4b decision — confirm with user which is docker default)

**Reference**: Compare with `/home/jinx/ai-stack/beigebox/config.yaml` for Phase-2 schema pattern

---

## Trinity-3: Pin 4 floating image tags in docker-compose.yaml

**Priority**: P1 (supply-chain reproducibility)

**Goal**: Replace `:latest` tags with pinned @sha256: digests

**What to fix**:
1. `ollama/ollama:latest` → `ollama/ollama@sha256:DIGEST` (get current digest from `docker pull ollama/ollama && docker inspect ollama/ollama`)
2. `fedirz/faster-whisper-server:latest-cpu` → pin by digest
3. `ghcr.io/remsky/kokoro-fastapi-cpu:latest-arm64` → pin by digest
4. `pytorch/executorch:latest` → pin by digest

**Reference**: Use `docker images --digests` or `docker pull IMAGE && docker inspect IMAGE` to find current pinned digests

---

## Trinity-4: Security hardening — busybox denylist & multi-stage build

**Priority**: P2 (security posture)

**Goal**: Reduce attack surface in Dockerfile

**What to fix** (choose 1 or both based on scope):
1. **Option A (busybox denylist)**: Replace denylist with allowlist
   - Current: blocklist approach (many bypasses)
   - Proposal: Allowlist only safe applets (`ls`, `cat`, `grep`, `ps`, `df`, `free`, `du`, `uptime`, `uname`, `env`, `printenv`, `whoami`, `id`, `hostname`, `date`)
   - Reference existing allow/blocklist patterns in operator.shell config
2. **Option B (multi-stage build)**: Remove build-essential from prod image
   - Stage 1 (builder): base python, apt-get install build-essential, pip install -r requirements.lock, keep site-packages
   - Stage 2 (runtime): python slim base, COPY --from=builder /usr/local/lib/python3.11/site-packages, add appuser, rest of setup
   - Net result: final image no gcc/make/libc-dev

**Note**: Do not modify Dockerfile.claude (passwordless sudo risk) — that's audit-only

---

## Trinity-5: Audit Dockerfile.claude socket risk

**Priority**: P2 (security audit, no fix — report only)

**Goal**: Verify docker-compose.yaml does NOT bind `/var/run/docker.sock` into claude-bot

**What to check**:
1. Search `docker-compose.yaml` for `docker.sock` volume mount
2. Confirm `claude-bot` service has no `volumes: ... /var/run/docker.sock:...`
3. If socket IS mounted: **FAIL** — risk of host root escalation (passwordless sudo + docker CLI)
4. If socket is NOT mounted: **PASS** — document finding

**Reference**: `/home/jinx/ai-stack/beigebox/docker/Dockerfile.claude` (line 2: ubuntu 22.04 with `docker.io` installed, passwordless sudo)

---

## Summary Table

| Trinity | Fix | Priority | Type | Est. Effort |
|---|---|---|---|---|
| 1 | Regenerate docker-compose.example.yaml | P0 | Schema alignment | High (10+ changes) |
| 2 | Align config.docker.yaml to Phase-2 + features block | P0 | Schema migration | High (7+ keys) |
| 3 | Pin 4 floating image tags | P1 | Supply-chain | Low (4 digests) |
| 4 | Busybox denylist → allowlist OR multi-stage build | P2 | Security | Medium (2 options) |
| 5 | Audit Dockerfile.claude socket risk | P2 | Audit only | Low (grep + confirm) |

---

## Spawn Order

**Recommended parallel groups** (all agents use Opus for complex multi-file reasoning):

1. **Group A (in parallel)**:
   - Trinity-1: docker-compose.example.yaml
   - Trinity-2: config.docker.yaml
   - Trinity-3: Pin image tags

2. **Group B (after Group A)**:
   - Trinity-4: Security hardening
   - Trinity-5: Audit socket risk

This minimizes dependencies and keeps schema-heavy work in parallel.

