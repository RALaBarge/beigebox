# Documentation Index

Complete reference for BeigeBox deployment, configuration, and usage.

## Getting Started

1. **[Deployment](deployment.md)** — Install and run BeigeBox (Docker Compose, Kubernetes, Systemd)
2. **[Quick Start](deployment.md#quick-start)** — 5-minute setup
3. **[Architecture](architecture.md)** — How it works under the hood

## Configuration & Customization

- **[Configuration](configuration.md)** — `config.yaml`, `runtime_config.yaml`, feature flags, per-model options
- **[Routing & Backends](routing.md)** — Per-model provider selection, latency-aware ordering, retry-with-backoff
- **[CLI](cli.md)** — Command-line tools (`bb sweep`, `bb tap`, `bb ring`, etc.)
- **[Authentication](authentication.md)** — API keys, multi-key setup, admin gate, ACLs

## Development & Integration

- **[API Reference](api-reference.md)** — HTTP endpoints, request/response formats, examples
- **[Agents & Tools](agents.md)** — How external MCP clients drive BeigeBox tools; Council / Ensemble / Wiggam / Ralph features
- **[Tools & Integrations](tools.md)** — Chrome DevTools Protocol (CDP), plugins, MCP server, document search

## Operations & Monitoring

- **[Observability](observability.md)** — Tap event log, metrics, debugging
- **[Observability Coverage](observability-coverage.md)** — definitive map of what's emitted, what's a gap, and the rubric for adding new events
- **[Security](security.md)** — Supply chain hardening, hash locking, network isolation, Docker hardening, threat model

## Skills

Importable async pipelines under `beigebox/skills/` — each is a self-contained directory with `pipeline.py`, a CLI, and a `SKILL.md`.

- **`fuzz`** ([SKILL.md](../beigebox/skills/fuzz/SKILL.md)) — pure-Python coverage-blind mutation fuzzer; risk-scored discovery, adaptive time budget, package-aware harness loader, garlicpress-shape findings
- **`static`** ([SKILL.md](../beigebox/skills/static/SKILL.md)) — ruff + semgrep + mypy, concurrent subprocess runners, per-runner failure isolation, garlicpress-shape findings
- **`fanout`** ([SKILL.md](../beigebox/skills/fanout/SKILL.md)) — fan a list of items out to N parallel OpenAI-compat calls + optional reduce; solves the "reasoning model blew its budget on a 13-file prompt" failure mode
- **`host-audit`** ([SKILL.md](../beigebox/skills/host-audit/SKILL.md)) — single-host audit of running containers/VMs and listening services
- **`services-inventory`** ([SKILL.md](../beigebox/skills/services-inventory/SKILL.md)) — same audit fleet-wide via SSH

### Portfolio

- **[fuzz + static six-repo validation](portfolio/fuzz-static-validation.md)** — methodology, results table, notable findings, what the validation proved

---

## By Use Case

### "I want to run BeigeBox locally"
→ [Deployment: Quick Start](deployment.md#quick-start)

### "I want to deploy to production"
→ [Deployment](deployment.md) → Choose your method (Docker Compose, Kubernetes, Systemd)

### "I want to use multiple models or backends"
→ [Routing & Backends](routing.md)

### "I want to understand how requests flow through the system"
→ [Architecture](architecture.md)

### "I want to add authentication and rate limiting"
→ [Authentication](authentication.md)

### "I want to integrate with my own code"
→ [API Reference](api-reference.md)

### "I want to use the operator (browser automation, RAG, etc.)"
→ [Agents & Tools](agents.md)

### "I want to harden BeigeBox for production"
→ [Security](security.md)

### "I want to debug a problem"
→ [Observability](observability.md)

---

## Files Referenced

### Core Configuration

- `config.yaml` — Static startup config (backends, models, features)
- `runtime_config.yaml` — Hot-reload config (defaults, toggles)
- `docker/docker-compose.yaml` — Docker deployment
- `docker/.env` — Environment variables (GPU, ports, API keys)
- `docker/MS_APM_beigebox.yaml` — Microsoft APM manifest (legacy; Operator was deleted in v3)

### Deployment

- `deploy/docker/` — Docker Compose setup
- `deploy/k8s/` — Kubernetes manifests
- `deploy/systemd/` — Systemd unit files
- `docker/Dockerfile` — Container image build
- `docker/compose-switch.sh` — Dev ↔ Prod switcher

### Source Code

- `beigebox/main.py` — FastAPI app, all endpoints
- `beigebox/proxy.py` — Request pipeline (routing, caching, transforms)
- `beigebox/config.py` — Config loader
- `beigebox/backends/router.py` — Multi-backend routing engine
- `beigebox/cache.py` — Semantic + embedding caching
- `beigebox/web/index.html` — Web UI (no build step)
- `beigebox/agents/` — Multi-LLM features (council, ensemble_voter, wiggam_planner, ralph_orchestrator, skill_loader)
- `beigebox/storage/` — SQLite + Postgres+pgvector storage (`storage/backends/{base,postgres,memory}.py` factory)
- `CLAUDE.md` — Development guidelines

### Observability & Tools

- `scripts/install-hooks.sh` — Install git hooks (supply chain automation)
- `requirements.lock` — Pinned Python dependencies (hash-locked)
- `docker-compose.yaml` — Network segmentation, Docker hardening
- `Tap` — Unified event logging (queried via `/api/v1/logs/events`)

---

## Common Workflows

### Benchmark model speed

```bash
beigebox bench --model llama3.1:8b --num-runs 5
# Or via web UI: Lab tab → Bench sub-tab
```

See [CLI & Z-Commands](cli.md#benchmarking).

### Check system metrics

```bash
curl http://localhost:1337/api/v1/system-metrics
```

See [API Reference](api-reference.md#health--status).

### Query the event log

```bash
curl "http://localhost:1337/api/v1/logs/events?limit=50&filter=route"
```

See [Observability](observability.md).

### Switch between dev and prod

```bash
cd docker
./compose-switch.sh prod
docker compose up -d
```

See [Deployment: Dev vs Prod](deployment.md#dev-vs-prod).

### Update dependencies

```bash
# Edit requirements.txt, then:
uv pip compile requirements.txt --generate-hashes --output-file requirements.lock
# (auto-runs on git commit via pre-commit hook)
```

See [Security: Hash Locking](security.md#python-dependencies--hash-locked).

### Scan for CVEs

```bash
pip-audit -r requirements.lock
# (auto-runs on git push via pre-push hook)
```

See [Security: CVE Scanning](security.md#python-dependencies--hash-locked).

---

## Key Concepts

| Term | Definition |
|---|---|
| **Backend** | Inference provider (Ollama, OpenRouter, OpenAI-compat, etc.) |
| **MultiBackendRouter** | Picks a backend for the request's `model` field; latency-aware |
| **Normalizer seam** | Request + response normalizers translate any backend to OpenAI shape |
| **MCP** | Model Context Protocol — how external agent clients drive BeigeBox tools (`/mcp`, `/pen-mcp`) |
| **Council / Ensemble / Wiggam / Ralph** | The four multi-LLM features that survive in v3 (see [Agents](agents.md)) |
| **Semantic cache** | Cache keyed by embedding similarity (not exact text match) |
| **Tap / Wiretap** | Unified event log (dual-write SQLite + JSONL); query via `bb tap` |
| **Window config** | Per-pane request overrides (`_window_config` in request body) |
| **Plugin** | Auto-loaded Python tool (drop in `plugins/`) |
| **Hook** | Event-driven custom code (shell or Python) — `HookManager` runs them |
| **Memory** | Postgres+pgvector vector store, exposed via `bb sweep` and the `memory` MCP tool |

## Defense-in-depth (Security strategy)

BeigeBox assumes supply chain compromise is inevitable, not a rare edge case.

**Three layers:**
1. **Prevention** — Hash-locked dependencies, pinned images, CVE scanning
2. **Containment** — Read-only root filesystem, network segmentation, capability drop
3. **Detection** — Tap logging, metrics, git hooks

Attack outcome: **Trapped in-memory, detected in 0.1s, cannot persist or escalate.**

See [Security](security.md) for full threat model and how each layer works.

---

## Quick Links

- **GitHub**: [ralabarge/beigebox](https://github.com/ralabarge/beigebox)
- **Issues**: [GitHub Issues](https://github.com/ralabarge/beigebox/issues)
- **Main README**: [../README.md](../README.md)
- **License**: [../LICENSE.md](../LICENSE.md) (AGPL-3.0 + Commercial)

---

## For Contributors

- Development setup: [../CLAUDE.md](../CLAUDE.md)
- Testing: `pytest`
- Code style: Black + flake8 (if configured)
- Commit hooks: `sh scripts/install-hooks.sh`

---

**Last updated:** 2026-03-25
**Version:** 1.9
