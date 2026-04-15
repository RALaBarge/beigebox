# BeigeBox Security Deployment Checklist

Pre-deployment verification for production releases.

---

## Authentication & Access Control

- [ ] **Password Auth Enabled**
  - [ ] `auth.mode: "password"` in config.yaml
  - [ ] Default password changed from "changeme"
  - [ ] `BB_INITIAL_PASSWORD` set via environment (not in config)
  - [ ] Password policy enforced (min 8 chars)

- [ ] **Session Management**
  - [ ] Session cookies: httponly=true, samesite=strict
  - [ ] Session TTL configured (default 4 hours)
  - [ ] Logout endpoint clears cookies properly
  - [ ] Session tokens signed with random key

- [ ] **HTTPS/TLS**
  - [ ] SSL certificate installed and valid
  - [ ] `secure` flag enabled on cookies
  - [ ] Certificate renewal automated (Let's Encrypt, etc.)
  - [ ] HTTP redirects to HTTPS

---

## Security Hardening Layers

### P1-A: RAG Poisoning Detection

- [ ] RAG poisoning detector enabled
  ```yaml
  embedding_poisoning_detection:
    enabled: true
    sensitivity: 0.95
  ```
- [ ] Baseline statistics initialized (L2 norm tracking)
- [ ] Z-score anomaly detection active
- [ ] False positive rate <1% verified in testing

### P1-B: Prompt Injection Detection

- [ ] Enhanced injection guard enabled
  ```yaml
  guardrails:
    enabled: true
    prompt_injection_detection: true
  ```
- [ ] Pattern library updated
- [ ] Semantic detection enabled
- [ ] Quarantine database writable
- [ ] 87-92% detection accuracy verified

### P1-C: API Anomaly Detection

- [ ] Anomaly detector enabled
  ```yaml
  security:
    anomaly_detector:
      enabled: true
      sensitivity: medium
  ```
- [ ] Rate limiting active (token budgets per user)
- [ ] Error rate thresholds configured
- [ ] Model switching detection active
- [ ] Latency baseline established

### P1-D: Memory Integrity

- [ ] Memory validator enabled
  ```yaml
  security:
    memory_integrity:
      enabled: true
  ```
- [ ] Cross-session isolation verified
- [ ] Context window tracking active
- [ ] State integrity checks passing

---

## Dependency & Supply Chain Security

- [ ] Run security scan
  ```bash
  ./scripts/security-scan.sh
  ```
- [ ] No high/critical CVEs in dependencies
- [ ] Python package hashes verified (`requirements.lock`)
- [ ] Docker image pinned to specific digest
- [ ] Container image scanned (Trivy, Grype)

```bash
docker scan ralabarge/beigebox:0.2.0-security
```

---

## Data Protection & Storage

- [ ] SQLite database encrypted at rest
  - [ ] Enable with: `pragma key='secure-key'`
  - [ ] Or use OS-level encryption (LUKS, FileVault)

- [ ] Session storage secure
  - [ ] `data/beigebox.db` owned by appuser:appuser
  - [ ] Permissions: 0600 (read/write owner only)

- [ ] Quarantine database backed up
  - [ ] `data/quarantine.db` backed up daily
  - [ ] Retention policy: 90 days minimum

- [ ] Logs sanitized
  - [ ] No passwords, tokens, or PII in logs
  - [ ] Log rotation configured (10MB, 3 backups)

---

## Container & Infrastructure

### Docker Security

- [ ] Image: non-root user (appuser:1000)
- [ ] Filesystem: read-only root filesystem
  ```yaml
  read_only_root_filesystem: true
  tmpfs: [/tmp, /app/logs]
  ```
- [ ] Capabilities: minimal set
  ```yaml
  cap_drop: [ALL]
  cap_add: [NET_BIND_SERVICE]
  ```
- [ ] Resource limits enforced
  ```yaml
  memory: "512M"
  cpus: "1.0"
  ```

### Kubernetes (if deployed)

- [ ] Pod Security Policy enforced
  - [ ] Restricted PSP applied
  - [ ] No privileged containers
  - [ ] No host network/PID/IPC

- [ ] RBAC configured
  ```bash
  kubectl apply -f deploy/k8s/beigebox-rbac.yaml
  ```
- [ ] Network policies enforced
  - [ ] Ingress: from ingress controller only
  - [ ] Egress: to backends + observability only

- [ ] Secrets management
  - [ ] Use Kubernetes Secrets (or Vault)
  - [ ] `BB_INITIAL_PASSWORD` injected at runtime
  - [ ] Never commit secrets to git

---

## Observability & Monitoring

- [ ] Tap event logging enabled
  ```bash
  beigebox tap  # Verify live events
  ```
- [ ] Metrics exported (Prometheus format)
  ```bash
  curl localhost:8000/metrics
  ```
- [ ] Grafana dashboards deployed
  - [ ] Security Overview dashboard
  - [ ] RAG Defense dashboard
  - [ ] Threat Timeline dashboard

- [ ] Alerts configured
  - [ ] High injection detection rate (>10/min)
  - [ ] RAG poisoning detected (any)
  - [ ] API anomalies flagged
  - [ ] Failed logins (5+ in 5 min)

- [ ] Log aggregation setup
  - [ ] Logs shipped to ELK/Splunk
  - [ ] Retention: 90 days minimum

---

## API & Backend Configuration

- [ ] Backend connectivity verified
  ```bash
  beigebox ring  # Health check
  ```
- [ ] Multi-backend router tested (if applicable)
- [ ] Fallback backends configured
- [ ] Latency monitoring active

- [ ] API key rotation scheduled
  - [ ] Monthly rotation for service accounts
  - [ ] Audited in SQLite

- [ ] Rate limiting active
  - [ ] Default: 100 req/min per API key
  - [ ] Adjust based on load testing

---

## Testing & Validation

- [ ] Security test suite passes
  ```bash
  pytest tests/test_rag_poisoning_detector.py -v  # 25 tests
  pytest tests/test_enhanced_injection_guard.py -v  # 35 tests
  pytest tests/test_anomaly_detector.py -v  # 15 tests
  ```

- [ ] Penetration testing (optional)
  - [ ] Third-party pentest completed
  - [ ] All findings addressed
  - [ ] Report available to customers

- [ ] Load testing done
  - [ ] 100+ concurrent users tested
  - [ ] No memory leaks detected
  - [ ] Security detectors still responsive

- [ ] Chaos testing (optional)
  - [ ] Backend failure recovery tested
  - [ ] Network partition handled gracefully
  - [ ] Fallback mechanisms work

---

## Documentation & Runbooks

- [ ] [AUTH_SETUP.md](./AUTH_SETUP.md) complete
  - [ ] Login instructions
  - [ ] API reference
  - [ ] Troubleshooting guide

- [ ] [SECURITY_POLICY.md](./SECURITY_POLICY.md) complete
  - [ ] Threat model documented
  - [ ] Detection accuracy published
  - [ ] False positive rates listed

- [ ] Incident response runbooks
  - [ ] Data breach procedure
  - [ ] Security advisory response
  - [ ] Customer notification template

- [ ] README updated
  - [ ] Security features highlighted
  - [ ] Quick start guide
  - [ ] Configuration reference

---

## Release Checklist

- [ ] Version bumped to v0.2.0-security
  ```bash
  grep __version__ beigebox/__init__.py
  ```

- [ ] CHANGELOG updated with:
  - [ ] Password authentication support
  - [ ] All P1 security layers
  - [ ] Bug fixes
  - [ ] Breaking changes (if any)

- [ ] GitHub release created
  ```bash
  gh release create v0.2.0-security \
    --title "BeigeBox v0.2.0-security" \
    --notes "$(cat CHANGELOG.md | head -50)"
  ```

- [ ] Docker image pushed
  ```bash
  docker tag docker-beigebox:latest ralabarge/beigebox:0.2.0-security
  docker push ralabarge/beigebox:0.2.0-security
  ```

- [ ] PyPI package released (if applicable)
  ```bash
  python -m build
  python -m twine upload dist/*
  ```

---

## Sign-Off

- [ ] Security team reviewed and approved
- [ ] Ops team confirmed deployment readiness
- [ ] Product team validated feature completeness
- [ ] Legal reviewed terms & privacy policy

**Signed off by:**
- Security Lead: _________________ Date: _______
- Ops Lead: _________________ Date: _______
- Product Manager: _________________ Date: _______

---

## Post-Deployment

- [ ] Monitor error logs for first 24 hours
- [ ] Verify all security detectors reporting events
- [ ] Baseline anomaly detector on customer traffic
- [ ] Customer success team trained on features
- [ ] Support tickets for security features tracked

---

## See Also

- [AUTH_SETUP.md](./AUTH_SETUP.md) — Authentication configuration
- [SECURITY_TOOLS_README.md](../beigebox/security/SECURITY_TOOLS_README.md) — Security layers
- [SECURITY_POLICY.md](./SECURITY_POLICY.md) — Threat model & detection accuracy
