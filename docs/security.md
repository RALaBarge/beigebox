# Security

BeigeBox is hardened against supply chain attacks, injection vectors, and privilege escalation at every layer.

## Supply Chain Security

### Python dependencies — hash-locked

All 175 Python packages are pinned to exact versions with SHA256 hashes in `requirements.lock` (3,280 hashes total). The Docker build uses `--require-hashes`, so pip rejects any package whose hash doesn't match — a compromised PyPI release cannot silently land.

```bash
# Regenerate after changing requirements.txt
uv pip compile requirements.txt --generate-hashes --output-file requirements.lock
```

**Automated:** The `pre-commit` git hook auto-regenerates the lockfile when `requirements.txt` is staged, and the `pre-push` hook scans for known CVEs using `pip-audit`.

```bash
# Manual CVE scan
pip-audit -r requirements.lock
```

Install hooks for your clone:
```bash
sh scripts/install-hooks.sh
```

### Docker images — pinned by digest

Every Docker image in `docker-compose.yaml` is pinned by SHA256 digest, not tag. Tags are mutable — a `latest` push from a compromised upstream deploys immediately on `docker pull`. Digests are immutable.

**Current pins (as of this build):**
- `python:3.12-slim` → `python@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4`
- `ollama/ollama:latest` → `ollama/ollama@sha256:0ff452f6a4c3c5bb4ab063a1db190b261d5834741a519189ed5301d50e4434d1`
- (All 9 images pinned — see `docker/docker-compose.yaml`)

**To update a digest after a deliberate upgrade:**
```bash
docker pull <image>:<tag>
docker inspect <image>:<tag> --format='{{index .RepoDigests 0}}'
# Copy the result into docker-compose.yaml
```

### Model weights — untrusted

Ollama verifies blob SHA256 internally, but the registry is not hash-pinned at the compose level. Treat pulled models the same as any unsigned binary — only pull from trusted sources.

### Community skills — git commit pinned

Submodules are pinned by commit SHA (immutable), but the upstream repo is trusted on that SHA only. Review before `git submodule update`.

---

## Network Segmentation

BeigeBox uses three isolated Docker networks to contain blast radius if any service is compromised:

```
llm:         beigebox ↔ ollama (inference + embeddings)
tools:       beigebox ↔ whisper, kokoro, chrome (voice + CDP)
inference:   beigebox ↔ llama-cpp, vllm, executorch (alt engines)
```

**Isolation:** Chrome, Whisper, and Kokoro cannot reach Ollama directly. If a browser automation task is compromised, it cannot hit the inference engine or steal model artifacts.

---

## Container Hardening

### BeigeBox service

```yaml
security_opt:
  - no-new-privileges:true   # Prevents privilege escalation via setuid/setgid
cap_drop:
  - ALL                      # Drops all Linux capabilities (NET_BIND_SERVICE not needed — port 8000 > 1024)
```

### Non-root user

```dockerfile
RUN useradd -m -u 1000 -s /usr/sbin/nologin appuser
USER appuser
```

Process runs as unprivileged user (UID 1000). Cannot modify files outside its home directory or execute shell commands via `sh -c`.

### Busybox wrapper

The Dockerfile includes a restricted busybox wrapper that blocks dangerous applets:

```bash
BLOCKED: rm, rmdir, mv, cp, chmod, chown, chroot, ln, mknod, mkfifo, dd, truncate,
         install, mount, umount, su, sudo, kill, reboot, poweroff, halt, modprobe, nc,
         wget, telnet, tftp, ash, bash, ...
```

Even if code execution is achieved, destructive commands and network exfiltration tools are unavailable.

---

## HTTP Security Headers

All responses include security headers to prevent CSRF, clickjacking, MIME sniffing, and data exfiltration:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline';
                        img-src 'self' data: blob:; connect-src 'self';
                        frame-ancestors 'none'
```

**CSP details:**
- `script-src 'unsafe-inline'` — required for the single-file web UI
- No `eval`, no remote scripts
- Images can be data URIs (previews) or blob: (stream buffers)
- Media (audio/previews) can be blob: (temp buffers)
- All network requests limited to same-origin

---

## API Authentication

### API key enforcement

All endpoints require an API key via one of:
- `Authorization: Bearer <key>` (standard)
- `api-key: <key>` (OpenAI style)
- `?api_key=<key>` (query param fallback)

Disabled by default if no keys are configured.

### Multi-key setup

Each key can have:
- **Endpoint ACL** — glob patterns of allowed endpoints
- **Model ACL** — glob patterns of allowed models
- **Rate limit** — per-key requests/minute window

See [Authentication](authentication.md) for setup.

### agentauth keychain

Keys are stored in the OS keychain (via `agentauth` library), not in plaintext in files or env vars.

---

## Code Execution Vectors

### Plugins

Drop a `.py` file in `plugins/` with a `*Tool` class and it auto-registers at startup. **Risk:** A malicious plugin runs arbitrary code on startup.

**Mitigation:** In production Docker, plugins are baked into the image at build time (immutable). In dev mode where codebase is mounted rw, only load plugins you trust.

### Hooks

Custom hooks in `hooks/` execute shell commands on tool events. **Risk:** Shell injection if hook commands are not carefully constructed.

**Mitigation:** Hooks run as the `appuser` unprivileged user, cannot modify system files. Review hook scripts before deployment.

### WASM modules

WASM modules in `wasm_modules/` are executed during request post-processing. **Risk:** WASM can attempt to access memory outside the sandbox.

**Mitigation:** WASM runs in a sandboxed `wasmtime` runtime, isolated from the host OS.

---

## vLLM Model Code Execution

By default, `TRUST_REMOTE_CODE` is **disabled** for vLLM:

```yaml
environment:
  - TRUST_REMOTE_CODE=${VLLM_TRUST_REMOTE_CODE:-false}
```

This means vLLM will not execute custom Python code from HuggingFace model repos. If you need this, explicitly set `VLLM_TRUST_REMOTE_CODE=true`, but only for trusted models.

---

## What's NOT protected

- **Ollama model integrity** — Models are fetched unsigned from public registries. Treat like any unsigned binary.
- **Upstream PyPI compromise** — If the maintainer's account is compromised, a malicious PyPI release can land. Hash locking means you *notice* it (rebuild fails) rather than silently deploying it.
- **GitHub compromise** — If the upstream repo is compromised and a new commit is pushed, submodule pins are useless. Always review code before `git submodule update`.
- **Zero-day CVEs** — `pip-audit` checks OSV/PyPI advisory databases, but vulnerabilities are discovered after exploitation. Run `pip-audit` regularly as part of your release process.

---

## Threat Model

**Assumption:** BeigeBox runs on a trusted network (localhost or internal network) with trusted administrators. If you expose `:1337` to the public internet, API key auth is your only defense.

**In scope:**
- Supply chain compromise (PyPI, Docker Hub)
- Accidental data leaks (unencrypted logs, unscoped API keys)
- Container escape (Linux capabilities, privilege escalation)
- Network lateral movement (chrome → ollama)
- Code injection (WASM, plugins, hooks)

**Out of scope:**
- Physical server compromise
- Host OS rootkit
- Hypervisor break-out
- Social engineering (credentials)

---

## Audit Trail

Every request logs:
- Entry/exit point
- Routing decision (which backend)
- Model + parameters
- Token usage
- Latency
- Errors

See [Observability](observability.md) for querying logs.

---

## Reporting Security Issues

⚠️ **Do not open a public GitHub issue for security vulnerabilities.**

Email security concerns to: **[maintainer email — see CONTRIBUTING.md]**

Include:
- Description of the issue
- Steps to reproduce
- Affected versions
- Proposed mitigation
