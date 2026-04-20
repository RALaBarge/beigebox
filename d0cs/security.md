# Security

BeigeBox is hardened against supply chain attacks, injection vectors, and privilege escalation using **defense-in-depth**: assume compromise, prevent persistence, detect attempts.

## Quick overview

Three layers of defense:

| Layer | Tactic | Tools | Goal |
|---|---|---|---|
| **1. Prevention** | Catch compromises before deployment | Hash-locked deps, pinned images, CVE scanning | Slow down attackers; catch build-time threats |
| **2. Containment** | Trap runtime attacks in isolation | Read-only root, cap drop, network segmentation | Prevent persistence; block lateral movement |
| **3. Detection** | Log and monitor everything | Tap logging, metrics, git hooks | Catch attempts immediately (ms-level detection) |

**Result:** Even if one of 175 packages is compromised, the attack becomes:
- **Cannot persist** (read-only blocks binaries)
- **Cannot escalate** (cap drop blocks privesc)
- **Cannot hide** (logs catch network attempts)
- **Detected in 0.1 seconds** (Tap logs all traffic)

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

### Go coordinator — zero external dependencies

**AMF coordinator (Go) extracts critical libraries to eliminate supply chain risk:**

| Component | Extraction | Lines | Protocols |
|-----------|-----------|-------|-----------|
| **NATS Client** | `internal/nats/client.go` | 350 | CONNECT, SUB, PUB, MSG, PING/PONG |
| **mDNS Resolver** | `internal/mdns/mdns.go` | 300 | RFC 6762-6763 multicast DNS |
| **DNS-SD Client** | `internal/dns/dns.go` | 200 | RFC 1035 (PTR, TXT, SRV queries) |
| **UUID Generator** | `stack/id.go` | 18 | RFC 4122 v4 (stdlib crypto/rand) |

**Threat Model:**

Extracted libraries eliminate supply chain attacks at **three high-risk boundary points:**

1. **Event fabric (NATS)** — Compromised nats-io/nats.go could inject malicious events, intercept messages, or trigger denial-of-service
2. **Service discovery (mDNS/DNS)** — Compromised zeroconf or miekg/dns could advertise fake agents, redirect agent connections, or perform man-in-the-middle
3. **ID generation (UUID)** — Compromised google/uuid (unlikely but possible) could generate predictable IDs, enabling session hijacking

**Benefits:**

- ✅ Isolated to point-in-time snapshot (no auto-updates expose new bugs)
- ✅ Zero transitive dependencies (no supply chain contagion)
- ✅ Uses only Go stdlib crypto (stdlib is security-critical, well-audited)
- ✅ Protocols are RFC standards (stable, unlikely to break)

**Costs:**

- Maintenance of 850 LOC for critical I/O paths
- No automatic bug fixes from upstream (manual porting required)
- Responsibility for security updates in extracted code

**Rationale:** Cost is justified because:
1. Protocols change rarely (NATS stable since 2016, DNS/mDNS since 2008-2013)
2. Supply chain is the top threat for coordinated systems
3. AMF runs on isolated networks (limited exposure)
4. Extracted code is reviewed at extraction time and locked in-tree

**Maintenance:** See [amf/stack/EXTRACTED_SOURCES.md](../../amf/stack/EXTRACTED_SOURCES.md) for:
- Extraction details (which functions, which protocols)
- Security review checkpoints (when to audit extracted code)
- How to port security patches from upstream
- Decision matrix for future extractions

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

### Read-only root filesystem

All containers run with read-only root, forcing compromised code to operate in-memory:

```yaml
# docker-compose.yaml
beigebox:
  read_only: true
  tmpfs:
    - /tmp                # 256 MB, cleared on restart
    - /app/workspace/in   # 512 MB, cleared on restart
  volumes:
    - beigebox_data:/app/data  # persistent, for SQLite + embeddings
```

**What breaks:**
- Write to `/etc`, `/usr`, `/bin`, `/app/beigebox` → `EROFS` (read-only filesystem)
- Drop binaries for persistence → DENIED
- Modify config to auto-load on startup → DENIED
- Install cron jobs for backdoors → DENIED

**What still works:**
- Read all memory → can read active conversation buffers
- Make network requests → can exfiltrate (blocked by egress filters)
- Write to `/tmp` → can drop binaries, but lost on restart
- Write to `/app/data` → persistent, but monitored via logs

**Why it matters:**

Without read-only root:
```
Day 0: Compromised library → writes /etc/cron.d/exfil.sh
Day 1-6: Cron exfils data nightly (undetected)
Day 7: You notice the data breach
```

With read-only root + egress filtering + logging:
```
Day 0: Compromised library → tries to write /etc/cron.d/ → EROFS, fails
Day 0: Tries to connect to attacker.com → egress filter blocks it
Day 0: Attempt logged in Tap: "outbound to 1.2.3.4:443 DENIED"
Day 0: You rotate API keys, restart container, incident resolved
```

**Testing:**

```bash
docker compose up -d
docker compose exec beigebox touch /test.txt
# Error: Read-only file system ✓

docker compose exec beigebox touch /tmp/test.txt
# Works (tmpfs is writable) ✓

docker compose exec beigebox touch /app/data/test.txt
# Works (volume is writable) ✓
```

Every service (ollama, whisper, kokoro, chrome, llama-cpp, vllm, executorch) uses the same pattern — only model caches and `/tmp` are writable, everything else immutable.

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

**Core assumption:** At least one of the 175 Python packages in the dependency tree is either compromised now or will be compromised in the future (supply chain inevitability).

### Defense-in-depth strategy

Rather than "prevent all compromise" (impossible), BeigeBox uses **three layers**:

**Layer 1: Prevention (supply chain)**
- Hash-locked dependencies (pip rejects hash mismatches)
- Pinned Docker images by digest (mutable `:latest` tags replaced)
- CVE scanning (pip-audit on pre-push, blocks suspicious code)
- Result: *Slow down* attackers; catch *detectable* compromises at build time

**Layer 2: Containment (runtime hardening)**
- Read-only root filesystem (blocks persistence, binary injection, rootkits)
- Minimal capabilities (cap_drop: ALL, no escalation)
- Unprivileged user (UID 1000, can't touch host)
- Network segmentation (chrome can't reach ollama, whisper can't reach inference)
- Restricted busybox (no rm, chmod, mount, shell, wget, etc.)
- Result: *Trap* compromised code in-memory; prevent lateral movement

**Layer 3: Detection (logging)**
- Tap comprehensive event logging (all request phases)
- System metrics + latency tracking (detect anomalies)
- Git hooks for supply chain automation (catch regressions)
- Result: *Detect* attempts immediately; catch in logs within 0.1s

### Attack scenarios

**Scenario 1: Compromised PyPI package (httpx, urllib3, etc.)**

```
Attack vector: Malicious code in dependency
Prevention: Hash mismatch caught at build time ✓
If missed: Read-only root blocks persistence, egress filter stops exfil
Detection: Network egress logged, Tap shows unusual request pattern
```

**Scenario 2: Code injection at runtime (WASM, plugin, hook)**

```
Attack vector: Malicious WASM/plugin drops a backdoor
Prevention: Code review, signed plugins (not currently enforced)
If missed: WASM is sandboxed in wasmtime, plugin in unprivileged user
Containment: Read-only root + cap_drop block privesc
Detection: Unusual I/O, network requests caught in logs
```

**Scenario 3: Lateral movement (chrome → ollama)**

```
Attack vector: Browser gets compromised, attacker pivots to inference
Prevention: None (can't prevent browser compromise)
Containment: Network segmentation blocks connection to ollama network
Detection: Attempt logged, firewall drops packet
```

**Scenario 4: In-memory exfiltration (steal conversations)**

```
Attack vector: Compromised library reads memory, opens socket
Prevention: Hash locking caught the compromise ✓
Containment: Read-only prevents persistence, exfil is in-memory only
Detection: Egress filter blocks outbound, Tap logs network attempt
```

### In scope (defended against)

- Supply chain compromise (PyPI, Docker Hub)
- Persistent backdoors (read-only root blocks these)
- Privilege escalation (cap_drop + no-new-privileges)
- Network lateral movement (segmentation)
- Accidental data leaks (logged, auditable)
- Code injection (WASM, plugins, hooks)

### Out of scope (cannot defend)

- Physical server compromise
- Host OS rootkit (beyond container)
- Hypervisor break-out
- Social engineering (credential theft)
- Zero-day in kernel/libc
- Timing side-channels
- Compromised admin credentials

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

## Known Limitations & Open Items

### uvicorn — ANSI escape injection in access logs

All versions of uvicorn are vulnerable to ANSI escape sequence injection in the request access logger. A malicious request path can embed terminal escape codes that execute when logs are viewed in a terminal emulator.

**Mitigation:** Run behind a reverse proxy (nginx/caddy) that sanitizes request lines before they reach uvicorn. For production, use a structured JSON log formatter (`python-json-logger`) to strip ANSI escapes.

### Secrets management

API keys are passed via `.env` files, with `agentauth` providing OS keychain credential management for single-operator deployments. For multi-user or production environments, a dedicated secrets manager (HashiCorp Vault, Docker secrets, SOPS, or cloud KMS) is more appropriate. Deferred until multi-user requirements are clear.

---

## Reporting Security Issues

⚠️ **Do not open a public GitHub issue for security vulnerabilities.**

Open a [GitHub Security Advisory](https://github.com/ralabarge/beigebox/security/advisories/new) or email the maintainer (see profile).

Include:
- Description of the issue
- Steps to reproduce
- Affected versions
- Proposed mitigation
