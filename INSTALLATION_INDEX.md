# BeigeBox Installation Index

Complete guide to installing BeigeBox and all security tools across all distribution channels.

---

## Quick Navigation

- **[Installation (4 Options)](#installation-4-options)** — Choose your preferred method
- **[Distribution Channels](#distribution-channels)** — Deep dive into each channel
- **[Verification](#verification)** — Test your installation
- **[Troubleshooting](#troubleshooting)** — Common issues and fixes
- **[Full Reference](#full-reference)** — Complete documentation

---

## Installation (4 Options)

### Option 1: Homebrew (Recommended for macOS/Linux CLI)

```bash
# Add tap (one-time setup)
brew tap RALaBarge/homebrew-beigebox

# Install BeigeBox
brew install beigebox

# Start the server
beigebox dial

# Optional: Install security tools
brew install bluetruth
brew install embeddings-guardian
```

**Best for:** macOS and Linux users who prefer native system integration  
**Requirements:** Homebrew, Python 3.10+ (bluetruth) or 3.11+ (beigebox)  
**See also:** [homebrew-beigebox/README.md](homebrew-beigebox/README.md)

---

### Option 2: PyPI (Recommended for Python Environments)

```bash
# Install from Python Package Index
pip install beigebox

# Start the server
beigebox dial

# Optional: Install security tools
pip install bluetruth
pip install embeddings-guardian

# Verify installation
pip show beigebox
beigebox --version
```

**Best for:** Python developers, virtual environments, CI/CD  
**Requirements:** Python 3.11+ (3.10+ for bluetruth)  
**Package URLs:**
- beigebox: https://pypi.org/project/beigebox/
- bluetruth: https://pypi.org/project/bluetruth/
- embeddings-guardian: https://pypi.org/project/embeddings-guardian/

---

### Option 3: Docker (Recommended for Production/Kubernetes)

```bash
# Pull the official image
docker pull ralabarge/beigebox:1.3.5

# Run the container
docker run -d -p 1337:1337 ralabarge/beigebox:1.3.5

# Open web UI
# http://localhost:1337

# Optional: Docker Compose
docker compose -f docker-compose.yaml up -d
```

**Best for:** Production deployments, Kubernetes, containerized environments  
**Requirements:** Docker engine, 2GB RAM minimum  
**Images:**
- `ralabarge/beigebox:1.3.5` — LLM proxy (amd64/arm64)
- `ralabarge/bluetruth:0.2.0` — Bluetooth diagnostics
- `ralabarge/embeddings-guardian:0.1.0-beta` — Security library

---

### Option 4: From Source (Development)

```bash
# Clone repository
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox

# Install in development mode
pip install -e .

# Start dev server with auto-reload
uvicorn beigebox.main:app --reload

# Or run in production mode
beigebox dial
```

**Best for:** Contributing, debugging, customization  
**Requirements:** Git, Python 3.11+, pip  
**See also:** [CLAUDE.md](CLAUDE.md) — Development guide

---

## Distribution Channels

### Channel Comparison

| Feature | Homebrew | PyPI | Docker | Source |
|---|:---:|:---:|:---:|:---:|
| **Installation time** | 2-3 min | 1-2 min | 5-10 min | 3-5 min |
| **Disk usage** | ~500MB | ~800MB | 1-2GB | 2-3GB |
| **System integration** | Yes | Yes | No | Manual |
| **Auto-updates** | Via brew | Via pip | Manual | Git |
| **macOS support** | ✓ | ✓ | ✓ | ✓ |
| **Linux support** | ✓ | ✓ | ✓ | ✓ |
| **Windows support** | - | ✓* | ✓ | ✓* |
| **Production ready** | ✓ | ✓ | ✓✓ | - |

*Windows: Use WSL2 or native Python 3.11+

---

## Verification

### Quick Test

After installation, verify it works:

```bash
# Test CLI
beigebox --version
beigebox --help

# Test web UI (if running)
curl http://localhost:1337/health

# Test OpenAI API compatibility
curl -X POST http://localhost:1337/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Full Verification Script

Verify all distribution channels are working:

```bash
# Run verification
./scripts/verify_distributions.sh

# Test specific channel
./scripts/verify_distributions.sh --pip      # PyPI only
./scripts/verify_distributions.sh --docker   # Docker only
./scripts/verify_distributions.sh --brew     # Homebrew only
./scripts/verify_distributions.sh --quick    # Skip slow tests
```

**Output:**
```
=================================================================
Verification Summary
=================================================================
Passed:  12
Failed:  0
Skipped: 2
=================================================================
All tests passed!
```

---

## Troubleshooting

### Homebrew Issues

**Tap not found**
```bash
# Manually add tap
brew tap RALaBarge/homebrew-beigebox
brew tap-info RALaBarge/homebrew-beigebox  # Verify
```

**Formula installation fails**
```bash
# Try installing dependencies first
brew install python@3.11

# Then install formula
brew install beigebox
```

**Command not in PATH**
```bash
# Check installation
brew info beigebox

# Reinstall if needed
brew uninstall beigebox
brew install beigebox

# Verify PATH
which beigebox
```

---

### PyPI Issues

**Package not found**
```bash
# Update pip
pip install --upgrade pip

# Check available versions
pip index versions beigebox

# Install specific version
pip install beigebox==1.3.5
```

**Dependency conflict**
```bash
# Check for conflicts
pip check

# Install without deps (advanced)
pip install --no-deps beigebox
```

**Permission denied**
```bash
# Install to user directory
pip install --user beigebox

# Or use virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate
pip install beigebox
```

---

### Docker Issues

**Image pull fails**
```bash
# Check Docker daemon
docker ps

# Re-authenticate
docker logout
docker login

# Try pull again
docker pull ralabarge/beigebox:1.3.5
```

**Container won't start**
```bash
# Check logs
docker logs <container_id>

# Inspect image
docker inspect ralabarge/beigebox:1.3.5

# Try verbose output
docker run -it ralabarge/beigebox:1.3.5 bash
```

**Permission denied (Bluetooth on Linux)**
```bash
# Use privileged mode
docker run --privileged ralabarge/bluetruth:0.2.0
```

---

### Common Issues

**Port already in use**
```bash
# Change port
docker run -p 9000:1337 ralabarge/beigebox:1.3.5
beigebox dial --port 9000
```

**Out of memory**
```bash
# Increase Docker memory
docker run -m 4g ralabarge/beigebox:1.3.5

# Or adjust config
vim config.yaml  # Set memory limits
```

**SSL/TLS certificate errors**
```bash
# For Docker behind proxy
docker run -e PYTHONHTTPSVERIFY=0 ralabarge/beigebox:1.3.5

# For pip behind proxy
pip install --trusted-host pypi.python.org beigebox
```

---

## Full Reference

### Documentation Files

| File | Purpose |
|---|---|
| **[README.md](README.md)** | Quick start, features, security |
| **[DISTRIBUTION.md](DISTRIBUTION.md)** | Complete distribution channel reference |
| **[DISTRIBUTION_SETUP_SUMMARY.md](DISTRIBUTION_SETUP_SUMMARY.md)** | Setup checklist and summary |
| **[INSTALLATION_INDEX.md](INSTALLATION_INDEX.md)** | This file — installation guide |
| **[homebrew-beigebox/README.md](homebrew-beigebox/README.md)** | Homebrew tap documentation |
| **[beigebox/tools/BLUETRUTH_README.md](beigebox/tools/BLUETRUTH_README.md)** | BlueTruth tool guide |
| **[beigebox/security/SECURITY_TOOLS_README.md](beigebox/security/SECURITY_TOOLS_README.md)** | Security tools overview |
| **[.github/RELEASE_TEMPLATE.md](.github/RELEASE_TEMPLATE.md)** | Release notes template |
| **[CLAUDE.md](CLAUDE.md)** | Development guide |
| **[d0cs/deployment.md](d0cs/deployment.md)** | Production deployment guide |

### Package URLs

**PyPI**
- https://pypi.org/project/beigebox/
- https://pypi.org/project/bluetruth/
- https://pypi.org/project/embeddings-guardian/

**Docker Hub**
- https://hub.docker.com/r/ralabarge/beigebox
- https://hub.docker.com/r/ralabarge/bluetruth
- https://hub.docker.com/r/ralabarge/embeddings-guardian

**GitHub**
- https://github.com/RALaBarge/beigebox
- https://github.com/RALaBarge/homebrew-beigebox

**Documentation**
- https://github.com/RALaBarge/beigebox/tree/main/d0cs

---

## Installation by Use Case

### I want to run BeigeBox locally for testing

**Recommended:** Docker Compose
```bash
cd docker
./FIRST_RUN.sh
./launch.sh up -d
```

### I want to use BeigeBox as a Python library in my project

**Recommended:** PyPI
```bash
pip install beigebox
```

### I want a system-wide CLI tool on macOS/Linux

**Recommended:** Homebrew
```bash
brew install beigebox
```

### I want to deploy to production (Kubernetes, servers)

**Recommended:** Docker + Helm/K8s manifests
```bash
docker pull ralabarge/beigebox:1.3.5
kubectl apply -f deploy/k8s/beigebox.yaml
```

### I want to contribute or customize BeigeBox

**Recommended:** From source
```bash
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox
pip install -e .
```

---

## Configuration After Installation

After installing via any channel, configure BeigeBox:

### 1. Create config directory
```bash
mkdir -p ~/.beigebox
```

### 2. Copy config template
```bash
# From source
cp config.example.yaml ~/.beigebox/config.yaml

# Or download from GitHub
curl -o ~/.beigebox/config.yaml \
  https://raw.githubusercontent.com/RALaBarge/beigebox/main/config.example.yaml
```

### 3. Edit configuration
```bash
nano ~/.beigebox/config.yaml
```

Key settings:
- `server.port` — Web UI port (default: 1337)
- `backends` — LLM backend configuration
- `features` — Enable/disable features
- `models` — Default model selection
- `security` — Enable guardrails, RAG defense

### 4. Start the server
```bash
beigebox dial              # Production mode
# or
uvicorn beigebox.main:app --reload  # Dev mode
```

### 5. Open web UI
```
http://localhost:1337
```

---

## Updates and Upgrades

### Homebrew
```bash
brew upgrade beigebox
```

### PyPI
```bash
pip install --upgrade beigebox
```

### Docker
```bash
docker pull ralabarge/beigebox:latest
docker compose up -d --build
```

### From Source
```bash
git pull origin main
pip install -e . --upgrade
```

---

## Uninstallation

### Homebrew
```bash
brew uninstall beigebox
brew untap RALaBarge/homebrew-beigebox
```

### PyPI
```bash
pip uninstall beigebox
```

### Docker
```bash
docker rmi ralabarge/beigebox:1.3.5
```

### From Source
```bash
pip uninstall beigebox
rm -rf beigebox/
```

---

## Getting Help

- **Issues:** https://github.com/RALaBarge/beigebox/issues
- **Discussions:** https://github.com/RALaBarge/beigebox/discussions
- **Security:** security@ralabarge.com

---

**Last Updated:** April 12, 2026  
**Status:** All distribution channels ready  
**Channels:** Homebrew ✓ | PyPI ✓ | Docker ✓
