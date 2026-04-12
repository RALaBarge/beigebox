# BeigeBox Deployment Security Checklist

**Version:** 1.0 | **Status:** Production-Ready | **Last Updated:** April 2026

Use this checklist to deploy BeigeBox securely in production. Estimated time: **2-3 hours** for full deployment + baseline calibration.

---

## Pre-Deployment (1 hour)

Before you start the container, complete these checks.

### Infrastructure & Networking

- [ ] **Network Isolation:** BeigeBox host is on a private network (not exposed to public internet without authentication)
- [ ] **Firewall Rules:** Inbound traffic restricted to authorized clients only (whitelist source IPs)
- [ ] **Outbound Access:** BeigeBox can reach configured backends (Ollama, OpenRouter, etc.)
- [ ] **Storage Path:** Persistent volume mounted for `/data/` (SQLite + ChromaDB + quarantine)
- [ ] **Logs Path:** Persistent volume mounted for `/logs/` (audit trails, Tap events)

**Commands to verify:**
```bash
# Check network connectivity
docker exec beigebox ping ollama  # if Ollama in same network
docker exec beigebox curl -s https://api.openrouter.io/health

# Check storage
docker exec beigebox ls -lh /data/
docker exec beigebox ls -lh /logs/
```

### Secrets & Authentication

- [ ] **API Keys:** All backend keys stored in `$HOME/.beigebox/keys` (not in config.yaml)
- [ ] **Docker Secrets:** If using Docker Secrets, mounted to `/run/secrets/` (not in env vars)
- [ ] **No Plaintext Keys:** Audit `docker ps` output and logs — no sensitive keys visible
- [ ] **Key Rotation Plan:** Document who rotates keys and when (e.g., quarterly)
- [ ] **Access Control:** Only authorized admins can edit config.yaml and runtime_config.yaml

**Commands to verify:**
```bash
# Check for plaintext keys in config
grep -i "api_key\|password\|secret" config.yaml  # should be empty

# Check env vars don't contain keys
docker inspect beigebox | grep -i "api_key"  # should be empty

# Verify file permissions
ls -l ~/.beigebox/keys  # should be 600 or 700
```

### Dependency Security

- [ ] **Dependencies Scanned:** Run `pip-audit` and `trivy` on container image
- [ ] **No High-Severity Vulns:** Zero critical/high vulns in pip or system packages
- [ ] **Hash-Locked Dependencies:** `requirements.lock` is used (not `requirements.txt`)
- [ ] **Image Digest:** Pull image by digest (not tag) to ensure reproducibility

**Commands to verify:**
```bash
# Scan pip dependencies
pip-audit

# Scan container image
trivy image beigebox:latest

# Verify hash-locked deps
grep -c "==" requirements.lock  # should be >10
```

### Configuration Review

- [ ] **Read SECURITY_POLICY.md:** Understand threat model and detection capabilities
- [ ] **Review config.yaml:** Understand each security setting
- [ ] **Audit config.example.yaml:** Know what features are available
- [ ] **Confirm Tap Logging Enabled:** `observability.tap.enabled: true`
- [ ] **Enable Rate Limiting:** `auth.rate_limit_per_minute: 60` (adjust per use case)
- [ ] **Set Token Budgets:** Per-key limits in `auth.keys[].max_daily_tokens_*`

**Example config snippet:**
```yaml
auth:
  enabled: true
  rate_limit_per_minute: 60
  keys:
    - name: "production-key"
      max_daily_tokens_in: 1000000
      max_daily_tokens_out: 500000
      extraction_detection: true

guardrails:
  input:
    injection_detection: true
    pattern_library: "standard"
    action_on_detection: "log"  # "log" | "block" | "quarantine" — start with "log"

rag_poisoning_detection:
  enabled: true
  method: "magnitude_anomaly"
  sensitivity: 0.85
  action: "quarantine"  # documents with anomalous embeddings go to quarantine

observability:
  tap:
    enabled: true
    sqlite_path: "./logs/tap.db"
```

---

## Baseline Calibration (1-2 hours)

Before enabling blocking rules, establish baseline statistics from your legitimate traffic.

### Step 1: Deploy in "Log Only" Mode (3-5 days)

Deploy with all security controls set to `action: "log"` (non-blocking).

```yaml
# config.yaml
guardrails:
  input:
    action_on_detection: "log"  # not "block"

rag_poisoning_detection:
  action: "log"  # not "quarantine"
```

**Commands:**
```bash
docker compose up -d beigebox

# Verify logging is working
docker compose logs -f beigebox | grep -i "tap\|security"
```

**What to monitor:** Let normal traffic flow. BeigeBox will log all security events without blocking.

### Step 2: Collect Baseline Data (3-5 days)

Let the system run with real traffic. Generate baseline statistics:

```bash
# Collect embedding baseline for RAG poisoning detection
python scripts/calibrate_embedding_baseline.py \
  --chroma-path ./data/chroma \
  --output config/baseline.json \
  --samples 200

# Expected output:
# Baseline calibrated from 200 legitimate embeddings
# Mean L2 norm: 1.023, std: 0.087
# Saved to: config/baseline.json
```

**Troubleshooting:**
- If <100 embeddings in ChromaDB, wait longer or manually add legitimate documents
- If baseline.json is empty, check ChromaDB path and permissions

### Step 3: Review False Positives (1 day)

Check Tap logs for false positives:

```bash
# Query Tap SQLite database
sqlite3 ./logs/tap.db << 'EOF'
SELECT COUNT(*) as alert_count, 
       event_type, 
       severity
FROM tap_events
WHERE timestamp > datetime('now', '-24 hours')
AND (source='prompt_guard' OR source='rag_scanner')
GROUP BY event_type, severity;
EOF

# Expected output:
# alert_count | event_type              | severity
# 0           | injection_detected      | warning
# 2           | poisoned_document       | critical
# (2 might be legitimate; investigate each)
```

**How to investigate a suspected false positive:**

```bash
# Get the alert details
sqlite3 ./logs/tap.db << 'EOF'
SELECT timestamp, meta, request_summary
FROM tap_events
WHERE source='prompt_guard'
AND timestamp > datetime('now', '-24 hours')
LIMIT 5;
EOF

# If legitimate (e.g., user asking "How do I ignore previous context?"):
# Adjust sensitivity threshold downward (0.85 → 0.80)
# Recalibrate baseline
```

**False Positive Rate Target:** <0.5% (i.e., <1 FP per 200 legitimate requests)

### Step 4: Threshold Tuning (if needed)

If FPR >1%, lower the sensitivity threshold:

```bash
# Generate recommended config
python scripts/tune_thresholds.py \
  --baseline config/baseline.json \
  --test-legit config/legit_test_embeddings.json \
  --test-poison config/poison_test_embeddings.json \
  --output config/recommended_config.yaml \
  --target_fpr 0.005  # target <0.5% FPR

# Review the output:
cat config/recommended_config.yaml
# rag_poisoning_detection:
#   sensitivity: 0.82  # lowered from 0.85
#   fpr_estimated: 0.004
```

**Apply the recommended settings:**
```bash
# Update config.yaml with new sensitivity
sed -i 's/sensitivity: 0.85/sensitivity: 0.82/' config.yaml

# Restart
docker compose restart beigebox

# Verify
docker compose logs beigebox | grep -i "baseline loaded"
```

---

## Stage 1: Logging & Monitoring (3-5 days)

Validate that monitoring is set up correctly.

### Configuration Checklist

- [ ] **Tap Logging Enabled:** `observability.tap.enabled: true`
- [ ] **Tap Database Path:** Points to persistent volume (survives restarts)
- [ ] **Log Rotation:** Configure logrotate to prevent disk fill
- [ ] **Alerts Configured:** Integrates with your monitoring (Prometheus, Datadog, PagerDuty, etc.)

**Enable alerts:**
```yaml
# Example for Prometheus alerting
alerts:
  - name: "High Injection Alert Rate"
    query: "source='prompt_guard' AND severity='critical'"
    threshold: 5  # per hour
    action: "page"  # page on-call engineer

  - name: "Quarantine Queue Growing"
    query: "source='rag_scanner' AND action='quarantine'"
    threshold: 10  # documents per day
    action: "email"  # email team for investigation
```

### Monitor Daily (5 min)

```bash
# Daily health check
beigebox tap | head -20  # show recent events
beigebox ring  # ping running instance

# Check quarantine
beigebox quarantine stats
beigebox quarantine list --limit 5

# Check for alerts
sqlite3 ./logs/tap.db << 'EOF'
SELECT COUNT(*) FROM tap_events
WHERE timestamp > datetime('now', '-24 hours')
AND severity IN ('critical', 'warning');
EOF
```

**Expected output:** 0-2 alerts per day (legitimate documents or investigation items)

### Monitor Weekly (15 min)

```bash
# Review false positives
sqlite3 ./logs/tap.db << 'EOF'
SELECT event_type, COUNT(*) as count
FROM tap_events
WHERE timestamp > datetime('now', '-7 days')
AND source IN ('prompt_guard', 'rag_scanner')
GROUP BY event_type;
EOF

# Recalibrate baseline
python scripts/calibrate_embedding_baseline.py \
  --chroma-path ./data/chroma \
  --output config/baseline.json \
  --samples 250

# Check API key anomalies
beigebox flash  # show stats and security summary
```

**Expected:** FPR remains <0.5%; no new API key anomalies

### Monitor Monthly (30 min)

```bash
# Full re-tuning if FPR increased
python scripts/tune_thresholds.py \
  --baseline config/baseline.json \
  --test-legit config/legit_test_embeddings.json \
  --test-poison config/poison_test_embeddings.json \
  --output config/recommended_config.yaml

# Review quarantine for patterns
sqlite3 ./logs/tap.db << 'EOF'
SELECT meta, COUNT(*) as count
FROM tap_events
WHERE source='rag_scanner'
AND timestamp > datetime('now', '-30 days')
GROUP BY meta
ORDER BY count DESC
LIMIT 10;
EOF

# Check for new vulnerabilities
pip-audit
trivy image beigebox:latest
```

---

## Stage 2: Blocking Mode (7+ days)

Once you confirm <0.5% FPR for 1 week, enable blocking.

### Enable Blocking Rules

```yaml
# config.yaml
guardrails:
  input:
    action_on_detection: "block"  # change from "log"

rag_poisoning_detection:
  action: "block"  # change from "log"
```

**Commands:**
```bash
# Edit config.yaml
nano config.yaml

# Restart
docker compose restart beigebox

# Verify blocking is active
docker compose logs beigebox | grep -i "action.*block"
```

### Monitor Blocking Behavior (daily)

```bash
# Check block rate
sqlite3 ./logs/tap.db << 'EOF'
SELECT COUNT(*) as blocks_per_day
FROM tap_events
WHERE source IN ('prompt_guard', 'rag_scanner')
AND action='block'
AND timestamp > datetime('now', '-24 hours');
EOF

# Expected: 0-1 blocks per day (anomalies, not FPs)

# Investigate each block
sqlite3 ./logs/tap.db << 'EOF'
SELECT timestamp, request_summary, pattern_matched, severity
FROM tap_events
WHERE action='block'
AND timestamp > datetime('now', '-24 hours');
EOF
```

**If block rate >2/day:** FPR has increased; lower sensitivity and investigate.

### Rollback Procedure (if needed)

If false positives exceed acceptable levels after blocking is enabled:

```bash
# 1. Revert to logging
nano config.yaml  # set action: "log"

# 2. Restart
docker compose restart beigebox

# 3. Investigate blocked requests
sqlite3 ./logs/tap.db << 'EOF'
SELECT request_summary, pattern_matched
FROM tap_events
WHERE action='block' AND timestamp > datetime('now', '-48 hours')
LIMIT 20;
EOF

# 4. Lower sensitivity threshold (if RAG poisoning FPs)
sed -i 's/sensitivity: 0.85/sensitivity: 0.80/' config.yaml

# 5. Re-enable blocking after 2-3 days of <0.5% FPR
```

---

## Stage 3: Full Enablement (Optional)

After 2+ weeks of successful blocking with <0.5% FPR, consider advanced features:

- [ ] **Enable Semantic Injection Detection:** Requires LLM Guard or custom model (adds 50-200ms latency)
- [ ] **Enable Output Exfiltration Monitoring:** Detects suspicious patterns in responses
- [ ] **Enable Operator Agent:** If using multi-turn reasoning (ensure guardrails are strict)
- [ ] **Enable Model Integrity Checks:** Verify model weights at startup (Ollama models only)

**Example advanced config:**
```yaml
guardrails:
  input:
    injection_backend: "llm_guard"  # semantic scanning (requires Docker sidecar)
    action_on_detection: "block"
  output:
    exfiltration_detection: true
    exfiltration_threshold: 0.8

model_integrity:
  enabled: true
  mode: "warn"  # "warn" | "block"
  registry_path: "./config/model_integrity.yaml"
```

**Effort:** 2-4 additional hours for setup and tuning.

---

## Performance Baseline (Document Before Going Live)

Measure baseline latency and memory usage with security controls enabled.

### Latency Test

```bash
# Measure end-to-end latency (request to response)
time curl -X POST http://localhost:1337/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:4b",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'

# Expected: 500-2000ms (depends on model size and backend)
# Security overhead: <5% (injection scanning is O(1), <2ms)
```

**Document baseline latency:**
- Median: _____ ms
- P95: _____ ms
- P99: _____ ms

### Memory Usage Test

```bash
# Monitor memory during sustained load
docker stats beigebox --no-stream

# Expected: 200-800 MB (depends on baseline model size)

# Run 100 requests in parallel
for i in {1..100}; do
  curl -X POST http://localhost:1337/v1/chat/completions ... &
done
wait

# Check peak memory
docker stats beigebox --no-stream
```

**Document peak memory:**
- Idle: _____ MB
- 100 concurrent requests: _____ MB

### Throughput Test

```bash
# Measure tokens/second
time for i in {1..10}; do
  curl -X POST http://localhost:1337/v1/chat/completions \
    -H "Authorization: Bearer $API_KEY" \
    -d '{"model": "qwen3:4b", "messages": [...]}'
done

# Calculate: 10 requests / total_time_seconds
```

**Document throughput:**
- Requests/second: _____
- Tokens/second: _____

**Alert thresholds (set in your monitoring):**
- If latency increases >20% from baseline → investigate
- If memory exceeds peak + 200MB → investigate
- If throughput drops >15% → investigate

---

## Monitoring & Alerting Setup

### Required Metrics

Configure these metrics in your observability platform (Prometheus, Datadog, etc.):

```yaml
metrics:
  security:
    - metric: "guardrail_injections_total"
      query: "source='prompt_guard' AND action='block'"
      alert: "if rate > 5/hour"

    - metric: "rag_poisoning_detections_total"
      query: "source='rag_scanner' AND action='block'"
      alert: "if rate > 2/day"

    - metric: "api_anomalies_total"
      query: "source='api_anomaly' AND signal='extraction_signal'"
      alert: "if rate > 1/24h"

    - metric: "false_positive_rate"
      query: "source IN ('prompt_guard', 'rag_scanner') AND action='log'"
      target: "<0.5%"
      alert: "if > 1%"

  performance:
    - metric: "request_latency_p95"
      target: "<baseline * 1.2"
      alert: "if > baseline * 1.5"

    - metric: "memory_usage_peak"
      target: "<baseline + 200MB"
      alert: "if > baseline + 500MB"
```

### Alert Routing

| Alert | Priority | Recipient | Action |
|-------|----------|-----------|--------|
| Injection blocked (>5/hour) | High | On-call engineer | Page (5 min response) |
| RAG poisoning quarantined (>2/day) | High | On-call engineer | Page (5 min response) |
| API anomaly detected (>1/24h) | High | On-call engineer | Page (5 min response) |
| False positive rate >1% | Medium | Security team | Email (4h response) |
| Latency spike >baseline * 1.5 | Medium | Ops team | Email (4h response) |

### Example: Prometheus Alert Rules

```yaml
# prometheus/rules.yaml
groups:
  - name: beigebox_security
    rules:
      - alert: HighInjectionAlertRate
        expr: |
          rate(tap_events_total{source="prompt_guard",action="block"}[1h]) > 5
        for: 5m
        annotations:
          summary: "BeigeBox: High injection detection rate"
          action: "page"

      - alert: RAGPoisoningDetected
        expr: |
          rate(tap_events_total{source="rag_scanner",action="block"}[1d]) > 2
        for: 10m
        annotations:
          summary: "BeigeBox: Multiple RAG poisoning attempts"
          action: "page"

      - alert: HighFalsePositiveRate
        expr: |
          (
            rate(tap_events_total{source=~"prompt_guard|rag_scanner",action="log"}[1d])
            / (rate(tap_events_total{source=~"prompt_guard|rag_scanner"}[1d]) + 0.001)
          ) > 0.01
        for: 30m
        annotations:
          summary: "BeigeBox: FPR >1%; review and tune thresholds"
          action: "email"
```

---

## Go/No-Go Decision Criteria

Before moving to production, verify:

### Readiness Checklist

- [ ] **Infrastructure:** Network isolated, storage persistent, secrets secure
- [ ] **Baseline Calibrated:** 200+ legitimate embeddings; threshold tuned to <0.5% FPR
- [ ] **Monitoring Setup:** Tap logging enabled; alerts configured and tested
- [ ] **Performance Baseline:** Latency and memory measured; within acceptable range
- [ ] **Security Review:** SECURITY_POLICY.md read; all controls understood
- [ ] **Team Training:** Operators familiar with troubleshooting guide (see OPERATIONS_RAG_DEFENSE.md)
- [ ] **Rollback Plan:** Know how to quickly disable controls if needed
- [ ] **Incident Response:** Escalation contacts documented; on-call rotation in place

### Success Criteria (First Week in Prod)

- [ ] **<0.5% False Positive Rate:** Fewer than 1 legitimate request blocked per 200 requests
- [ ] **Zero Bypasses:** No successful injection/poisoning attacks detected in Tap logs
- [ ] **Latency <baseline * 1.2:** Security controls don't significantly impact performance
- [ ] **Zero Data Loss:** All requests logged; no events dropped from Tap database

### Decision Point

| Criterion | Status | Decision |
|-----------|--------|----------|
| FPR <0.5% | ✓ | GO |
| Performance baseline met | ✓ | GO |
| Team trained | ✓ | GO |
| Monitoring alerts working | ✓ | GO |
| Rollback procedure documented | ✓ | GO |

**GO/NO-GO:** ________ (date) — Approved by: ________ (name)

---

## Emergency Procedures

### Disable Security Controls (<5 min)

If false positives are causing production issues:

```bash
# 1. Edit config.yaml
nano config.yaml

# 2. Disable blocking
guardrails:
  input:
    action_on_detection: "log"  # disable blocking
rag_poisoning_detection:
  enabled: false  # disable RAG detection entirely

# 3. Restart
docker compose restart beigebox

# 4. Verify (should see "action: log" in logs)
docker compose logs beigebox | grep -i "action"

# 5. Notify team
# Email: security@ralabarge.dev
# Subject: "EMERGENCY: BeigeBox security controls disabled for production"
```

### Incident Escalation

If you detect a potential security incident:

1. **Capture evidence:** Screenshot/export the Tap log entry
2. **Disable the key:** Rotate or revoke the API key immediately
3. **Notify:** Email security@ralabarge.dev with:
   - Timestamp of incident
   - API key ID (not the full key)
   - Request/response summary
   - Any patterns you noticed

4. **Follow up:** Our team will respond within 24 hours

---

## Troubleshooting

### Issue: "FPR >1%; legitimate requests being blocked"

**Steps:**
1. Verify false positive with user (does the request contain suspicious patterns?)
2. Check Tap log:
   ```bash
   sqlite3 ./logs/tap.db << 'EOF'
   SELECT pattern_matched, request_summary
   FROM tap_events WHERE action='block' LIMIT 5;
   EOF
   ```
3. If pattern is incorrect: Report to security@ralabarge.dev
4. If pattern is legitimate: Lower sensitivity threshold by 0.05
   ```bash
   sed -i 's/sensitivity: 0.85/sensitivity: 0.80/' config.yaml
   docker compose restart beigebox
   ```
5. Retest for 3 days; monitor FPR

### Issue: "Quarantine growing; many documents blocked"

**Steps:**
1. Review quarantine:
   ```bash
   beigebox quarantine list --limit 20
   ```
2. Determine if documents are legitimately suspicious or false positives
3. If legitimate documents: Lower sensitivity (see above)
4. If suspicious: Keep quarantine enabled; investigate source

### Issue: "Latency increased after enabling security controls"

**Steps:**
1. Check which control is slow:
   ```bash
   # Latency breakdown in Tap logs
   sqlite3 ./logs/tap.db << 'EOF'
   SELECT source, AVG(latency_ms) as avg_latency
   FROM tap_events
   WHERE timestamp > datetime('now', '-1 hour')
   GROUP BY source;
   EOF
   ```
2. If prompt_guard is slow (>5ms): Disable semantic injection detection (if enabled)
3. If rag_scanner is slow (>3ms): Reduce baseline sample size
4. If output_monitor is slow (>10ms): Disable exfiltration detection (if enabled)

### Issue: "Can't connect to ChromaDB for baseline calibration"

**Steps:**
```bash
# Check ChromaDB is running
docker compose logs chroma

# Check path permissions
docker exec beigebox ls -l /data/chroma

# Verify ChromaDB is healthy
docker exec beigebox curl -s http://chroma:8000/api/v1/heartbeat
```

---

## Document Your Deployment

Fill in these details for your team:

```yaml
deployment:
  date_deployed: _______________
  approved_by: _______________
  
  configuration:
    environment: production / staging / development
    baseline_samples: _____
    sensitivity_tuned_to: _____
    fpr_measured: _____
    
  baseline:
    mean_latency_ms: _____
    p95_latency_ms: _____
    peak_memory_mb: _____
    throughput_tokens_sec: _____
    
  monitoring:
    tap_database_path: _____
    alert_platform: _____  # Prometheus / Datadog / etc
    on_call_rotation: _____
    escalation_contact: security@ralabarge.dev
    
  rollback:
    can_disable_controls_in_minutes: _____
    documented_procedure: yes / no
```

---

## Support

- **Questions:** GitHub Discussions (https://github.com/ralabarge/beigebox/discussions)
- **Security Issues:** security@ralabarge.dev (24-48h response)
- **Emergency:** Include "URGENT" in subject line

---

**Last Updated:** April 12, 2026  
**Version:** 1.0  
**Status:** Production-Ready

For the latest version, see: https://github.com/ralabarge/beigebox/blob/main/DEPLOYMENT_SECURITY_CHECKLIST.md
