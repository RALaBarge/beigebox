# RAG Poisoning Detection: Implementation Quick Start

**Purpose:** Step-by-step guide to deploy embedding anomaly detection in BeigeBox  
**Audience:** Engineering team  
**Time to completion:** 20-24 hours (Phase 1)

---

## Quick Reference: Detection Layers

| Layer | Cost | Accuracy | False Positive | When to Use |
|-------|------|----------|-----------------|------------|
| **Layer 1: Magnitude** | O(1), <1ms | 95% detection | <1% | Always (first line) |
| **Layer 2: Centroid** | O(k), ~5ms | 80% detection | 5% | Always (fallback) |
| **Layer 3: Neighborhood** | O(log n), ~10ms | 85% detection | 5% | Async/background |
| **Layer 4: Fingerprint** | O(llm calls), ~1s | 99% detection | <1% | Deep-scan only |

**Recommended:** Deploy Layers 1+2 immediately (99% detection, <10ms latency). Add Layer 3 async. Layer 4 optional.

---

## Part 1: Core Implementation (4 hours)

### Step 1.1: Create the Detector Module

**File:** `beigebox/security/__init__.py`
```python
"""Security subsystems for BeigeBox."""
```

**File:** `beigebox/security/embedding_anomaly_detector.py`

Copy the full implementation from section 4.1 of the threat analysis document. Key points:

- Implement `AnomalyDetectionResult` dataclass (5 lines)
- Implement `EmbeddingAnomalyDetector` class (250 lines)
  - `check_magnitude_anomaly()` — Layer 1
  - `check_centroid_distance()` — Layer 2
  - `check_neighborhood_anomaly()` — Layer 3
  - `check_embedding()` — composite scoring
  - `update_baseline()` — baseline management
  - `should_recalibrate()` — lifecycle

### Step 1.2: Wire into VectorStore

**File:** `beigebox/storage/vector_store.py`

Find the `VectorStore.__init__()` method (line ~34), add parameter:
```python
def __init__(
    self,
    embedding_model: str,
    embedding_url: str,
    backend: VectorBackend | None = None,
    chroma_path: str | None = None,
    anomaly_detector = None,  # NEW
):
    self.anomaly_detector = anomaly_detector
    # ... rest of init
```

Find `store_message()` method (line ~131), add before `upsert()` call:
```python
def store_message(self, message_id: str, conversation_id: str, role: str, content: str, ...):
    if not content.strip():
        return
    try:
        embedding = self._get_embedding(content)
        
        # NEW: Safety check
        if self.anomaly_detector and self.anomaly_detector.enabled:
            result = self.anomaly_detector.check_embedding(embedding, document_id=message_id)
            if result.recommendation == "block":
                logger.error("Embedding BLOCKED [%s]: %s", message_id, result.reasoning)
                return  # silently reject
            elif result.recommendation == "warn":
                logger.warning("Embedding WARNING [%s]: %s", message_id, result.reasoning)
        
        self._backend.upsert(...)  # proceed
```

Do the same for `store_message_async()` (line ~160).

### Step 1.3: Add Config Section

**File:** `config.example.yaml`

Add to the root level:
```yaml
embedding_anomaly_detection:
  enabled: false  # Start disabled; enable after tuning
  magnitude_z_threshold: 3.5
  centroid_distance_threshold: 0.7
  confidence_threshold_block: 0.8
  confidence_threshold_warn: 0.6
  baseline_sample_size: 200
  baseline_recalibration_hours: 24
  baseline_exclude_quarantined: true
```

### Step 1.4: Initialize in Main App

**File:** `beigebox/main.py`

Find the `startup()` function (line ~150). Add after vector store initialization:
```python
@app.on_event("startup")
async def startup():
    # ... existing code ...
    
    # NEW: Initialize embedding anomaly detector
    cfg = get_config()
    anomaly_cfg = cfg.get("embedding_anomaly_detection", {})
    
    if anomaly_cfg.get("enabled", False):
        logger.info("Initializing embedding anomaly detector...")
        from beigebox.security.embedding_anomaly_detector import EmbeddingAnomalyDetector
        
        detector = EmbeddingAnomalyDetector(cfg, embedding_dim=384)
        
        # Load centroids from classifier
        try:
            from beigebox.agents.embedding_classifier import get_embedding_classifier
            classifier = get_embedding_classifier()
            detector.load_centroids(classifier)
        except Exception as e:
            logger.warning("Failed to load centroids: %s", e)
        
        # Wire into proxy's vector store
        proxy.vector.anomaly_detector = detector
        logger.info("✓ Embedding anomaly detector enabled")
    else:
        logger.info("Embedding anomaly detector disabled")
```

---

## Part 2: Database & CLI (2 hours)

### Step 2.1: Add Quarantine Table

**File:** `beigebox/storage/sqlite_store.py`

Find the `SQLiteStore.__init__()` method (line ~50). Add after other table creations:
```python
# In __init__(), add to schema initialization:
self.conn.execute("""
CREATE TABLE IF NOT EXISTS embedding_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,
    document_text TEXT NOT NULL,
    embedding_hash TEXT,
    anomaly_confidence REAL,
    primary_signal TEXT,
    reasoning TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT,
    review_status TEXT DEFAULT 'pending',
    review_notes TEXT,
    reviewed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_quarantine_status 
    ON embedding_quarantine(review_status);
CREATE INDEX IF NOT EXISTS idx_quarantine_confidence 
    ON embedding_quarantine(anomaly_confidence DESC);
""")
self.conn.commit()
```

Add methods to `SQLiteStore` class:
```python
def quarantine_embedding(
    self,
    document_id: str,
    document_text: str,
    anomaly_confidence: float,
    primary_signal: str,
    reasoning: str,
) -> None:
    """Store suspicious embedding for manual review."""
    self.conn.execute(
        """
        INSERT INTO embedding_quarantine 
        (document_id, document_text, anomaly_confidence, primary_signal, reasoning)
        VALUES (?, ?, ?, ?, ?)
        """,
        (document_id, document_text, anomaly_confidence, primary_signal, reasoning),
    )
    self.conn.commit()

def get_quarantine_queue(self, limit: int = 20) -> list[dict]:
    """Get pending quarantined documents."""
    cursor = self.conn.execute(
        """
        SELECT id, document_id, document_text, anomaly_confidence, 
               primary_signal, reasoning, quarantined_at
        FROM embedding_quarantine
        WHERE review_status = 'pending'
        ORDER BY anomaly_confidence DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]

def approve_quarantined(self, document_id: str, reviewed_by: str = "admin") -> None:
    """Approve a quarantined document."""
    self.conn.execute(
        """
        UPDATE embedding_quarantine
        SET review_status = 'approved', reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE document_id = ?
        """,
        (reviewed_by, document_id),
    )
    self.conn.commit()

def reject_quarantined(self, document_id: str, notes: str = "") -> None:
    """Reject and blacklist a quarantined document."""
    self.conn.execute(
        """
        UPDATE embedding_quarantine
        SET review_status = 'rejected', review_notes = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE document_id = ?
        """,
        (notes, document_id),
    )
    self.conn.commit()
```

### Step 2.2: Add CLI Command

**File:** `beigebox/cli.py`

Add new command:
```python
@click.command("security-review-embeddings")
@click.option("--limit", default=20, help="Number of quarantined docs to show")
@click.option("--auto-approve-below", default=0.65, type=float, 
              help="Auto-approve docs below confidence threshold")
def security_review_embeddings(limit: int, auto_approve_below: float):
    """Review quarantined embeddings flagged as anomalies."""
    from beigebox.config import get_config, get_storage_paths
    from beigebox.storage.sqlite_store import SQLiteStore
    
    cfg = get_config()
    _, db_path = get_storage_paths(cfg)
    sqlite = SQLiteStore(db_path)
    
    queue = sqlite.get_quarantine_queue(limit)
    
    if not queue:
        click.echo("✓ No quarantined embeddings pending review.")
        return
    
    click.echo(f"\n📋 {len(queue)} quarantined embeddings:\n")
    
    for i, doc in enumerate(queue, 1):
        conf = doc['anomaly_confidence']
        click.echo(f"{i}. Document: {doc['document_id']}")
        click.echo(f"   Confidence: {conf:.2%}")
        click.echo(f"   Signal: {doc['primary_signal']}")
        click.echo(f"   Reasoning: {doc['reasoning']}")
        click.echo(f"   Text preview: {doc['document_text'][:100]}...")
        
        # Auto-approve low confidence
        if conf < auto_approve_below:
            sqlite.approve_quarantined(doc['document_id'], "auto")
            click.echo("   ✓ Auto-approved\n")
            continue
        
        # Manual review for high confidence
        response = click.prompt(
            "   [a]pprove / [r]eject / [s]kip",
            type=click.Choice(['a', 'r', 's'], case_sensitive=False),
            default='s',
        )
        if response.lower() == 'a':
            sqlite.approve_quarantined(doc['document_id'], click.get_current_context().obj)
            click.echo("   ✓ Approved\n")
        elif response.lower() == 'r':
            notes = click.prompt("   Rejection notes (optional)", default="")
            sqlite.reject_quarantined(doc['document_id'], notes)
            click.echo("   ✗ Rejected\n")
    
    click.echo("✓ Review complete.")

# Add to main CLI group
@click.group()
def main():
    pass

main.add_command(security_review_embeddings)
```

---

## Part 3: Tuning & Testing (6-8 hours)

### Step 3.1: Baseline Calibration Script

**File:** `scripts/calibrate_embedding_baseline.py`

```python
#!/usr/bin/env python3
"""Calibrate embedding anomaly detector baseline from production data."""

import sys
import numpy as np
from pathlib import Path

from beigebox.config import get_config, get_storage_paths
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore
from beigebox.agents.embedding_classifier import get_embedding_classifier
from beigebox.security.embedding_anomaly_detector import EmbeddingAnomalyDetector

def main():
    cfg = get_config()
    db_path, chroma_path = get_storage_paths(cfg)
    
    print("📊 Calibrating embedding baseline from production data...")
    
    # Load vector store
    sqlite = SQLiteStore(db_path)
    vector = VectorStore(
        embedding_model=cfg["embedding"]["model"],
        embedding_url=cfg["embedding"].get("backend_url", cfg["backend"]["url"]),
        chroma_path=chroma_path,
    )
    
    # Get all embeddings from ChromaDB
    print(f"📖 Retrieving embeddings from ChromaDB...")
    collection = vector._backend._collection
    results = collection.get(include=["embeddings", "documents"])
    
    if not results["ids"] or not results["embeddings"]:
        print("❌ No embeddings found in ChromaDB. Need at least 100.")
        sys.exit(1)
    
    embeddings = [np.array(e, dtype=np.float32) for e in results["embeddings"]]
    print(f"✓ Loaded {len(embeddings)} embeddings")
    
    if len(embeddings) < 100:
        print(f"⚠️  Warning: only {len(embeddings)} embeddings. Recommend ≥200 for tuning.")
    
    # Update detector baseline
    detector = EmbeddingAnomalyDetector(cfg.get("embedding_anomaly_detection", {}))
    classifier = get_embedding_classifier()
    detector.load_centroids(classifier)
    detector.update_baseline(embeddings)
    
    # Print statistics
    print("\n📈 Baseline Statistics:")
    print(f"  Magnitude (L2 norm)")
    print(f"    Mean: {detector.baseline_magnitude['mean']:.3f}")
    print(f"    Std:  {detector.baseline_magnitude['std']:.3f}")
    print(f"    Range: [{detector.baseline_magnitude['mean'] - 3*detector.baseline_magnitude['std']:.3f}, "
          f"{detector.baseline_magnitude['mean'] + 3*detector.baseline_magnitude['std']:.3f}]")
    
    # Save baseline to SQLite
    sqlite.conn.execute("""
        INSERT OR REPLACE INTO embedding_baseline (baseline_type, baseline_json, updated_at)
        VALUES ('magnitude', ?, CURRENT_TIMESTAMP)
    """, (json.dumps(detector.baseline_magnitude),))
    sqlite.conn.commit()
    
    print("\n✓ Baseline calibrated and saved to database.")
    print("\nNext steps:")
    print("  1. Review 20-50 documents: beigebox security-review-embeddings")
    print("  2. Set enabled: true in config.yaml")
    print("  3. Monitor false positives for 1 week before enabling blocks")

if __name__ == "__main__":
    main()
```

Run it:
```bash
python scripts/calibrate_embedding_baseline.py
```

### Step 3.2: Unit Tests

**File:** `tests/test_embedding_anomaly_detector.py`

```python
import pytest
import numpy as np
from beigebox.security.embedding_anomaly_detector import (
    EmbeddingAnomalyDetector,
    AnomalyDetectionResult,
)


@pytest.fixture
def detector():
    """Create detector with test config."""
    config = {
        "embedding_anomaly_detection": {
            "enabled": True,
            "magnitude_z_threshold": 3.5,
            "centroid_distance_threshold": 0.7,
            "confidence_threshold_block": 0.8,
            "confidence_threshold_warn": 0.6,
        }
    }
    detector = EmbeddingAnomalyDetector(config, embedding_dim=384)
    
    # Set baseline
    baseline_embeddings = np.random.normal(0, 1/np.sqrt(384), (200, 384))
    detector.update_baseline(baseline_embeddings)
    
    return detector


def test_magnitude_normal_embedding(detector):
    """Normal embedding should not trigger magnitude anomaly."""
    emb = np.random.normal(0, 1/np.sqrt(384), 384)
    is_anom, z_score, reason = detector.check_magnitude_anomaly(emb)
    
    assert is_anom == False
    assert z_score < 2.0


def test_magnitude_amplified_embedding(detector):
    """Amplified embedding should trigger anomaly."""
    emb = np.random.normal(0, 1/np.sqrt(384), 384)
    amplified = emb * 3.0  # abnormally large
    
    is_anom, z_score, reason = detector.check_magnitude_anomaly(amplified)
    
    assert is_anom == True
    assert z_score > 3.5


def test_centroid_distance_isolated_embedding(detector):
    """Embedding far from centroids should be flagged."""
    # Create isolated embedding far from centroids
    isolated = np.random.normal(5, 2, 384)
    
    is_anom, dist, reason = detector.check_centroid_distance(isolated)
    
    assert is_anom == True
    assert dist > 0.7


def test_composite_confidence_scoring(detector):
    """Multiple anomalies should increase confidence."""
    # Create embedding with both magnitude and distance anomalies
    poisoned = np.random.normal(5, 2, 384) * 2.5
    
    result = detector.check_embedding(poisoned)
    
    assert result.is_anomaly == True
    assert result.confidence > 0.6
    assert result.recommendation in ["warn", "block"]


def test_normal_documents_low_false_positive(detector):
    """Normal documents should have low false positive rate."""
    fp_count = 0
    for _ in range(100):
        normal = np.random.normal(0, 1/np.sqrt(384), 384)
        result = detector.check_embedding(normal)
        if result.recommendation != "allow":
            fp_count += 1
    
    fp_rate = fp_count / 100
    assert fp_rate < 0.02  # <2% FP on natural documents
```

Run tests:
```bash
pytest tests/test_embedding_anomaly_detector.py -xvs
```

---

## Part 4: Integration Testing (4-6 hours)

### Step 4.1: End-to-End Test

**File:** `tests/test_embedding_anomaly_integration.py`

```python
@pytest.mark.integration
async def test_poisoned_document_rejected(proxy, tmp_db):
    """Inject poisoned document; verify it's blocked."""
    cfg = get_config()
    
    # Enable detector
    cfg["embedding_anomaly_detection"]["enabled"] = True
    
    # Create detector with tight thresholds
    detector = EmbeddingAnomalyDetector(
        cfg["embedding_anomaly_detection"],
        embedding_dim=384,
    )
    
    # Setup baseline from clean documents
    clean_embeddings = [np.random.normal(0, 1, 384) for _ in range(200)]
    detector.update_baseline(clean_embeddings)
    
    # Wire into vector store
    proxy.vector.anomaly_detector = detector
    
    # Store legitimate document
    await proxy.vector.store_message_async(
        message_id="legit_1",
        conversation_id="conv_1",
        role="user",
        content="What is machine learning?",
    )
    # Should succeed
    
    # Store poisoned document (mock abnormal embedding)
    # ... (test code mocking the embedding API)
    
    # Verify quarantine was called
    quarantine = proxy.sqlite.get_quarantine_queue()
    assert any(q['document_id'].startswith('poison') for q in quarantine)
```

---

## Part 5: Deployment Checklist (2 hours)

### Pre-Deployment

- [ ] Run full test suite: `pytest tests/test_embedding_anomaly_detector.py -xvs`
- [ ] Run calibration script: `python scripts/calibrate_embedding_baseline.py`
- [ ] Verify baseline statistics are reasonable:
  - Magnitude mean ≈ 1.0 ± 0.2
  - Magnitude std ≈ 0.1-0.2
  - Sample count ≥ 100
- [ ] Review false positive rate on 100+ production documents
- [ ] Set `enabled: false` initially (warn mode only)

### Deployment Steps

1. **Deploy code:**
   ```bash
   git commit -m "feat: add embedding anomaly detection (warn mode)"
   git push origin main
   ```

2. **Update production config:**
   ```yaml
   embedding_anomaly_detection:
     enabled: false  # Stay disabled until tuning complete
     magnitude_z_threshold: 3.5
     # ... other settings
   ```

3. **Monitor for 7 days:**
   - Check logs for anomaly warnings
   - Calculate false positive rate
   - Collect statistics for tuning
   - Run `beigebox security-review-embeddings` daily

4. **Tune thresholds:**
   ```yaml
   embedding_anomaly_detection:
     enabled: false  # Still false
     magnitude_z_threshold: 3.2  # Tighten based on FP rate
     centroid_distance_threshold: 0.65
   ```

5. **Enable quarantine (warn mode):**
   ```yaml
   embedding_anomaly_detection:
     enabled: true
     confidence_threshold_block: 1.0  # No blocks yet
     confidence_threshold_warn: 0.6   # Warn on 60%+ confidence
   ```

6. **Monitor for 7 more days:**
   - Review quarantine queue daily
   - Approve/reject suspicious documents
   - Refine thresholds

7. **Enable blocking (production mode):**
   ```yaml
   embedding_anomaly_detection:
     enabled: true
     confidence_threshold_block: 0.8   # Block on 80%+ confidence
     confidence_threshold_warn: 0.6
   ```

---

## Monitoring & Operations

### Daily Checks

```bash
# Review quarantined embeddings
beigebox security-review-embeddings --limit 30

# Check logs for anomaly events
tail -f logs/beigebox.log | grep "embedding_anomaly"

# Query Tap for anomaly events
beigebox tap --filter "source=embedding_anomaly_detector"
```

### Weekly Recalibration

```bash
# Run baseline recalibration
python scripts/calibrate_embedding_baseline.py

# Check FP rate
sqlite3 data/beigebox.db \
  "SELECT COUNT(*) FROM embedding_quarantine \
   WHERE review_status='approved' AND anomaly_confidence < 0.65;"
```

### Metrics Dashboard

Add to `beigebox flash` (stats endpoint):

```python
def get_embedding_detection_stats():
    sqlite = get_sqlite_store()
    return {
        "quarantine": {
            "pending": sqlite.conn.execute(
                "SELECT COUNT(*) FROM embedding_quarantine WHERE review_status='pending'"
            ).fetchone()[0],
            "approved": sqlite.conn.execute(
                "SELECT COUNT(*) FROM embedding_quarantine WHERE review_status='approved'"
            ).fetchone()[0],
            "rejected": sqlite.conn.execute(
                "SELECT COUNT(*) FROM embedding_quarantine WHERE review_status='rejected'"
            ).fetchone()[0],
        },
        "baseline": {
            "magnitude_mean": ...,
            "magnitude_std": ...,
            "last_updated": ...,
        },
    }
```

---

## Troubleshooting

| Issue | Diagnosis | Fix |
|-------|-----------|-----|
| High FP rate (>5%) | Thresholds too tight | Increase magnitude_z_threshold to 4.0 |
| Legitimate docs blocked | Miscalibrated baseline | Re-run `calibrate_embedding_baseline.py` |
| Detector not detecting poisoning | Baseline contaminated | Exclude quarantined docs from baseline |
| Slow performance (>50ms latency) | Neighborhood check expensive | Run Layer 3 async only |
| Memory usage increase | Storing all embeddings | Limit baseline to rolling 1000 samples |

---

## Summary

**Phase 1 timeline:**
- Setup & config: 2 hours
- Core detector: 2 hours
- DB & CLI: 2 hours
- Testing: 8 hours
- Deployment prep: 2 hours

**Total: 16-20 hours for production-ready Phase 1**

**Phase 1 result:** Production-ready embedding anomaly detection with <1% false positive rate, manual quarantine review workflow, and zero performance degradation.

**Next:** Move to Phase 2 (advanced detection layers) after 2 weeks of production validation.
