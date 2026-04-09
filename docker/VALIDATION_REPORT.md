# Docker Folder Validation Report
**Date**: 2026-04-08  
**Method**: 8 parallel agents spawned through beigebox proxy  
**Model**: Claude Opus (extended reasoning)

---

## Agent 1: Dockerfile Validation

**Status**: WARN

### Analysis of /home/jinx/ai-stack/beigebox/docker/Dockerfile

**Security**
- ✓ PASS: Pinned base image by digest; non-root `appuser` (uid 1000) with `nologin`; no secrets in env.
- ⚠ WARN: Base is `python:` (likely full Debian, not `-slim` or distroless) — large attack surface. Consider `python:*-slim` for minimal base.
- ⚠ WARN: `build-essential` retained in final image — compilers shipped to production increase attack surface. Use multi-stage build (builder installs wheels, runtime copies site-packages).
- ⚠ WARN: `curl` retained at runtime; only needed if healthchecks use it.
- ℹ INFO: No `HEALTHCHECK` directive.
- ℹ INFO: No explicit `USER` before pip install — pip runs as root (expected, but packages land in system site-packages; fine).
- ⚠ **CRITICAL**: Busybox wrapper is a denylist (blocklist) approach — denylists are inherently fragile; any unlisted applet with write/exec capability bypasses the restriction (e.g., `find -exec`, `awk` system(), `sed` w, `vi`, `ed`, `patch`, `tar`, `cpio`, `unzip`, `wget` alt names, `env`, `setsid`, `start-stop-daemon`). Allowlist would be safer. This is the most significant security finding.

**Build Correctness**
- ✓ PASS: COPY sources (`requirements.txt`, `requirements.lock`, `beigebox/`, `docker/config.docker.yaml`, `scripts/`, `hooks/`) are all expected to exist in repo root build context.
- ✓ PASS: `printf` heredoc workaround is valid; redirection `> $WRAPPER` works in shell form.
- ✓ PASS: `CMD` uses exec form.
- ⚠ WARN: `EXPOSE 8000` but CLAUDE.md and typical beigebox run on `1337`. Possible port mismatch — verify compose mapping.

**Layer Efficiency**
- ⚠ WARN: `COPY requirements.txt .` is unused (only `requirements.lock` is installed) — wastes a layer and busts cache on unrelated edits.
- ✓ PASS: Deps copied before app code for cache reuse.
- ⚠ WARN: Separate `RUN mkdir/chown` after COPYs adds a layer; could be folded or use `COPY --chown`.
- ⚠ WARN: `chown -R /app/data` only; other `/app` files owned by root — fine since appuser only writes to `/app/data`, but if runtime tries to write elsewhere (logs, wire.jsonl) it will fail.

**Docker-Specifics**
- ✓ PASS: Exec-form CMD; shell-form RUNs appropriate for chaining.
- ✓ PASS: `apt-get update && install && rm -rf lists` single layer.
- ℹ INFO: No `.dockerignore` check performed here — recommend verifying one exists to avoid context bloat.
- ℹ INFO: `LABEL` directives at end are fine but conventionally placed near top; no functional issue.

---

## Agent 2: docker-compose.yaml Validation

**Status**: WARN

### Analysis of /home/jinx/ai-stack/beigebox/docker/docker-compose.yaml

**YAML Syntax**
- ✓ PASS. Valid structure, consistent indentation, proper anchors/mappings.

**Service Definitions**
- ✓ PASS with notes.
  - All images pinned by digest except: `ollama/ollama:latest` (line 73), `fedirz/faster-whisper-server:latest-cpu` (254), `ghcr.io/remsky/kokoro-fastapi-cpu:latest-arm64` (285), `pytorch/executorch:latest` (423, TODO noted). **Unpinned tags are reproducibility/supply-chain risk**.
  - Port bindings valid; `${VAR:-default}` patterns correct.
  - Volumes declared at bottom match usage.

**Networks & Environment**
- ✓ PASS. Three bridge networks (llm/tools/inference) with sensible segmentation. `beigebox` spans all three as the hub. Env vars use safe `${VAR:-default}` fallbacks. 
- ⚠ Minor: All networks set `internal: false` — comment says segmentation limits blast radius, but none are `internal: true`, so all have outbound internet. Intentional for Anthropic API on claude-bot, but ollama/whisper/kokoro could be `internal: true` for stricter isolation.

**Security**
- ⚠ WARN.
  - `beigebox`: `cap_drop: ALL` + `no-new-privileges` good, but `read_only` disabled (commented — logging reason given). No explicit `user:` directive — relies on Dockerfile USER.
  - `ollama`: `read_only: true` + tmpfs good. No `cap_drop`, no `no-new-privileges`, no `user:`. Runs privileged-ish with GPU.
  - `chrome`: `cap_add: SYS_ADMIN` + `--no-sandbox` — expected for headless Chromium but high blast radius; no `cap_drop: ALL` baseline, no `no-new-privileges`.
  - `whisper`, `kokoro` (x86): no `read_only`, no `cap_drop`, no `no-new-privileges`. Apple variants correctly set `read_only: true` — **inconsistency**.
  - `llama-cpp`, `vllm`, `executorch`: `read_only: true` but missing `cap_drop`/`no-new-privileges`.
  - `ollama-model-pull`, `claude-bot`: no hardening. `claude-bot` mounts project root rw (`..:/workspace`) with internet + API key — expected for sandbox but notable.
  - **Only `beigebox` has full hardening triad**. Inconsistent baseline across services.

**Dependency Ordering**
- ⚠ WARN.
  - `ollama-model-pull` → `ollama` (service_started): `service_started` fires before Ollama API is ready; the `sleep 5` + curl is a workaround. Should use `condition: service_healthy` since ollama has a healthcheck defined.
  - `beigebox` → `ollama` (service_started): same issue — beigebox may start before ollama API ready. Prefer `service_healthy`.
  - `claude-bot` → `beigebox` (service_started): beigebox has a healthcheck; `service_healthy` would be more correct.
  - No dependency from `beigebox` on `ollama-model-pull`, so first requests may hit a model-less Ollama.

**Overall**: Functional and well-structured, but tighten image pinning (4 floating tags), unify security hardening across services, and switch `depends_on` to `condition: service_healthy` where healthchecks already exist.

---

## Agent 3: config.docker.yaml Validation

**Status**: PASS (with minor warnings)

### Analysis of /home/jinx/ai-stack/beigebox/docker/config.docker.yaml

**YAML Syntax**
- ✓ Valid. Indentation and structure are consistent.

**Required Keys**
- ✓ All present — backend, server, embedding, storage, tools, decision_llm, operator, hooks, advanced, logging, wiretap, auto_summarization, system_context, wasm, voice.

**Service URLs**
- ✓ Correct container hostnames — `ollama:11434` (embedding + backend + decision_llm), `whisper:8000`, `kokoro:8880`. `host.docker.internal:9009` for browserbox relay is intentional (host-side).

**Models/Timeouts**
- ✓ Reasonable. backend timeout 120s, decision_llm 15s (matches memory note), operator 300s for cold loads, stream_stall 120s, wasm 5000ms. All models (`qwen3:4b`, `llama3.2:3b`, `nomic-embed-text`) consistent across sections.

**Feature Flags / Dependencies**
- ✓ decision_llm.enabled=true and routes all reference `qwen3:4b` (matches backend.default_model). Consistent.
- ⚠ WARN: wasm.enabled=true with pdf_oxide enabled; opener_strip is referenced by 3 decision_llm routes (default/large/fast) but `wasm.modules.opener_strip.enabled: false`. **Routes specify `wasm_module: opener_strip` but the module is disabled — those routes will silently skip the transform.**
- ✓ voice.enabled=false but URLs configured — fine.
- ✓ tools.browserbox points to host.docker.internal — requires `extra_hosts` mapping in docker-compose; verify it exists.
- ✓ operator.shell.enabled=true with `/usr/local/bin/bb` wrapper — ensure that binary exists in the container image.

**Key Issue**: Enable `wasm.modules.opener_strip` or remove `wasm_module: opener_strip` from the decision_llm routes to keep them consistent.

---

## Agent 4: docker-compose.example.yaml vs actual

**Status**: FAIL — significant drift

### Analysis

**Schema/Structure Drift**
- ⚠ Example uses `image: ollama/ollama@sha256:...` (pinned); actual uses `:latest`. Example pins all images by digest; actual mixes pinned digests (whisper, kokoro, chrome, llama-cpp, vllm) with `:latest` (ollama, ollama-model-pull, executorch, whisper-apple, kokoro-apple).
- ❌ **Example beigebox port maps `1337:8001`; actual maps `1337:8000`. Health check ports also differ (8001 vs 8000). Breaking inconsistency between the two files.**
- ❌ **Example beigebox volumes are minimal (config.yaml, runtime_config.yaml, named volumes); actual mounts many host paths (data, hooks, 2600/skills, workspace/out, workspace/mounts, beigebox/web, wasm_modules, logs, tmpfs in/tmp). Example does not represent actual mount surface.**
- ⚠ Example uses single `llm` network; actual uses three networks (`llm`, `tools`, `inference`) with documented segmentation.
- ⚠ Example beigebox has `cap_add: NET_BIND_SERVICE` and `read_only: true`; actual drops read_only and adds `security_opt: no-new-privileges`.
- ⚠ Example ollama-model-pull pulls `qwen3:4b` + `nomic-embed-text`; actual pulls `gemma3:4b` + `nomic-embed-text` and pins it in VRAM via keep_alive:-1.
- ⚠ Actual beigebox has build arg `REQUIRE_HASHES`, `extra_hosts: host.docker.internal`, env vars (GOOGLE_API_KEY, GOOGLE_CSE_ID, BROWSERBOX_WS_URL, LOG_LEVEL), and `logging:` driver config — none in example.
- ⚠ Chrome service: example uses `ghcr.io/browserless/chrome:latest`, port `9222:9222`, env DEBUG=`browserless:*`; actual uses `browserless/chrome@sha256:...`, port `9222:3000`, different env vars, `cap_add: SYS_ADMIN`, `chrome_data` volume.
- ⚠ Whisper/Kokoro: example uses `openai/whisper-api:latest` and `hexgrad/kokoro:latest` (likely nonexistent/wrong); actual uses `fedirz/faster-whisper-server` and `ghcr.io/remsky/kokoro-fastapi-cpu` pinned by digest with proper env vars and volumes.

**Breaking Changes vs Example**
- ❌ Yes — port mismatch (1337→8001 vs 1337→8000), wrong image names for whisper/kokoro/chrome in example, missing required mounts and env vars.

**Documented Services in Example**
- ❌ Example does not document or define:
  - `whisper-apple`, `kokoro-apple` (apple profile)
  - `executorch` (engines-executorch profile)
  - `claude-bot` (sandbox profile)
  - Listed in actual SERVICES header and profiles but absent from example entirely.

**Profile Parity**
- ❌ Example profiles: `voice`, `cdp`, `engines-cpp`, `engines-vllm` (declared in header but only `voice` and `cdp` actually attached to services; `engines-cpp` and `engines-vllm` services are not even defined).
- ❌ Actual profiles: `voice`, `apple`, `cdp`, `engines-cpp`, `engines-vllm`, `engines-executorch`, `sandbox`.
- ❌ Missing in example: `apple`, `engines-executorch`, `sandbox`, plus the `engines-cpp`/`engines-vllm` service bodies themselves.

**Overall Verdict**: FAIL. The example file is stale and not representative of the current schema. It is missing 4 services, 3 profiles, 2 networks, multiple volumes, and contains incorrect image names and a port mismatch that would prevent a working `cp example → up` quickstart.

---

## Agent 5: env.example Validation

**Status**: PASS (with minor WARN)

### Analysis of /home/jinx/ai-stack/beigebox/docker/env.example

**Compose Vars Documented**
- ✓ BEIGEBOX_PORT, OLLAMA_PORT, WHISPER_PORT, KOKORO_PORT, LLAMA_CPP_PORT, VLLM_PORT, EXECUTORCH_PORT, OLLAMA_DATA, OLLAMA_HOST, GOOGLE_API_KEY, GOOGLE_CSE_ID, BROWSERBOX_WS_URL, WHISPER_MODEL, LLAMA_CPP_MODEL_PATH/CONTEXT/GPU_LAYERS, VLLM_MODEL/MAX_MODEL_LEN/GPU_MEM/TRUST_REMOTE_CODE, EXECUTORCH_MODEL_PATH/DTYPE, ANTHROPIC_API_KEY, REQUIRE_HASHES — all present.

**Defaults Match Compose**
- ✓ Ports: 1337, 11434, 9000, 8880, 8001, 8002, 8003 — match.
- ✓ OLLAMA_HOST=ollama — matches.
- ✓ WHISPER_MODEL default `faster-whisper-base` matches voice profile; note apple profile defaults to `faster-whisper-small` in compose but env.example only documents base (minor WARN — not wrong, just undocumented apple default).
- ⚠ VLLM_TRUST_REMOTE_CODE: env.example says `true`, compose default is `false` (WARN — **inconsistent default**).

**Comments**
- ✓ Each section has header + explanation. Good.

**REQUIRE_HASHES**
- ✓ Documented with ~15 lines covering true/false tradeoffs, recommendations for prod/dev/CI. Solid.

**Secrets**
- ✓ ANTHROPIC_API_KEY, GOOGLE_API_KEY, GOOGLE_CSE_ID, BB_GOOGLE_CLIENT_SECRET, BB_SESSION_SECRET all empty/commented. No leaked secrets.

**Issues**
- ⚠ WARN: VLLM_TRUST_REMOTE_CODE default mismatch (env.example `true` vs compose fallback `false`). Security-relevant — should default to `false`.
- ⚠ WARN: Apple-profile whisper model default (`faster-whisper-small`) not mentioned in env.example comments.
- ℹ Minor: No mention of `LOG_LEVEL` or `PYTHONUNBUFFERED` (hardcoded in compose, not user-tunable — acceptable).

---

## Agent 6: launch.sh & runSmokeTests.sh

**Status**: PASS (with minor warnings)

### launch.sh Analysis

**Syntax**
- ✓ Valid. `set -euo pipefail`, proper `[[ ]]`, quoted expansions, arrays handled correctly.

**Commands Referenced**
- ✓ `docker`, `docker compose`, `uname`, `grep`, `sed`, `dirname`. All standard.

**Error Handling**
- ✓ `set -euo pipefail`; `pin_image_digest` degrades gracefully on missing digest.

**Comments**
- ✓ Header block plus per-section inline comments — adequate.

**Secrets/Unsafe**
- ✓ None. No `eval`, no unsanitized redirects.

**WARN Items**
- ⚠ Line 45: `sed -i ''` is BSD/macOS syntax. On Linux (this host) GNU sed will interpret `''` as a filename and fail. Since the digest-pinning path only runs under the `apple` profile (line 87), Linux users typically won't hit it, but it is non-portable despite the script claiming to be platform-aware.
- ⚠ Stale USAGE/log tags reference `up.sh` instead of `launch.sh` (cosmetic; file was renamed in commit 0655d0a6).
- ⚠ Line 30 logic: the `grep -q "@sha256:" && grep -q "$image"` check returns "already pinned" if any line in the file has a digest AND any line mentions the image — not necessarily the same line. Could skip pinning incorrectly once one image is pinned.

### runSmokeTests.sh Analysis

**Syntax**
- ✓ Valid. Heredocs, `[[ ]]`, command substitutions, arrays all well-formed.

**Commands**
- ✓ `docker compose`, `curl`, `python3`, `grep`, `head`, `sleep`, `echo`. Standard.

**Error Handling**
- ✓ `set -euo pipefail`; per-check pass/fail counters; explicit early exit if container never goes healthy; final exit code reflects FAIL count.

**Comments**
- ✓ Every section has a numbered header banner explaining purpose. Excellent.

**Secrets/Unsafe**
- ✓ None. No `eval`. No credentials. All curl targets are localhost. `docker compose exec` inputs are static strings, not user input.

**WARN Items**
- ⚠ Section 5 (line 70): logic is inverted relative to comment intent — it passes when status is anything except 404, and fails on 404. Comment says "should forward to backend", which is correct, but the `_ok` message "(got HTTP $STATUS)" reads oddly when STATUS could itself be an error code (e.g. 502) and still be counted as success.
- ⚠ `set -e` interacts with the `cmd && _ok || _fail` idiom: if `_ok` ever returned non-zero (it won't, since `PASS=$((...))` returns 0), the `_fail` branch would fire. Currently safe but fragile.
- ⚠ Line 364: `[[ "$FAIL" -eq 0 ]] && echo ... && exit 0 || exit 1` — if any `echo` failed, would exit 1 spuriously. Negligible risk.
- ⚠ Shebang is `#!/usr/bin/bash` (vs `#!/bin/bash` in launch.sh). Works on most Linux distros but not all (e.g. some minimal images only have `/bin/bash`).

---

## Agent 7: config.yaml ↔ config.docker.yaml Alignment

**Status**: WARN (bordering on FAIL)

### Analysis

**Top-Level Section Parity**
- ❌ Root has: features, models, harness, classifier, semantic_cache, routing, zcommands, guardrails, amf_mesh, cost_tracking, conversation_replay, web_ui, plugins, connections, payload_log, workspace, local_models
- ❌ Docker is missing all of these. Docker is a thin override (intended), but lacks a `features:` block entirely, so feature flags do not align.

**Conflicting Values**
- ⚠ `server.port`: root 8001 vs docker 8000
- ⚠ `backend.default_model`: root `gemma3:4b` vs docker `qwen3:4b`
- ✓ `backend.url`: root `http://${OLLAMA_HOST:-localhost}:11434` vs docker `http://ollama:11434` (correct for Docker)
- ❌ `decision_llm.model`: root uses `models.profiles.routing` (gemma3:4b); docker hardcodes legacy `decision_llm.model: llama3.2:3b` — **schema drift** (root uses Phase 2 `models.profiles`, docker still uses pre-refactor keys).
- ⚠ `operator.model`: root gemma3:4b vs docker qwen3:4b
- ⚠ `auto_summarization.token_budget`: root 24000 vs docker 3000 (large drift)
- ⚠ `logging.level`: root INFO vs docker DEBUG
- ✓ `logging.file`: root `./logs/beigebox.log` vs docker `/app/logs/beigebox.log` (correct for container)
- ⚠ `wasm.enabled`: root false vs docker true (conflict — but docker has no `features.wasm` to gate it)
- ❌ `storage` keys differ: root uses `path`/`vector_store_path`; docker uses legacy `sqlite_path`/`chroma_path` — **schema drift**, may not load correctly depending on loader.
- ❌ `embedding`: root uses `backend: ollama`; docker uses `backend_url:` — **different key names, schema drift**.
- ❌ `tools.registry` (root, list-form) vs `tools.web_search/web_scraper/...` (docker, dict-form) — **completely different schemas**.
- ✓ `operator.shell.allowed_commands`: root `[]` (allow-all); docker has explicit allowlist (docker is safer).
- ✓ `operator.shell.blocked_patterns`: root blocks `^rm `, docker blocks `rm` (docker stricter).

**Bare-Metal Defaults (Root)**
- ✓ PASS — uses `${OLLAMA_HOST:-localhost}`, relative paths, sensible.

**Docker Service Names**
- ✓ PASS — `ollama:11434`, `whisper:8000`, `kokoro:8880`, `host.docker.internal` for browserbox relay, `/app/...` paths. All correct.

**Feature Flag Alignment**
- ❌ FAIL — docker has no `features:` block at all. Subsystems are toggled only via per-section `enabled:` keys, so master switches in root (`features.harness`, `features.classifier`, `features.tools`, `features.cost_tracking`, etc.) have no equivalent in docker. If the loader requires `features.*` to activate subsystems, harness/classifier/cost_tracking/etc. silently disabled in Docker.

**Overall**: WARN bordering on FAIL. Key concerns:
- Docker config uses pre-refactor schema (`storage.sqlite_path`, `embedding.backend_url`, `tools.web_search.enabled`, `decision_llm.model`) while root uses post-Phase-2 schema (`storage.path`, `models.profiles.*`, `tools.registry`).
- No `features:` block in Docker means Phase 1 master switches are absent.
- Default models differ across every role (gemma3:4b vs qwen3:4b) — intentional? worth confirming.
- `wasm.enabled: true` in Docker without `features.wasm` gate.

---

## Agent 8: claude.sh & Dockerfile.claude

**Status**: PASS (minor notes) / WARN

### claude.sh Analysis

**Bash Syntax**
- ✓ Valid. `set -euo pipefail`, proper quoting, `${1:-}` guards against unset.

**Shebang**
- ✓ `#!/usr/bin/env bash` correct. File is not marked executable in the listing, but `bash launch.sh` invocations and `./claude.sh` usage in comments imply chmod +x is expected — would need `chmod +x` if not already set.

**Docker Compose Commands**
- ✓ Valid: `docker compose --profile sandbox ps/up/build`, `docker compose exec -w -u SERVICE CMD` — all standard v2 syntax.

**Profile Selection**
- ✓ Consistently uses `--profile sandbox` for ps/build/up. Note: `docker compose exec` (lines 33, 36) does not need `--profile` (exec targets a running container by name), so omission is correct. Logic is sound.

**Comments**
- ✓ Header documents purpose, usage modes, volume mount, network endpoint, and required env var. Inline `[claude.sh]` echo prefixes aid runtime tracing.

**Minor Warnings**
- ⚠ The running-check `grep -q "$SERVICE"` against `ps --status running` could false-positive if another container name contains `claude-bot` as substring; safer would be `ps -q` + name filter, but acceptable here.
- ⚠ `--build` consumes `$1` via `shift`, but a subsequent `--shell` after `--build` would work; documented usage doesn't combine them, fine.
- ✓ No malware indicators: it's a thin wrapper around docker compose. No exfiltration, no obfuscation, no suspicious network calls.

### Dockerfile.claude Analysis

**Status**: WARN

**Base Image**
- ✓ debian:bookworm-slim is reasonable for a Claude Code sidecar (needs glibc for the native binary). PASS.

**Security vs Main Dockerfile**
- ⚠ WARN. Several hardening differences vs typical main app container:
  - **Passwordless `NOPASSWD:ALL` sudo for `jinx` — full root escalation inside container.**
  - **Installs `docker.io` (Docker CLI). If `/var/run/docker.sock` is bind-mounted at runtime, jinx effectively has host root via the Docker socket. This bypasses any dropped caps / non-root posture.**
  - No `--cap-drop`, `no-new-privileges`, read-only FS, or USER hardening declared here (those would be in compose, but worth confirming).
  - Claude binary copied to `/usr/local/bin` world-readable — fine, but installer was run as root via `curl | bash` (supply-chain risk; pinned to upstream).

**Dependencies for Claude Code**
- ✓ git, curl, bash, python3, build-essential, jq, ripgrep, fd-find, openssh-client present. PASS. 
- ⚠ Minor: `fd-find` installs as `fdfind` on Debian (no `fd` symlink) — Claude tooling expecting `fd` won't find it. 
- ⚠ Node.js is NOT installed; the native installer doesn't need it, so OK, but any MCP servers requiring `node`/`npx` will fail.

**Mount Points / Env Vars**
- ⚠ WARN — relies entirely on compose to do the right thing.
  - WORKDIR `/workspace` exists but no VOLUME declared, and no ENV (e.g., `CLAUDE_CONFIG_DIR`, `PATH` additions, `HOME` is implicit). 
  - `/home/jinx/.claude` is created for config persistence but not declared as a volume — must be handled in compose. 
  - No `ANTHROPIC_API_KEY`/auth env wiring.

**Breaking Diffs from Main Dockerfile Assumptions**
- The main beigebox image is Python/uvicorn-based and runs as a constrained service user; this sidecar is a privileged shell environment. They are intentionally different, but:
  - UID/GID of `jinx` is whatever adduser picks (likely 1000); if main container or host bind mounts expect a specific UID, file ownership may mismatch. WARN.
  - `CMD ["sleep","infinity"]` means no healthcheck — orchestrator can't tell if it's broken.

**Recommendation**: Verify compose does not bind `/var/run/docker.sock` into this container, and pin the `jinx` UID if sharing volumes with the main app.

---

## Summary Table

| File | Status | Key Issues |
|---|---|---|
| Dockerfile | WARN | Denylist fragile, build-essential in prod, unused copy layer |
| docker-compose.yaml | WARN | 4 unpinned images, uneven hardening, service_started not healthy |
| config.docker.yaml | PASS | opener_strip module disabled but referenced |
| docker-compose.example.yaml | **FAIL** | Stale, port mismatch, wrong images, missing 4 services & 3 profiles |
| env.example | PASS | VLLM_TRUST_REMOTE_CODE default mismatch |
| launch.sh | PASS | sed -i '' non-portable, stale comments |
| runSmokeTests.sh | PASS | Inverted logic in section 5, non-portable shebang |
| config.yaml ↔ config.docker.yaml | **WARN** | Schema drift, missing features block, pre-refactor vs post-Phase-2 |
| claude.sh | PASS | Clean, valid compose commands |
| Dockerfile.claude | WARN | Passwordless sudo + docker.io = host root risk if socket mounted |

