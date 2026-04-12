# BeigeBox Distribution Channels

Complete reference for installing BeigeBox security tools across all distribution channels: PyPI, Docker Hub, and Homebrew.

---

## Channel Overview

| Channel | Best for | Performance | Maintenance |
|---|---|---|---|
| **PyPI** | Development, Python environments | Instant availability | Automated via CI/CD |
| **Docker** | Production, Kubernetes, containers | Multi-arch (amd64/arm64) | Automated via CI/CD |
| **Homebrew** | macOS/Linux CLI, native tooling | System integration | Manual (community-maintained) |

---

## Channel 1: PyPI (Python Package Index)

### Packages

**beigebox** — LLM proxy middleware
- **PyPI URL:** https://pypi.org/project/beigebox/
- **Latest:** https://pypi.org/project/beigebox/#history
- **Repository:** https://github.com/RALaBarge/beigebox

**bluetruth** — Bluetooth diagnostics
- **PyPI URL:** https://pypi.org/project/bluetruth/
- **Repository:** https://github.com/RALaBarge/beigebox

**embeddings-guardian** — Security library
- **PyPI URL:** https://pypi.org/project/embeddings-guardian/
- **Repository:** https://github.com/RALaBarge/beigebox

### Installation

```bash
# Latest versions
pip install beigebox
pip install bluetruth
pip install embeddings-guardian

# Specific version
pip install beigebox==1.3.5
pip install bluetruth==0.2.0
pip install embeddings-guardian==0.1.0

# From requirements.txt
pip install -r requirements.txt
# where requirements.txt contains:
# beigebox>=1.3.5
# bluetruth>=0.2.0
# embeddings-guardian>=0.1.0

# Development (editable)
git clone https://github.com/RALaBarge/beigebox.git
cd beigebox
pip install -e .
```

### Verification

```bash
# Check if installed
pip show beigebox
pip show bluetruth
pip show embeddings-guardian

# Version check
python -c "import beigebox; print(beigebox.__version__)"
python -c "import bluetruth; print(bluetruth.__version__)"

# CLI test
beigebox --version
bluetruth --version
```

### Upgrade

```bash
pip install --upgrade beigebox
pip install --upgrade beigebox[security]  # with security extras
```

### Uninstall

```bash
pip uninstall beigebox
pip uninstall bluetruth
pip uninstall embeddings-guardian
```

### Dependency Management

**beigebox** depends on:
- fastapi >= 0.115.0
- uvicorn[standard] >= 0.32.0
- pydantic >= 2.4.0
- beigebox-security >= 0.1.0-beta
- (+ 20+ other dependencies)

Full dependency tree:
```bash
pip show -f beigebox  # Show all dependencies
pipdeptree           # Visual dependency tree
```

---

## Channel 2: Docker Hub

### Images

**ralabarge/beigebox**
- **Registry:** https://hub.docker.com/r/ralabarge/beigebox
- **Latest:** `ralabarge/beigebox:latest`
- **Stable:** `ralabarge/beigebox:1.3.5`
- **Architectures:** amd64 (x86_64), arm64 (Apple Silicon, ARM Linux)

**ralabarge/bluetruth**
- **Registry:** https://hub.docker.com/r/ralabarge/bluetruth
- **Latest:** `ralabarge/bluetruth:latest`
- **Stable:** `ralabarge/bluetruth:0.2.0`

**ralabarge/embeddings-guardian**
- **Registry:** https://hub.docker.com/r/ralabarge/embeddings-guardian
- **Latest:** `ralabarge/embeddings-guardian:latest`
- **Stable:** `ralabarge/embeddings-guardian:0.1.0-beta`

### Installation

```bash
# Pull image
docker pull ralabarge/beigebox:1.3.5
docker pull ralabarge/bluetruth:0.2.0

# Run container
docker run -d \
  -p 1337:1337 \
  -v ~/.beigebox:/root/.beigebox \
  ralabarge/beigebox:1.3.5

# Interactive shell
docker run -it ralabarge/beigebox:1.3.5 /bin/bash
```

### Multi-Architecture

Both amd64 and arm64 are included in the manifest:

```bash
# Docker automatically selects correct architecture
docker run ralabarge/beigebox:1.3.5

# Explicit architecture selection
docker run --platform linux/amd64 ralabarge/beigebox:1.3.5
docker run --platform linux/arm64 ralabarge/beigebox:1.3.5

# Check available architectures
docker manifest inspect ralabarge/beigebox:1.3.5
```

### Docker Compose

```yaml
version: '3.9'

services:
  beigebox:
    image: ralabarge/beigebox:1.3.5
    ports:
      - "1337:1337"
    environment:
      - LOG_LEVEL=INFO
    volumes:
      - ~/.beigebox:/root/.beigebox
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:1337/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  bluetruth:
    image: ralabarge/bluetruth:0.2.0
    ports:
      - "8484:8484"
    privileged: true  # Required for Bluetooth HCI access
    volumes:
      - ~/.bluetruth:/root/.bluetruth
```

### Verification

```bash
# Check image locally
docker images | grep ralabarge

# Inspect image
docker inspect ralabarge/beigebox:1.3.5

# Run health check
docker run ralabarge/beigebox:1.3.5 beigebox --version

# Pull and test
docker pull ralabarge/beigebox:1.3.5
docker run --rm ralabarge/beigebox:1.3.5 beigebox --help
```

### Image Details

**beigebox:1.3.5**
- **Base:** python:3.11-slim
- **Size:** ~800MB
- **Layers:** Optimized multi-stage build
- **User:** unprivileged (uid=1000)
- **Exposed ports:** 1337 (web UI), 1338 (metrics)

### Upgrade

```bash
# Pull latest
docker pull ralabarge/beigebox:latest

# Update service
docker compose down
docker compose pull beigebox
docker compose up -d

# Clean up
docker image prune
```

### Uninstall

```bash
# Remove container
docker rm <container_id>

# Remove image
docker rmi ralabarge/beigebox:1.3.5

# Remove all BeigeBox images
docker rmi $(docker images | grep ralabarge | awk '{print $3}')
```

---

## Channel 3: Homebrew

### Tap

Official tap for BeigeBox tools:

**Repository:** https://github.com/RALaBarge/homebrew-beigebox

### Installation

```bash
# Add tap (one-time)
brew tap RALaBarge/homebrew-beigebox

# Install formulas
brew install beigebox
brew install bluetruth
brew install embeddings-guardian

# Specific version
brew install beigebox@1
```

### Formulas

**beigebox.rb**
- **Depends on:** python@3.11, pip
- **Install to:** /usr/local/bin/beigebox
- **License:** AGPL-3.0

**bluetruth.rb**
- **Depends on:** python@3.10, dbus
- **Install to:** /usr/local/bin/bluetruth
- **License:** MIT
- **Caveats:** Requires DBus (Linux) or Bluetooth framework (macOS)

**embeddings-guardian.rb**
- **Depends on:** python@3.11
- **Install to:** Python site-packages (library)
- **License:** MIT

### Verification

```bash
# Check if tap is installed
brew tap-info RALaBarge/homebrew-beigebox

# List installed formulas
brew list | grep beigebox

# Show formula details
brew info beigebox
brew info bluetruth
brew info embeddings-guardian

# Version check
beigebox --version
bluetruth --version

# Which binary
which beigebox
which bluetruth
```

### Upgrade

```bash
# Upgrade all
brew upgrade

# Upgrade specific
brew upgrade beigebox
brew upgrade bluetruth
```

### Uninstall

```bash
# Remove formula
brew uninstall beigebox
brew uninstall bluetruth
brew uninstall embeddings-guardian

# Remove tap
brew untap RALaBarge/homebrew-beigebox
```

### Configuration

After installation, configure Homebrew-installed tools:

**macOS:**
```bash
# Create config directory
mkdir -p ~/.beigebox
cp /usr/local/etc/beigebox/config.yaml.example ~/.beigebox/config.yaml

# Edit config
nano ~/.beigebox/config.yaml

# Start service
brew services start beigebox
brew services list  # Check status
```

**Linux:**
```bash
mkdir -p ~/.beigebox
sudo systemctl enable beigebox
sudo systemctl start beigebox
```

---

## Compatibility Matrix

| Component | PyPI | Docker | Homebrew |
|---|:---:|:---:|:---:|
| **beigebox** | ✓ | ✓ | ✓ |
| **bluetruth** | ✓ | ✓ | ✓ |
| **embeddings-guardian** | ✓ | ✓ | ✓ |
| **Python 3.10** | ✓* | ✓** | - |
| **Python 3.11** | ✓ | ✓ | ✓ |
| **macOS ARM64** | ✓ | ✓ | ✓ |
| **macOS Intel** | ✓ | ✓ | ✓ |
| **Linux x86** | ✓ | ✓ | ✓ |
| **Linux ARM64** | ✓ | ✓ | ✓ |

*Only for bluetruth (requires Python 3.10+)  
**Separate image for Python 3.10

---

## Version Management

### Latest Releases

| Package | Latest | Release Date |
|---|---|---|
| beigebox | 1.3.5 | [DATE] |
| bluetruth | 0.2.0 | [DATE] |
| embeddings-guardian | 0.1.0 | [DATE] |

### Version Pinning

**For production:**
```bash
pip install beigebox==1.3.5    # Pin to exact version
docker pull ralabarge/beigebox:1.3.5  # Specify tag
brew install beigebox@1        # Pin to major version
```

**For development:**
```bash
pip install beigebox           # Latest
docker pull ralabarge/beigebox:latest
brew install beigebox          # Latest
```

---

## Verification Script

Verify all distribution channels are working:

```bash
./scripts/verify_distributions.sh              # Full test
./scripts/verify_distributions.sh --pip        # PyPI only
./scripts/verify_distributions.sh --docker     # Docker only
./scripts/verify_distributions.sh --brew       # Homebrew only
./scripts/verify_distributions.sh --quick      # Skip slow tests
```

---

## Troubleshooting

### PyPI Issues

**No such file or directory during install**
```bash
pip install --upgrade pip setuptools wheel
pip install beigebox
```

**Version not found**
```bash
pip index versions beigebox  # Check available versions
```

**Dependency conflict**
```bash
pip install --no-deps beigebox  # Install without dependencies
pip check  # Check for conflicts
```

### Docker Issues

**Image pull fails**
```bash
docker logout
docker login  # Re-authenticate to Docker Hub
docker pull ralabarge/beigebox:1.3.5
```

**Container won't start**
```bash
docker logs <container_id>  # Check logs
docker inspect <container_id>  # Check config
```

**Permission denied (Bluetooth)**
```bash
# Use --privileged flag
docker run -d --privileged ralabarge/bluetruth:0.2.0
```

### Homebrew Issues

**Tap not found**
```bash
brew tap-info RALaBarge/homebrew-beigebox
# If not found, tap was removed or renamed
brew tap RALaBarge/homebrew-beigebox
```

**Formula conflicts**
```bash
brew cleanup
brew doctor  # Check for issues
```

**DBus not found (bluetruth Linux)**
```bash
# Ubuntu/Debian
sudo apt-get install libdbus-1-dev

# Fedora
sudo dnf install dbus-devel
```

---

## Security Considerations

### PyPI
- Packages signed with GPG (optional)
- Two-factor authentication recommended
- Dependency tree auditable via `pipdeptree`

### Docker
- Images scanned for CVEs (Trivy)
- Non-root user (uid=1000)
- Read-only root filesystem (recommended)
- Signed manifests (Docker Content Trust)

### Homebrew
- Formula integrity verified by Homebrew
- Checksums computed and verified
- Community-maintained tap

---

## CI/CD Integration

### GitHub Actions

```yaml
- name: Install BeigeBox
  run: pip install beigebox==${{ github.ref_name }}

- name: Pull Docker image
  run: docker pull ralabarge/beigebox:${{ github.ref_name }}

- name: Install via Homebrew
  run: |
    brew tap RALaBarge/homebrew-beigebox
    brew install beigebox
```

### GitLab CI

```yaml
install_beigebox:
  script:
    - pip install beigebox==$CI_COMMIT_TAG
    - docker pull ralabarge/beigebox:$CI_COMMIT_TAG
```

---

## Release Process

All three channels are updated during release:

1. **Merge to main branch**
2. **Create Git tag:** `v1.3.5`
3. **GitHub Actions triggers:**
   - Build and test all channels
   - Upload to PyPI
   - Push to Docker Hub
   - Create Homebrew formula
4. **Verify:** `./scripts/verify_distributions.sh`
5. **Create GitHub Release** with multi-channel instructions

See [.github/RELEASE_TEMPLATE.md](.github/RELEASE_TEMPLATE.md).

---

## Support Matrix

| Channel | Support | Issues | Updates |
|---|---|---|---|
| **PyPI** | Official | GitHub Issues | Automatic via CI/CD |
| **Docker** | Official | Docker Hub | Automatic via CI/CD |
| **Homebrew** | Community | GitHub/Homebrew | Manual formula updates |

---

## Further Reading

- [README.md](README.md) — Quick start guide
- [homebrew-beigebox/README.md](homebrew-beigebox/README.md) — Homebrew tap details
- [CLAUDE.md](CLAUDE.md) — Development guide
- [.github/RELEASE_TEMPLATE.md](.github/RELEASE_TEMPLATE.md) — Release notes template
