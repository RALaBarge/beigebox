# RAG Poisoning Defense — Operations Runbook

**Version:** 1.0  
**Audience:** On-call engineers, DevOps, security team  
**Last Updated:** 2026-04-12

## Quick Reference

| Task | Command | Frequency | Time |
|------|---------|-----------|------|
| Check health | `beigebox quarantine stats` | Daily | 2 min |
| Review blocks | `beigebox quarantine list` | Daily | 3 min |
| Recalibrate | `python scripts/calibrate_embedding_baseline.py` | Weekly | 5 min |
| Full review | Manual analysis of quarantine log | Monthly | 30 min |
| Emergency disable | Edit `config.yaml: enabled: false` | On-demand | 1 min |

---

## Daily Operations (5 min)

### 9:00 AM: Run Daily Health Check

```bash
#!/bin/bash
# Save as: scripts/daily_rag_check.sh

echo "=== RAG Defense Daily Check ==="
echo "Time: $(date)"
echo ""

echo "1. Checking quarantine statistics..."
beigebox quarantine stats

echo ""
echo "2. Last 10 quarantine blocks..."
beigebox quarantine list --limit 10 --sort time --desc

echo ""
echo "3. Checking for FPR spike..."
# Parse output and alert if FPR > 2%
# (Integrate with your monitoring system)

echo "=== End Daily Check ==="
```

Run this daily:
```bash
chmod +x scripts/daily_rag_check.sh
./scripts/daily_rag_check.sh > /tmp/rag_daily.log 2>&1
```

### Alert Conditions

**IMMEDIATE ACTION** if any of these occur:

1. **FPR (False Positive Rate) > 2%**
   - Action: Disable detection immediately
   - ```bash
     # In config.yaml, set:
     detection_mode: warn  # temporarily downgrade
     # OR
     enabled: false  # full disable
     ```
   - Then investigate root cause (see troubleshooting)

2. **TPR (True Positive Rate) < 90%**
   - Action: Reduce sensitivity
   - ```bash
     # In config.yaml, lower:
     sensitivity: 0.92  # was 0.95
     ```
   - Restart: `beigebox dial`

3. **Quarantine blocks spike >5x normal**
   - Action: Check logs for patterns
   - ```bash
     tail -100 logs/beigebox.log | grep quarantine
     ```

---

## Weekly Operations (15 min)

### Monday 10:00 AM: Recalibrate Baseline

```bash
#!/bin/bash
# Save as: scripts/weekly_rag_calibrate.sh

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BASELINE_NEW="config/baseline_${TIMESTAMP}.json"
BASELINE_CURRENT="config/baseline.json"

echo "=== RAG Defense Weekly Calibration ==="
echo "Time: $(date)"
echo ""

echo "1. Collecting baseline from production corpus..."
python scripts/calibrate_embedding_baseline.py \
  --chroma-path ./data/chroma \
  --output "$BASELINE_NEW" \
  --samples 200

if [ $? -ne 0 ]; then
  echo "ERROR: Baseline calibration failed"
  exit 1
fi

echo ""
echo "2. Comparing with previous baseline..."
python -c "
import json
with open('$BASELINE_CURRENT') as f1, open('$BASELINE_NEW') as f2:
  old = json.load(f1)
  new = json.load(f2)
  old_mean = old['statistics']['norm']['mean']
  new_mean = new['statistics']['norm']['mean']
  drift = abs(new_mean - old_mean) / old_mean * 100
  print(f'Mean norm drift: {drift:.1f}%')
  if drift > 10:
    print('WARNING: Significant drift detected (>10%)')
    print('Consider re-tuning thresholds')
"

echo ""
echo "3. Backup old baseline..."
cp "$BASELINE_CURRENT" "${BASELINE_CURRENT}.bak"

echo "4. Activate new baseline..."
cp "$BASELINE_NEW" "$BASELINE_CURRENT"

echo ""
echo "=== End Weekly Calibration ==="
```

Run this weekly:
```bash
chmod +x scripts/weekly_rag_calibrate.sh
./scripts/weekly_rag_calibrate.sh | tee -a logs/rag_calibration.log
```

### Weekly Task Checklist

- [ ] Baseline recalibrated
- [ ] No significant drift (>10%) detected
- [ ] Review top 5 quarantine blocks manually
- [ ] No false positives identified
- [ ] Check if corpus size changed
- [ ] Verify detection latency <10ms (avg)

---

## Monthly Operations (30 min)

### First Friday of Month: Full Metrics Review

```bash
#!/bin/bash
# Save as: scripts/monthly_rag_review.sh

echo "=== RAG Defense Monthly Metrics Review ==="
echo "Date: $(date)"
echo ""

echo "SECTION 1: Quarantine Statistics"
echo "=================================="
beigebox quarantine stats

echo ""
echo "SECTION 2: Recent Activity (Last 30 Days)"
echo "========================================="
beigebox quarantine list --limit 50 --sort time | tee /tmp/rag_monthly_blocks.txt

echo ""
echo "SECTION 3: Detector Baseline"
echo "============================"
python -c "
import json
with open('config/baseline.json') as f:
  baseline = json.load(f)
  stats = baseline['statistics']['norm']
  print(f\"Collected: {baseline['collected_at']}\")
  print(f\"Samples: {baseline['sample_count']}\")
  print(f\"Mean norm: {stats['mean']:.4f}\")
  print(f\"Std norm: {stats['std']:.4f}\")
  print(f\"Range: [{stats['min']:.4f}, {stats['max']:.4f}]\")
"

echo ""
echo "SECTION 4: Current Configuration"
echo "=================================="
python -c "
import yaml
with open('config.yaml') as f:
  cfg = yaml.safe_load(f)
  rag = cfg.get('rag_poisoning_detection', {})
  print(f\"Enabled: {rag.get('enabled')}\")
  print(f\"Sensitivity: {rag.get('sensitivity')}\")
  print(f\"Mode: {rag.get('detection_mode')}\")
  print(f\"Min norm: {rag.get('min_norm')}\")
  print(f\"Max norm: {rag.get('max_norm')}\")
"

echo ""
echo "SECTION 5: System Impact"
echo "========================"
echo "Check logs for latency impact..."
grep -i "poison.*duration\|poison.*ms" logs/beigebox.log | tail -20 || echo "No latency logs found"

echo ""
echo "=== End Monthly Review ==="
```

Run this monthly:
```bash
chmod +x scripts/monthly_rag_review.sh
./scripts/monthly_rag_review.sh | tee -a logs/rag_monthly_review_$(date +%Y%m).log
```

### Monthly Checklist

- [ ] Review all quarantine blocks (false positives?)
- [ ] Verify no systematic issues
- [ ] Corpus growth reasonable?
- [ ] Sensitivity adjustment needed?
- [ ] Document findings in `logs/rag_monthly_review_YYYYMM.log`
- [ ] Plan any re-tuning or adjustments

---

## Troubleshooting Guide

### Problem: High False Positive Rate (>2%)

**Diagnosis:**
```bash
beigebox quarantine stats
# If quarantine_count > 2% of total embeddings, investigate

# Review recent blocks
beigebox quarantine list --limit 20 --sort time --desc
# Sample 5–10 blocks, assess if legitimate
```

**Root Causes & Fixes:**

| Cause | Detection | Fix |
|-------|-----------|-----|
| Sensitivity too low | FPR >2% consistently | Increase sensitivity: 0.92 → 0.95 |
| Corpus changed | FPR spike after [date] | Recalibrate baseline |
| Embedding model updated | FPR spike + different distribution | Collect new test data, re-tune |
| Outlier corpus segment | FPR in specific content type | Create allowlist or adjust ranges |

**Quick Fix (Temporary):**
```bash
# Reduce detection_mode temporarily
# config.yaml:
detection_mode: warn  # downgrade from quarantine

# Then restart
beigebox dial

# Then investigate root cause (see above)
```

### Problem: Missed Poisoned Embeddings (Low TPR)

**Diagnosis:**
```bash
# Check TPR against test set
python scripts/tune_thresholds.py \
  --baseline config/baseline.json \
  --test-legit test_legit.json \
  --test-poison test_poison.json \
  --output /tmp/tuning_check.yaml
# If TPR < 90%, sensitivity is too aggressive
```

**Root Causes & Fixes:**

| Cause | Detection | Fix |
|--------|-----------|-----|
| Sensitivity too high | TPR <90% on test data | Lower sensitivity: 0.98 → 0.95 |
| Baseline stale | TPR degraded over time | Recalibrate baseline weekly |
| Attack vector changed | New attacks not detected | Analyze attack characteristics, adjust ranges |
| Embedding model drift | Different distribution | Retrain baseline classifier |

**Quick Fix:**
```bash
# Reduce sensitivity to catch more
# config.yaml:
sensitivity: 0.92  # was 0.95

# Restart
beigebox dial

# Then re-tune properly
```

### Problem: System Latency Impact

**Diagnosis:**
```bash
# Check recent logs for latency metrics
grep -i "poison\|quarantine" logs/beigebox.log | grep "duration\|latency\|ms" | tail -50

# If detection adding >10ms per request, investigate
```

**Root Causes & Fixes:**

| Cause | Detection | Fix |
|-------|-----------|-----|
| Large baseline_window | Processing slow | Reduce: baseline_window: 500 |
| Low sensitivity | More computation | Increase: sensitivity: 0.98 |
| High corpus volume | ChromaDB query slow | Query fewer embeddings |

**Quick Fix:**
```bash
# Disable detection temporarily while investigating
# config.yaml:
enabled: false

# Then debug and re-enable
```

### Problem: Configuration Not Applying

**Diagnosis:**
```bash
# Verify file syntax
python -c "import yaml; yaml.safe_load(open('config.yaml'))" && echo "OK" || echo "SYNTAX ERROR"

# Check if loaded correctly
tail -20 logs/beigebox.log | grep -i "config\|rag"
```

**Fix:**
```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# Restart BeigeBox
beigebox dial

# Verify in logs
grep "RAGPoisoningDetector initialized" logs/beigebox.log
```

### Problem: ChromaDB Connection Error

**Diagnosis:**
```bash
python -c "
import chromadb
from pathlib import Path
path = Path('./data/chroma')
print(f'Path exists: {path.exists()}')
try:
  client = chromadb.PersistentClient(path=str(path))
  collection = client.get_collection('conversations')
  print(f'Collection count: {collection.count()}')
except Exception as e:
  print(f'ERROR: {e}')
"
```

**Fix:**
```bash
# If corrupted, backup and recreate
mv ./data/chroma ./data/chroma.backup
mkdir -p ./data/chroma
# Restart server
beigebox dial
```

---

## Emergency Procedures

### Emergency: Disable Detection

**Use if:** FPR >2% and affecting users

```bash
# Step 1: Edit config.yaml
# Change:
#   enabled: false

# Step 2: Restart immediately
beigebox dial

# Step 3: Verify disabled
tail -5 logs/beigebox.log | grep -i rag

# Step 4: Notify team
# email security-team@company.com with alert timestamp
```

**Time to disable:** <5 minutes

### Emergency: Rollback to Previous Baseline

**Use if:** Baseline calibration caused issues

```bash
# Step 1: Restore previous baseline
cp config/baseline.json.bak config/baseline.json

# Step 2: Restart
beigebox dial

# Step 3: Verify
beigebox quarantine stats
```

**Time to rollback:** <5 minutes

### Emergency Hotline

- **On-call engineer:** [escalation contact]
- **Security team:** security-team@company.com
- **Incident channel:** #security-incidents Slack

---

## Scheduled Maintenance

### Weekly Maintenance Window (Monday 2 AM UTC)

```
- Recalibrate baseline (5 min)
- Full metrics review (10 min)
- Any necessary config adjustments (5 min)
- Total: ~20 min downtime (if restarting)
```

### Monthly Maintenance (First Friday, 10 AM UTC)

```
- Full metrics review (30 min)
- Sensitivity tuning if needed (30 min)
- Documentation update (15 min)
- Total: ~1.25 hours (no downtime required)
```

---

## Logging & Auditing

### Log Locations

| Log | Purpose | Retention |
|-----|---------|-----------|
| `logs/beigebox.log` | Main application log | 30 days |
| `logs/rag_calibration.log` | Weekly calibration runs | 90 days |
| `logs/rag_monthly_review_YYYYMM.log` | Monthly reviews | 12 months |
| `data/chroma/` | Quarantine storage | Until manually purged |

### Audit Trail

Every quarantine event should be logged with:
```json
{
  "timestamp": "2026-04-12T10:00:00Z",
  "event_type": "quarantine_flag",
  "embedding_id": "conv_123_msg_456",
  "norm": 15.234,
  "z_score": 3.45,
  "reason": "Embedding magnitude anomaly",
  "confidence": 0.95,
  "action": "quarantine"
}
```

### Retrieval

```bash
# View recent events
beigebox quarantine list --limit 100 --sort time --desc

# Export for analysis
beigebox quarantine export --start 2026-04-01 --end 2026-04-30 > quarantine_april.json
```

---

## Knowledge Base

### Understanding FPR vs TPR

- **False Positive Rate (FPR)**: % of legitimate embeddings incorrectly flagged
  - **Goal:** <0.5% (fewer false alarms)
  - **Action if high:** Lower sensitivity or recalibrate

- **True Positive Rate (TPR)**: % of poisoned embeddings correctly detected
  - **Goal:** >95% (catch attacks)
  - **Action if low:** Raise sensitivity or adjust ranges

### When to Re-Tune

**Trigger re-tuning if:**
1. FPR drifts >1% from baseline
2. Corpus size changes >20%
3. Embedding model version changes
4. New document types added
5. TPR drops below 90%

**Process:**
```bash
# 1. Collect new test data
python scripts/calibrate_embedding_baseline.py --samples 200

# 2. Prepare test sets (legit + poison)
# ... [create test data]

# 3. Re-tune
python scripts/tune_thresholds.py \
  --baseline config/baseline.json \
  --test-legit test_legit.json \
  --test-poison test_poison.json

# 4. Review recommended config
# 5. Update config.yaml with new sensitivity
# 6. Restart and monitor
```

### Configuration Best Practices

1. **Version control config.yaml changes**
   ```bash
   git diff config.yaml  # before restarting
   ```

2. **Backup baseline.json weekly**
   ```bash
   cp config/baseline.json config/baseline_backup_$(date +%Y%m%d).json
   ```

3. **Test changes in warn mode first**
   ```yaml
   detection_mode: warn  # before quarantine
   ```

4. **Monitor after any change**
   ```bash
   tail -f logs/beigebox.log | grep -i poison
   ```

---

## Contacts & Escalation

| Role | Contact | On-Call |
|------|---------|---------|
| Ops Lead | ops-lead@company.com | Yes (PagerDuty) |
| Security Team | security-team@company.com | Yes |
| Platform Team | platform@company.com | Yes |
| Data Science | ml-team@company.com | No |

---

## Appendix: Useful Commands

### Check Status
```bash
beigebox quarantine stats
```

### View Blocks
```bash
beigebox quarantine list --limit 20 --sort time --desc
```

### Recalibrate
```bash
python scripts/calibrate_embedding_baseline.py --chroma-path ./data/chroma --output config/baseline.json
```

### Re-Tune
```bash
python scripts/tune_thresholds.py --baseline config/baseline.json --test-legit test_legit.json --test-poison test_poison.json
```

### Search Logs
```bash
grep -i "quarantine\|poison" logs/beigebox.log
```

### Disable Detection
```bash
# Edit config.yaml:
# rag_poisoning_detection:
#   enabled: false
beigebox dial
```

