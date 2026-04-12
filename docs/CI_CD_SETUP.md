# CI/CD Setup & Troubleshooting Guide

## Overview

BeigeBox ecosystem uses GitHub Actions for continuous integration and deployment across 10 repositories. This guide explains the CI/CD infrastructure, common issues, and how to debug failures.

## Repository Workflows

### Python Projects (pip/pyproject.toml)

**Repos:** beigebox, bluTruth, embeddings-guardian, beigebox-security, agentauth, garlicpress

**Workflows:**
- `security.yml` - Dependency scanning, code analysis, build verification
  - pip-audit: Checks for known vulnerabilities in dependencies
  - bandit: Static security analysis of Python code
  - semgrep: Pattern-based code security scanning
  - pytest: Unit tests (with --timeout=300s to prevent hangs)
  - Build: Creates wheel distribution and verifies package integrity

**Key Actions:**
- `actions/checkout@v4` - Clone repository (Node 20 compatible)
- `actions/setup-python@v5` - Install Python 3.11
- `returntocorp/semgrep-action@v1` - Run semgrep via action (not pip)
- `actions/upload-artifact@v4` - Store build artifacts

### Node.js/TypeScript Projects

**Repos:** browserbox

**Workflows:**
- `security.yml` - npm audit, semgrep scanning
  - npm ci: Clean install (respects package-lock.json)
  - npm audit: Checks for npm package vulnerabilities
  - semgrep: Pattern-based scanning

**Key Actions:**
- `actions/checkout@v4`
- `actions/setup-node@v4` - Install Node.js 20
- `returntocorp/semgrep-action@v1`

### Rust Projects

**Repos:** pdf-oxide-wasi

**Workflows:**
- `ci.yml` - Cargo build, test, clippy
  - cargo build: Compile project
  - cargo test: Run test suite
  - cargo clippy: Linting (warnings don't fail)

**Key Actions:**
- `actions/checkout@v4`
- `dtolnay/rust-toolchain@stable` - Latest stable Rust

### Docker Build Workflows

**Repos:** beigebox, bluTruth, beigebox-security

**Workflow:** `docker-build.yml`
- Builds Docker image on push/PR
- Pushes to ghcr.io on merged PRs
- Uses buildx for multi-platform builds
- Caches layers for faster builds

**Key Actions:**
- `docker/setup-buildx-action@v3`
- `docker/login-action@v3`
- `docker/build-push-action@v6` (latest stable)

## Common Issues & Fixes

### Issue: "semgrep-core executable not found"

**Cause:** Old versions of semgrep (0.86.3 and earlier) require building from source, which fails in CI.

**Solution:** Use `returntocorp/semgrep-action@v1` instead of `pip install semgrep`.

```yaml
# WRONG
- name: Install dependencies
  run: pip install semgrep

- name: Run semgrep
  run: semgrep --config=p/security-audit beigebox/

# CORRECT
- name: Run semgrep
  uses: returntocorp/semgrep-action@v1
  with:
    config: p/security-audit
```

### Issue: "Node.js 20 is deprecated"

**Cause:** GitHub Actions runners now support Node.js 24. Actions using Node 20 will emit deprecation warnings (Sept 2025) and be removed (Sept 2026).

**Solution:** Use compatible action versions:
- ✓ `actions/checkout@v4` - Node 20 compatible
- ✓ `actions/setup-python@v5` - Node 20 compatible
- ✓ `actions/setup-node@v4` - Node 24 compatible
- ✓ `docker/setup-buildx-action@v3` - Node 24 compatible
- ✓ `docker/build-push-action@v6` - Node 24 compatible
- ✓ `returntocorp/semgrep-action@v1` - Node 24 compatible

**Do NOT use:** `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` - this is a temporary workaround and should be removed.

### Issue: "python -m build failed - no pyproject.toml"

**Cause:** Repo is not a Python package (e.g., Node.js, Rust, frontend).

**Solution:** Don't include the "Build & Verify Distribution" job in non-Python repos.

### Issue: "pytest timed out"

**Cause:** Test hangs without timeout enforcement.

**Solution:** Add `--timeout=300` flag to pytest:

```yaml
- name: Run pytest
  run: |
    pytest tests/ -v --tb=short --timeout=300
```

### Issue: "Docker build fails - ENOENT: no such file"

**Cause:** `./docker/Dockerfile` doesn't exist.

**Solution:** Verify Dockerfile location in repo and update workflow:

```yaml
- name: Build and push
  uses: docker/build-push-action@v6
  with:
    context: .
    file: ./docker/Dockerfile  # Adjust path as needed
```

## Workflow File Structure

### Standard Python Workflow

```yaml
name: Security Scanning

on:
  push:
    branches: [main, macos, master]
  pull_request:
    branches: [main, macos, master]

jobs:
  scan:
    runs-on: ubuntu-latest
    name: Dependency & Code Security Scan

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: |
          python -m pip install --upgrade pip
          pip install pip-audit bandit
          pip install -e ".[dev]" || true
      - run: pip-audit --desc --skip-editable
        continue-on-error: true
      - run: |
          bandit -r . --exclude tests -f json -o bandit-report.json || true
        continue-on-error: true
      - uses: returntocorp/semgrep-action@v1
        with:
          config: p/security-audit
        continue-on-error: true
      - run: pytest tests/ -v --tb=short --timeout=300 || true
        continue-on-error: true

  build:
    runs-on: ubuntu-latest
    needs: scan
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python -m pip install --upgrade pip build twine
      - run: python -m build --outdir dist/
      - run: twine check dist/*
      - uses: actions/upload-artifact@v4
        with:
          name: distributions
          path: dist/
```

## Action Version Pinning

Always pin to major versions only (`@v4`, `@v5`), not patch versions (`@v4.2.0`).

**Why?** GitHub Actions don't publish patch releases separately. Major versions are branches that receive updates automatically.

```yaml
# CORRECT
uses: actions/checkout@v4
uses: docker/build-push-action@v6

# WRONG
uses: actions/checkout@v4.2.0
uses: docker/build-push-action@v6.1.5
```

## Monitoring Workflow Status

### Via CLI

```bash
# Check latest run for a repo
gh run list --repo RALaBarge/beigebox --limit=5

# View detailed log for a run
gh run view <RUN_ID> --repo RALaBarge/beigebox --log

# List all workflows
gh workflow list --repo RALaBarge/beigebox
```

### Via Web

Visit: https://github.com/RALaBarge/{REPO}/actions

## Adding a New Workflow

1. Create `.github/workflows/my-workflow.yml` in repo
2. Use major version pins only (`@v4`, not `@v4.2.0`)
3. Include `continue-on-error: true` for non-critical checks
4. Test locally with `act` (optional):
   ```bash
   act -j scan -P ubuntu-latest=ghcr.io/catthehacker/ubuntu:full-latest
   ```
5. Push to trigger workflow
6. Monitor: `gh run list --repo RALaBarge/{REPO}`

## Security Best Practices

- Use `GITHUB_TOKEN` (default) instead of personal tokens in public repos
- Pin action versions to major versions, review updates regularly
- Use `continue-on-error: true` only for informational checks (semgrep, bandit)
- Never commit secrets (use GitHub Secrets for credentials)
- Run security scans on every push and PR
- Require passing checks before merging

## Debugging Failed Workflows

1. **Check logs:** `gh run view <ID> --repo RALaBarge/<REPO> --log`
2. **Look for error context:** Search for `error`, `failed`, `ERROR`
3. **Check environment:** Python/Node/Rust version mismatches?
4. **Verify dependencies:** Are pyproject.toml, package.json, Cargo.toml correct?
5. **Test locally:**
   - Python: `python -m pytest tests/ -v --timeout=300`
   - Node: `npm ci && npm audit`
   - Rust: `cargo test --verbose`

## Status Badges

Add to README.md to show workflow status:

```markdown
[![Security Scan](https://github.com/RALaBarge/beigebox/workflows/Security%20Scanning/badge.svg)](https://github.com/RALaBarge/beigebox/actions)
[![Tests](https://github.com/RALaBarge/beigebox/workflows/Tests/badge.svg)](https://github.com/RALaBarge/beigebox/actions)
[![Docker Build](https://github.com/RALaBarge/beigebox/workflows/Docker%20Build/badge.svg)](https://github.com/RALaBarge/beigebox/actions)
```

## See Also

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [returntocore/semgrep-action](https://github.com/returntocorp/semgrep-action)
- [actions/setup-python](https://github.com/actions/setup-python)
- [docker/build-push-action](https://github.com/docker/build-push-action)
