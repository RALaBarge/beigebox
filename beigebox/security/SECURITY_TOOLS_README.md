# BeigeBox Security Tools

Comprehensive suite of security tools for AI/LLM systems: content filtering, policy enforcement, supply chain hardening, and threat detection.

## Packages

### 1. beigebox-security (built-in)

Core security module integrated into BeigeBox:
- **Prompt injection detection** — pattern + semantic scanning (87-92% detection)
- **RAG poisoning defense** — embedding anomaly detection (95% TP rate)
- **API key theft prevention** — token budgets + anomaly detection
- **Input/output filtering** — content policies, guardrails
- **Supply chain hardening** — dependency scanning, CVE detection

**Installation:**
```bash
pip install beigebox  # includes beigebox-security
```

**Usage:**
```python
from beigebox.security import GuardRails, RAGDefense

# In your BeigeBox config:
guardrails:
  enabled: true
  prompt_injection_detection: true
  rag_poisoning_detection: true
  
rag_poisoning_detection:
  enabled: true
  method: "magnitude_anomaly"
  sensitivity: 0.85
```

### 2. embeddings-guardian

Security library for embedding-based content filtering:
- **Semantic content filtering** — block harmful embeddings before storage
- **Policy enforcement** — tag and categorize documents by intent
- **Anomaly detection** — detect poisoned/malicious embeddings
- **Quarantine system** — flag and review suspicious content

**Installation:**
```bash
# Option 1: Homebrew
brew tap RALaBarge/homebrew-beigebox
brew install embeddings-guardian

# Option 2: PyPI (recommended for libraries)
pip install embeddings-guardian

# Option 3: From source
pip install -e .
```

**Usage:**
```python
from embeddings_guardian import EmbeddingGuard, Anomaly

guard = EmbeddingGuard(model="nomic-embed-text")

# Check if embedding is suspicious
is_malicious = guard.detect(embedding, method="centroid_distance")
if is_malicious:
    quarantine(document)  # Flag for review
```

### 3. bluetruth

Bluetooth diagnostic and threat detection tool:
- **Device discovery** — find all Bluetooth devices in range
- **Security assessment** — analyze encryption, pairing, signal strength
- **Threat detection** — identify spoofing, jamming, replay attacks
- **Event correlation** — link related events across devices
- **Test simulation** — mock device lifecycle for agent testing

**Installation:**
```bash
# Option 1: Homebrew
brew tap RALaBarge/homebrew-beigebox
brew install bluetruth

# Option 2: PyPI
pip install bluetruth

# Option 3: Docker
docker pull ralabarge/bluetruth:0.2.0
```

**Quick Start:**
```bash
sudo bluetruth serve --port 8484  # Start collector
bluetruth list                    # List devices
bluetruth threat AA:BB:CC:DD:EE:FF  # Threat assessment
```

See [BLUETRUTH_README.md](../tools/BLUETRUTH_README.md) for full documentation.

## Deployment

### Docker Stack

All security tools available in Docker images:

```bash
# BeigeBox (core + all security)
docker pull ralabarge/beigebox:1.3.5

# BlueTruth (standalone)
docker pull ralabarge/bluetruth:0.2.0

# Embeddings Guardian (library)
docker pull ralabarge/embeddings-guardian:0.1.0
```

Docker Compose with security enabled:
```yaml
services:
  beigebox:
    image: ralabarge/beigebox:1.3.5
    environment:
      - ENABLE_PROMPT_INJECTION_DETECTION=true
      - ENABLE_RAG_DEFENSE=true
    volumes:
      - ./data:/app/data

  bluetruth:
    image: ralabarge/bluetruth:0.2.0
    privileged: true  # Required for Bluetooth access
    ports:
      - "8484:8484"
```

### Kubernetes

Deploy BeigeBox with security hardening on Kubernetes:

```bash
kubectl apply -f deploy/k8s/beigebox-security.yaml
```

Includes:
- Security policies (read-only root, network segmentation)
- Resource limits and pod disruption budgets
- RBAC with minimal permissions
- ConfigMap for hot-reload config

### Bare Metal / Systemd

Install via package manager and run as systemd service:

```bash
# Ubuntu/Debian
sudo apt-get install beigebox

# macOS
brew install beigebox

# Then enable service
sudo systemctl enable beigebox
sudo systemctl start beigebox
```

## Threat Model

BeigeBox defends against:

| Threat | Detection | Mitigation |
|--------|-----------|-----------|
| **Prompt Injection** | Pattern detection + semantic scanning | Block suspicious prompts, log attempts |
| **RAG Poisoning** | Embedding anomaly detection | Quarantine suspicious documents |
| **API Key Theft** | Token budget + anomaly detection | Revoke keys, alert administrator |
| **Supply Chain** | Dependency scanning, image pinning | Fail build on CVE, pinned digests |
| **Bluetooth Threat** | Device spoofing, jamming, replay detection | Alert, isolate device, block |

## Configuration

### Global Security Config

In `config.yaml`:
```yaml
features:
  guardrails: true
  rag_poisoning_detection: true

guardrails:
  enabled: true
  prompt_injection_detection: true
  prompt_injection_method: "semantic"
  prompt_injection_sensitivity: 0.85
  input_filtering: true
  output_filtering: true

rag_poisoning_detection:
  enabled: true
  method: "magnitude_anomaly"
  sensitivity: 0.85
  action: "quarantine"
  quarantine_path: "./data/quarantine.db"
```

### Per-Request Overrides

Use Z-commands to override:
```
z: guardrails=true prompt_injection_detection=true
```

### Runtime Config

Edit `runtime_config.yaml` for hot-reload (no restart):
```yaml
features:
  guardrails: true
  
guardrails:
  prompt_injection_sensitivity: 0.90  # Increase strictness
```

## Monitoring

### CLI Commands

```bash
beigebox tap                    # Live security event log
beigebox quarantine stats       # RAG defense statistics
beigebox quarantine list        # List quarantined documents
beigebox flash                  # Security status at a glance
```

### Metrics

BeigeBox emits Tap events for all security operations:
- `security.prompt_injection.detected`
- `security.rag_poisoning.quarantined`
- `security.api_key.budget_exceeded`
- `security.threat.bluetooth.spoofing`

### Grafana Dashboards

Included dashboards:
- **Security Overview** — attack volume, detection rates
- **RAG Defense** — quarantine stats, anomaly detections
- **Threat Timeline** — events over time
- **API Key Usage** — budget tracking, anomalies

## Hardening Checklist

Before production deployment:

- [ ] Enable all guardrails in `config.yaml`
- [ ] Run `security-scan.sh` to check dependencies
- [ ] Calibrate detection thresholds in test environment
- [ ] Enable Tap logging and set up Grafana
- [ ] Configure automated backups for quarantine database
- [ ] Set up alerts for security events (email/Slack)
- [ ] Run threat detection test suite
- [ ] Document any false positives for threshold tuning
- [ ] Enable read-only root filesystem in Docker/K8s
- [ ] Rotate API keys and review access logs

See [DEPLOYMENT_SECURITY_CHECKLIST.md](../../DEPLOYMENT_SECURITY_CHECKLIST.md).

## Testing

```bash
# Run all security tests
pytest tests/ -k security -v

# Test specific threat
pytest tests/test_prompt_injection.py -v
pytest tests/test_rag_poisoning.py -v
pytest tests/test_api_key_theft.py -v
pytest tests/test_bluetruth_scenarios.py -v

# Test with coverage
pytest --cov=beigebox.security tests/
```

## Verification

Verify distributions are correct and working:

```bash
./scripts/verify_distributions.sh              # Full verification
./scripts/verify_distributions.sh --pip        # PyPI packages
./scripts/verify_distributions.sh --docker     # Docker images
./scripts/verify_distributions.sh --brew       # Homebrew formulas
```

## License

- **beigebox-security:** Part of beigebox (AGPL-3.0 + Commercial license)
- **embeddings-guardian:** MIT
- **bluetruth:** MIT

See [LICENSE.md](../../LICENSE.md) and [COMMERCIAL_LICENSE.md](../../COMMERCIAL_LICENSE.md).

## Support

- **Issues:** https://github.com/RALaBarge/beigebox/issues
- **Security:** Email security@ralabarge.com for responsible disclosure
- **Homebrew:** https://github.com/RALaBarge/homebrew-beigebox

## Resources

- [Security Policy](../../SECURITY_POLICY.md) — Threat model, detection accuracy, false positives
- [Deployment Checklist](../../DEPLOYMENT_SECURITY_CHECKLIST.md) — Pre-deployment validation
- [Known Vulnerabilities](../../KNOWN_VULNERABILITIES.md) — Gaps, roadmap, workarounds
- [RAG Defense](../../docs/RAG_DEFENSE_INDEX.md) — RAG poisoning operations runbook
