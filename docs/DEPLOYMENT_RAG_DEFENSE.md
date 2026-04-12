# RAG Poisoning Defense — Production Deployment Runbook

**Version:** 1.0  
**Status:** Ready for Stage 1 (Warn Mode)  
**Last Updated:** 2026-04-12

## Overview

This document describes the phased deployment process for RAG poisoning detection in production. The defense uses embedding magnitude anomaly detection to flag potentially malicious context insertions before they affect LLM responses.

### Key Principles

- **Non-blocking initially**: Stage 1 runs in "warn" mode (logs suspicious embeddings, doesn't reject)
- **Data-driven decisions**: Move between stages only when metrics confirm safety
- **Quick rollback**: Disable detection in <5 minutes if false positive rate exceeds 2%
- **Weekly recalibration**: Baseline statistics adapt to legitimate corpus changes

---

## Stage 1: Deployment in Warn Mode (3 Days)

### Goal
Deploy the detector in non-blocking mode to validate false positive rate (FPR) on production traffic.

### Prerequisites

1. **Baseline calibration** (run once):
   ```bash
   python scripts/calibrate_embedding_baseline.py \
     --chroma-path ./data/chroma \
     --output config/baseline.json \
     --samples 200
   ```

2. **Threshold tuning** (run once):
   - Prepare test set with legitimate embeddings (e.g., first 200 from production corpus)
   - Prepare test set with known-bad embeddings (adversarial examples)
   ```bash
   python scripts/tune_thresholds.py \
     --baseline config/baseline.json \
     --test-legit test_legit_embeddings.json \
     --test-poison test_poison_embeddings.json \
     --output config/recommended_config.yaml
   ```

3. **Review recommended config**:
   - Verify sensitivity value (typically 0.95–0.98)
   - Verify TPR >90% on test poisoned set
   - Verify FPR <1% on test legitimate set

### Deployment Steps

1. **Update `config.yaml`** to enable detection in warn mode:
   ```yaml
   rag_poisoning_detection:
     enabled: true
     sensitivity: 0.95      # from tuning recommendation
     detection_mode: warn   # log only, don't block
     baseline_window: 1000
     min_norm: 0.1
     max_norm: 100.0
   ```

2. **Restart BeigeBox**:
   ```bash
   beigebox dial  # or docker compose restart beigebox
   ```

3. **Verify detection is running**:
   ```bash
   # Check logs for detection initialization
   tail -f logs/beigebox.log | grep -i "rag\|poison"
   ```

### Stage 1: Daily Monitoring (3 Days)

**Daily Task (5 min):**

1. Check quarantine statistics:
   ```bash
   beigebox quarantine stats
   ```

2. Review daily summary:
   - Total embeddings processed
   - Number flagged as suspicious (should be <0.5%)
   - Review 3–5 flagged embeddings for false positives

3. Alert conditions:
   - **HALT STAGE 1** if FPR suddenly spikes >2%
     - Immediately disable: `detection_mode: off` in config
     - Investigate cause (corpus change, embedding model change, etc.)
     - Roll back to Stage 0

### Stage 1: Decision Criteria

**Proceed to Stage 2 if:**
- FPR consistently <0.5% across 3 days
- No false positives in manual review
- System performance unaffected (no latency increase)

**Stay in Stage 1 if:**
- FPR between 0.5% and 1%
- Extend monitoring 1–2 weeks, then reassess

**Rollback to Stage 0 if:**
- FPR >2% at any point
- False positives affecting user experience
- System performance degradation

---

## Stage 2: Block Mode (7+ Days)

### Goal
Enable blocking (quarantine) mode to prevent poisoned embeddings from being stored or retrieved.

### Prerequisites

- Stage 1 complete with FPR <0.5%
- Stakeholder approval (security team, ops lead)

### Deployment Steps

1. **Update `config.yaml`** to block mode:
   ```yaml
   rag_poisoning_detection:
     enabled: true
     sensitivity: 0.95
     detection_mode: quarantine  # block suspicious embeddings
     baseline_window: 1000
     min_norm: 0.1
     max_norm: 100.0
   
   # Optional: strict mode blocks entire request
   # detection_mode: strict
   ```

2. **Restart BeigeBox**:
   ```bash
   beigebox dial
   ```

3. **Verify blocking is active**:
   ```bash
   # Check logs for quarantine activity
   tail -f logs/beigebox.log | grep -i quarantine
   ```

### Stage 2: Daily Monitoring (7+ Days)

**Daily Task (10 min):**

1. Check quarantine statistics:
   ```bash
   beigebox quarantine stats
   ```

2. Log summary:
   - Embeddings blocked (target: 0–2 per day)
   - Any spike in blocks → investigate

3. Review quarantine list:
   ```bash
   beigebox quarantine list --limit 10
   ```
   - Sample 5 recent blocks
   - Assess if legitimate or actual attacks

### Stage 2: Weekly Task

1. Recalibrate baseline:
   ```bash
   python scripts/calibrate_embedding_baseline.py \
     --chroma-path ./data/chroma \
     --output config/baseline.json \
     --samples 200
   ```

2. Review metrics:
   - Total blocks
   - Any false positives?
   - Corpus changed (size, distribution)?

### Stage 2: Decision Criteria

**Proceed to Stage 3 if:**
- 7+ days with no issues
- <0.5% FPR maintained
- Zero false positives in manual review
- Business stakeholders confirmed acceptance

**Extend Stage 2 if:**
- Occasional false positives (<1 per week)
- Corpus rapidly changing
- Need more data before full confidence

**Rollback if:**
- FPR >2%
- False positives affecting critical functionality
- System stability issues

---

## Stage 3: Full Enablement (Optional Advanced Layers)

### Goal
Enable additional detection layers (neighborhood density, dimension-wise anomalies) for maximum coverage.

### Prerequisites

- Stage 2 stable for 7+ days with no false positives

### Deployment Steps

1. **Update `config.yaml`** for advanced detection:
   ```yaml
   rag_poisoning_detection:
     enabled: true
     sensitivity: 0.95
     detection_mode: quarantine
     enable_neighborhood_detection: true
     enable_dimension_analysis: true
   ```

2. **Restart and monitor**:
   ```bash
   beigebox dial
   tail -f logs/beigebox.log | grep -i poison
   ```

3. **Verify additional detections**:
   - Slightly higher block rate (expected)
   - FPR should remain <1%

### Stage 3: Ongoing Maintenance

**Weekly:**
- Recalibrate baseline
- Review quarantine logs
- Update baseline.json if corpus changes significantly

**Monthly:**
- Full metrics review
- Adjust sensitivity if needed
- Update documentation

---

## Rollback Procedure

**If False Positive Rate Exceeds 2% at Any Stage:**

1. **Immediate (1 min):**
   ```bash
   # Disable detection
   # Edit config.yaml:
   # detection_mode: warn  # downgrade to warn instead of block
   ```

2. **Restart** (2 min):
   ```bash
   beigebox dial
   ```

3. **Investigate** (next 30 min):
   - Check logs for patterns in false positives
   - Compare recent corpus changes
   - Check if embedding model version changed

4. **Root Cause Analysis**:
   - Did corpus grow significantly?
   - Did embedding model change?
   - New document types introduced?
   - Legitimate but unusual embeddings added?

5. **Fix Options**:
   - **Option A**: Recalibrate baseline on new corpus
   - **Option B**: Reduce sensitivity (e.g., 0.95 → 0.92)
   - **Option C**: Adjust min_norm / max_norm ranges
   - **Option D**: Add corpus segment to allowlist

6. **Retest**:
   - Collect new test data
   - Re-run tuning script
   - Verify metrics before re-enabling

---

## Configuration Checklist

### Before Stage 1

- [ ] Baseline calibration complete (`config/baseline.json`)
- [ ] Threshold tuning complete (`config/recommended_config.yaml`)
- [ ] Recommended sensitivity value reviewed and approved
- [ ] Alert monitoring set up (`beigebox quarantine stats` daily)

### Before Stage 2

- [ ] Stage 1 completed with FPR <0.5%
- [ ] 3 days of clean monitoring data
- [ ] Security team approval
- [ ] `detection_mode: quarantine` in config

### Before Stage 3

- [ ] Stage 2 stable for 7+ days
- [ ] Zero false positives in manual review
- [ ] Business stakeholders confirmed
- [ ] Incident response plan reviewed

---

## Monitoring & Alerting

### KPIs to Track

| Metric | Good | Warn | Alert |
|--------|------|------|-------|
| FPR (False Pos Rate) | <0.5% | 0.5–1% | >2% |
| TPR (True Pos Rate) | >95% | 90–95% | <90% |
| Latency Impact | <5ms | 5–10ms | >10ms |
| Daily Blocks | 0–2 | 2–5 | >10 |

### Automated Alerts (Optional)

Configure alerts in your monitoring system:

```yaml
# Example: Prometheus alert
- alert: RAGPoisoningFPRHigh
  expr: rag_detection_false_positive_rate > 0.02
  for: 5m
  annotations:
    summary: "RAG detection FPR exceeds 2%"
    runbook: "See docs/DEPLOYMENT_RAG_DEFENSE.md"
```

### Manual Checks

```bash
# Daily (5 min)
beigebox quarantine stats

# Weekly (15 min)
beigebox quarantine list --limit 20  # review recent blocks
python scripts/calibrate_embedding_baseline.py --chroma-path ./data/chroma --output config/baseline_latest.json

# Monthly (30 min)
# Full metrics review + sensitivity tuning
```

---

## Common Issues & Troubleshooting

### Issue: FPR Suddenly Spikes

**Symptoms:**
- Legitimate embeddings flagged at high rate
- Sharp increase in quarantine blocks

**Possible Causes:**
1. Corpus changed significantly (new document type, language, etc.)
2. Embedding model updated
3. ChromaDB collection corrupted or modified

**Fix:**
1. Check corpus change date in logs
2. Compare baseline before/after change
3. Recalibrate: `python scripts/calibrate_embedding_baseline.py`
4. If embedding model changed, get new test data and re-tune

### Issue: Not Detecting Known Poisoned Embeddings (FN)

**Symptoms:**
- TPR low (<90%)
- Known attack vectors not flagged

**Possible Causes:**
1. Sensitivity too high (too strict)
2. Baseline statistics don't match test set
3. Attack vector doesn't trigger norm-based detection

**Fix:**
1. Lower sensitivity: 0.95 → 0.92 in config
2. Recalibrate baseline
3. Re-run threshold tuning
4. If still failing, consider enabling additional layers (neighbor detection)

### Issue: System Latency Increased

**Symptoms:**
- Requests taking >50ms more
- Embedding storage slower

**Possible Causes:**
1. Detection enabled with very low sensitivity (expensive computation)
2. Baseline window too large

**Fix:**
1. Check `baseline_window` (default 1000, reduce to 500 if needed)
2. Verify sensitivity not too low (<0.90)
3. Monitor CPU/memory during quarantine checks

### Issue: False Positives in Review

**Symptoms:**
- Quarantine list contains clearly legitimate embeddings
- Users reporting blocked valid content

**Possible Causes:**
1. Legitimate corpus contains outliers (non-standard embeddings)
2. Sensitivity too aggressive
3. min_norm / max_norm ranges too tight

**Fix:**
1. Temporarily move to `detection_mode: warn` to observe
2. Collect 100 false positives
3. Analyze their characteristics (norm, dimensions)
4. Adjust ranges or re-tune sensitivity
5. Consider creating allowlist for specific corpus segments

---

## Appendix: Configuration Reference

```yaml
rag_poisoning_detection:
  # Master enable/disable
  enabled: true
  
  # Sensitivity (0.90–0.99, higher = fewer false alarms, more misses)
  sensitivity: 0.95
  
  # Detection mode: off, warn, quarantine, strict
  #   off:       disabled
  #   warn:      log suspicious, store anyway
  #   quarantine: reject suspicious (don't store)
  #   strict:    raise error, reject entire request
  detection_mode: warn  # Stage 1
  
  # Baseline statistics window size (rolling)
  baseline_window: 1000
  
  # Embedding magnitude safe range
  min_norm: 0.1      # too small = likely poisoned
  max_norm: 100.0    # too large = likely poisoned
  
  # Advanced (optional, Stage 3)
  enable_neighborhood_detection: false
  enable_dimension_analysis: false
```

---

## Support & Escalation

**Questions?** Contact: security-team@company.com  
**Emergency rollback?** Disable detection immediately: `enabled: false`  
**Long-term tuning?** Run `tune_thresholds.py` weekly with production data

