# BeigeBox Release [VERSION]

> [YYYY-MM-DD] — [Brief description]

## Installation

This release is available across **three distribution channels**:

### 1. Homebrew (macOS/Linux)

Add the official tap and install:

```bash
brew tap RALaBarge/homebrew-beigebox
brew install beigebox@[MAJOR.MINOR]  # or 'beigebox' for latest
beigebox --version
```

For Bluetooth diagnostics:
```bash
brew install bluetruth
```

For security library:
```bash
brew install embeddings-guardian
```

### 2. PyPI (Python Package Index)

Install via pip:

```bash
pip install beigebox==[VERSION]
pip install bluetruth==[VERSION]
pip install embeddings-guardian==[VERSION]
```

Verify installation:
```bash
beigebox --version
bluetruth --version
```

### 3. Docker Hub

Pull container images:

```bash
# Core LLM proxy
docker pull ralabarge/beigebox:[VERSION]

# Bluetooth diagnostics
docker pull ralabarge/bluetruth:[VERSION]

# Security library
docker pull ralabarge/embeddings-guardian:[VERSION]
```

For amd64/arm64 (multi-arch):
```bash
docker run -d ralabarge/beigebox:[VERSION]  # auto-selects platform
```

---

## Verification

Verify distributions are working:

```bash
# Quick test (all channels)
./scripts/verify_distributions.sh --quick

# Full verification (includes Docker pulls)
./scripts/verify_distributions.sh
```

**Tests:**
- ✓ PyPI packages install without conflicts
- ✓ Homebrew formulas tap and install correctly
- ✓ Docker images pull and run
- ✓ CLI binaries work and report correct version
- ✓ Multi-architecture support (amd64/arm64)

---

## What's New

### Major Features
- [Feature 1]
- [Feature 2]

### Bug Fixes
- [Fix 1]
- [Fix 2]

### Security
- [Security improvement 1]
- [Security improvement 2]

### Dependencies
- Updated [dependency] to [version]
- Pinned [critical dependency] to [version]

---

## Checksums

### PyPI (sha256)

| Package | SHA256 |
|---|---|
| beigebox-[VERSION].tar.gz | `[SHA256_HERE]` |
| bluetruth-[VERSION].tar.gz | `[SHA256_HERE]` |
| embeddings_guardian-[VERSION].tar.gz | `[SHA256_HERE]` |

Verify with:
```bash
echo "[SHA256_HERE]  beigebox-[VERSION].tar.gz" | sha256sum -c -
```

### Docker (digest)

| Image | Digest |
|---|---|
| ralabarge/beigebox:[VERSION] | `sha256:[DIGEST_HERE]` |
| ralabarge/bluetruth:[VERSION] | `sha256:[DIGEST_HERE]` |

Verify with:
```bash
docker inspect ralabarge/beigebox:[VERSION] | jq -r '.[] | .RepoDigests'
```

---

## Homebrew Formulas

Formulas are registered in the official tap: https://github.com/RALaBarge/homebrew-beigebox

- **beigebox.rb** — LLM proxy (Python 3.11+)
- **bluetruth.rb** — Bluetooth diagnostics (Python 3.10+, DBus)
- **embeddings-guardian.rb** — Security library (Python 3.11+)

---

## Upgrade Instructions

### From Homebrew

```bash
brew upgrade beigebox
brew upgrade bluetruth
brew upgrade embeddings-guardian
```

### From PyPI

```bash
pip install --upgrade beigebox==[VERSION]
pip install --upgrade bluetruth==[VERSION]
pip install --upgrade embeddings-guardian==[VERSION]
```

### From Docker

Update your `docker-compose.yaml`:
```yaml
services:
  beigebox:
    image: ralabarge/beigebox:[VERSION]  # Update version
    # ... rest of config
```

Then:
```bash
docker compose pull
docker compose up -d --build
```

---

## Known Issues

| Issue | Workaround |
|---|---|
| [Issue] | [Workaround] |

See [KNOWN_VULNERABILITIES.md](KNOWN_VULNERABILITIES.md) for threat-specific issues.

---

## Migration Guide

If upgrading from [PREVIOUS_VERSION]:

1. **Backup your config:**
   ```bash
   cp config.yaml config.yaml.backup
   ```

2. **Update packages:**
   ```bash
   pip install --upgrade beigebox==[VERSION]
   ```

3. **Validate config:**
   ```bash
   beigebox flash
   ```

4. **Review breaking changes:**
   See [CHANGELOG.md](CHANGELOG.md#breaking-changes)

---

## Support

- **Issues:** https://github.com/RALaBarge/beigebox/issues
- **Discussions:** https://github.com/RALaBarge/beigebox/discussions
- **Security:** security@ralabarge.com (responsible disclosure)

---

## Attribution

Thanks to all contributors, testers, and community members.

---

**Release signed:** [GPG_KEY_FINGERPRINT]  
**Build details:** [CI_BUILD_URL]  
**Changes:** [COMPARE_URL]
