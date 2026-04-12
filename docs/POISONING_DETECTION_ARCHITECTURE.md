# RAG Poisoning Detection: Generic Architecture

**Last Updated:** April 12, 2026  
**Author:** Security Research Team  
**Status:** Research Phase

---

## Executive Summary

A **vector-store-agnostic poisoning detection layer** is technically feasible and strategically valuable. The architecture can work across ChromaDB, Pinecone, Weaviate, Qdrant, Milvus, and pgvector by abstracting over three core primitives:

1. **Embedding retrieval** (semantic search)
2. **Metadata filtering** (document source, timestamp, tenant)
3. **Batch statistics** (collection-wide anomaly detection)

The main architectural challenge is **API fragmentation**, which can be solved via the Vextra abstraction pattern (arxiv 2601.06727) — minimal overhead (<5%), significant portability gain.

---

## Part 1: Generic Architecture Design

### 1.1 Core Abstraction Layers

```
┌─────────────────────────────────────────────────────┐
│     RAG Poisoning Detector (BeigeBox tool)         │
│   - Embedding anomaly detection                     │
│   - Isolation Forest / LOF clustering               │
│   - Cosine distance outliers                        │
│   - Statistical drift detection                     │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│   VectorStore Abstraction Interface                │
│                                                     │
│  • query(embedding, k, where_filter) → results    │
│  • batch_query(embeddings[], k) → results[]       │
│  • get_metadata(ids) → metadata[]                 │
│  • get_statistics() → collection_stats            │
│  • get_all_ids(where_filter) → id_list           │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│   Pluggable Backend Adapters                       │
│                                                     │
│  ├─ ChromaDBAdapter (Vextra pattern)              │
│  ├─ PineconeAdapter (namespace support)           │
│  ├─ WeaviateAdapter (GraphQL + hybrid)           │
│  ├─ QdrantAdapter (point-in-time lookups)        │
│  ├─ MilvusAdapter (collection partitioning)      │
│  ├─ pgvectorAdapter (SQL WHERE clauses)          │
│  └─ FAISSAdapter (in-memory only)                │
└─────────────────────────────────────────────────────┘
```

### 1.2 Detection Pipeline

```
1. Embedding Fetch
   ├─ Retrieve N recent embeddings + metadata (batch)
   ├─ Handle pagination for large collections
   └─ Timestamp-based ordering (freshness detection)

2. Statistical Profiling
   ├─ Compute centroid of collection
   ├─ Calculate per-embedding distance to centroid
   ├─ Measure embedding norm distribution
   ├─ Detect sudden shifts (drift detection)
   └─ Identify outliers via Isolation Forest / LOF

3. Semantic Coherence Check
   ├─ Query: "Summarize the main topics in this collection"
   ├─ Retrieve top-K documents per topic
   ├─ Measure semantic diversity (cosine distance variance)
   ├─ Flag documents that don't fit topic clusters
   └─ Detect sudden topical divergence

4. Metadata Anomalies
   ├─ Check for unusual source patterns
   ├─ Verify timestamp distribution (clustering?)
   ├─ Detect missing/null metadata fields
   ├─ Cross-check embeddings against document source
   └─ Identify orphaned or suspicious entries

5. Isolation Detection (High-Signal)
   ├─ Identify embeddings with unusually high cosine distance
   ├─ Check: Are they semantically isolated from the collection?
   ├─ Verify: Do they align with expected topics?
   ├─ Flag: High distance + low semantic coherence = high risk
   └─ Report: Threshold-based scoring (0-1 confidence)

6. Reporting & Triage
   ├─ Generate risk scores (per-document)
   ├─ Group by risk tier (critical, high, medium, low)
   ├─ Output: CSV / JSON with remediation hints
   ├─ Optional: Export to upstream tools (SIEM, audit logs)
   └─ Integration: Tap event logging for observability
```

### 1.3 Minimum Viable Interface

**VectorStoreAdapter (base class):**

```python
class VectorStoreAdapter(ABC):
    """Unified interface for poisoning detection across all vector DBs."""
    
    @abstractmethod
    async def get_embeddings_batch(
        self,
        limit: int = 1000,
        offset: int = 0,
        where_filter: dict | None = None
    ) -> dict:
        """Retrieve batch of embeddings with metadata.
        
        Returns:
            {
                "ids": ["id1", "id2"],
                "embeddings": [[...], [...]], 
                "documents": ["doc1", "doc2"],
                "metadatas": [{"source": "...", "ts": "..."}, ...]
            }
        """
        pass
    
    @abstractmethod
    async def query_k_nearest(
        self,
        embedding: list[float],
        k: int,
        where_filter: dict | None = None
    ) -> dict:
        """Find k nearest neighbors to an embedding.
        
        Returns:
            {
                "ids": ["id1", "id2"],
                "distances": [0.1, 0.15],
                "documents": ["doc1", "doc2"],
                "metadatas": [...]
            }
        """
        pass
    
    @abstractmethod
    async def get_collection_stats(self) -> dict:
        """Collection statistics.
        
        Returns:
            {
                "total_documents": 1000,
                "embedding_dimension": 384,
                "creation_timestamp": "2026-04-01T00:00:00Z"
            }
        """
        pass
```

### 1.4 API Fragmentation: Store-Specific Challenges

| Store | Challenge | Solution |
|-------|-----------|----------|
| **ChromaDB** | No bulk embed fetch; must paginate via collection.get() | Implement pagination loop; batch via limit/offset |
| **Pinecone** | Metadata filtering is pre-query, not post-retrieval | Separate fetch + client-side filtering |
| **Weaviate** | GraphQL interface; different query syntax | Use where_filter abstraction; translate to Weaviate WHERE |
| **Qdrant** | Point payloads have fixed structure; custom metadata limits | Pre-allocate metadata schema for poisoning flags |
| **Milvus** | Collection partitions; no cross-partition atomic query | Parallelize per-partition, merge results |
| **pgvector** | SQL-native; index selection critical for perf | Leverage PostgreSQL EXPLAIN to auto-tune indexes |
| **FAISS** | In-memory only; no metadata backend | Load from external DB; FAISS returns indices only |

### 1.5 Key Decisions

**Decision 1: Sync vs. Async**
- Poisoning detection is I/O-heavy (vector store queries)
- **Recommendation: Async-first** with optional sync wrapper
- BeigeBox tools use `async` natively; design for that

**Decision 2: Statistical Method**
- Isolation Forest (simple, fast, no assumptions)
- vs. Local Outlier Factor (better for clustering)
- vs. Autoencoders (expensive, requires training)
- **Recommendation: Isolation Forest + Cosine Distance Ensemble**
  - Isolation Forest: O(n log n), handles high dimensions
  - Cosine Distance: O(n²) but explicit semantic isolation
  - Combine via voting: ≥2 methods flag = alert

**Decision 3: Real-time vs. Batch**
- Real-time: Flag each new embedding at ingestion (hook-based)
- Batch: Periodic full-collection scan (hourly/daily)
- **Recommendation: Both**
  - Real-time for critical/regulated sectors
  - Batch for statistical coherence (drift over time)
  - Configurable via config.yaml

**Decision 4: Threshold Setting**
- Fixed thresholds (distance > X)? → Brittle
- Adaptive per-collection? → Requires baseline
- Per-source thresholds? → Complex
- **Recommendation: Adaptive baseline**
  - Compute P90/P95 distance on initial 1000 docs
  - Alert on deviation ≥3σ from baseline
  - Re-baseline every 10k new docs or on config request

---

## Part 2: Cross-Store Compatibility Analysis

### 2.1 API Comparison Matrix

```
Feature                | ChromaDB    | Pinecone  | Weaviate  | Qdrant    | Milvus    | pgvector
─────────────────────────────────────────────────────────────────────────────────────────────
Bulk embed fetch       | Partial     | Limited   | GraphQL   | Yes       | Yes       | Yes
Metadata filtering     | Yes         | Scalar    | GraphQL   | Yes       | Yes       | SQL WHERE
Streaming results      | No          | Yes       | No        | Yes       | No        | Yes
Pagination            | offset/limit| token     | offset    | offset    | offset    | LIMIT
Batch operations      | Limited     | Batch API | Batch     | Batch API | Batch     | COPY
Multi-tenancy native  | No          | Namespace | Tenant    | Shards    | Partition | Schema
Hybrid search         | No          | No        | Yes       | No        | No        | No
Authentication        | Token       | API key   | OIDC      | Token     | Token     | DB auth
Cost model            | Free        | Per query | Per doc   | Managed   | Managed   | DB-native
─────────────────────────────────────────────────────────────────────────────────────────────
```

### 2.2 Poisoning Detection Viability Per Store

**ChromaDB** ✅ **High viability**
- Adapter: paginate via `collection.get()`
- Limit: No native statistical functions; compute client-side
- Cost: Free; self-hosted
- Timeline: Available now

**Pinecone** ✅ **Viable with trade-offs**
- Adapter: use `describe_index_stats()` + query API
- Limit: Metadata filtering is scalar-only (no complex WHERE)
- Cost: Charged per query; budget-aware needed
- Timeline: Available now; cost implications for large scans

**Weaviate** ✅ **Fully viable**
- Adapter: GraphQL `_additional` fields for metadata + custom scoring
- Advantage: Native where() filtering, batch support
- Cost: Self-hosted free
- Timeline: Available now

**Qdrant** ✅ **Fully viable**
- Adapter: native batch query + point payloads
- Advantage: Best pagination support, payload filtering
- Cost: Self-hosted free
- Timeline: Available now

**Milvus** ✅ **Viable**
- Adapter: iterate per-partition for statistical coherence
- Limit: Partitions are logical; cross-partition queries expensive
- Cost: Self-hosted free
- Timeline: Available now; partition traversal needed

**pgvector** ✅ **Fully viable**
- Adapter: native SQL; leverage `<->` distance operator
- Advantage: Standard SQL; can integrate anomaly detection directly in DB
- Cost: Self-hosted; same as PostgreSQL
- Timeline: Available now; SQL-based detection most native

**FAISS** ⚠️ **Limited viability**
- Adapter: in-memory only; no metadata backend
- Use case: Only for local/development detection
- Recommendation: Pair with SQLite for metadata
- Timeline: Available now

---

## Part 3: Minimum Viable Generic Implementation

### 3.1 Reference Architecture

```python
# beigebox/tools/poisoning_detector.py

from abc import ABC, abstractmethod
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
import logging

class PoisoningDetector:
    """Vector-store-agnostic RAG poisoning detector."""
    
    def __init__(
        self,
        adapter: VectorStoreAdapter,
        method: str = "isolation_forest",  # or "lof", "hybrid"
        contamination: float = 0.05,  # assume 5% of collection is poisoned
        batch_size: int = 1000,
    ):
        self.adapter = adapter
        self.method = method
        self.contamination = contamination
        self.batch_size = batch_size
        self.baseline_stats = None
        
    async def detect_poisoning(
        self,
        full_scan: bool = False,
        risk_threshold: float = 0.7,
    ) -> dict:
        """
        Detect poisoned embeddings.
        
        Args:
            full_scan: If True, scan entire collection. Else, sample recent docs.
            risk_threshold: Score ≥ this → flag as poisoned (0-1)
        
        Returns:
            {
                "findings": [
                    {
                        "id": "doc_xyz",
                        "risk_score": 0.92,
                        "reason": "High cosine isolation + outlier ensemble",
                        "metadata": {...}
                    }
                ],
                "stats": {"scanned": 5000, "flagged": 42},
                "recommendation": "Review high-risk documents"
            }
        """
        # 1. Fetch embeddings
        embeddings_data = await self.adapter.get_embeddings_batch(
            limit=self.batch_size if not full_scan else 999999,
            offset=0
        )
        
        embeddings = np.array(embeddings_data["embeddings"])
        ids = embeddings_data["ids"]
        metadatas = embeddings_data["metadatas"]
        
        # 2. Detect outliers
        scores = self._compute_anomaly_scores(embeddings)
        
        # 3. Rank and filter
        findings = []
        for i, (id_, score) in enumerate(zip(ids, scores)):
            if score >= risk_threshold:
                findings.append({
                    "id": id_,
                    "risk_score": float(score),
                    "reason": self._explain_score(embeddings[i], embeddings, score),
                    "metadata": metadatas[i] if i < len(metadatas) else {}
                })
        
        findings.sort(key=lambda x: x["risk_score"], reverse=True)
        
        return {
            "findings": findings,
            "stats": {
                "scanned": len(ids),
                "flagged": len(findings),
                "flagged_pct": len(findings) / len(ids) if ids else 0
            },
            "recommendation": self._generate_recommendation(findings)
        }
    
    def _compute_anomaly_scores(self, embeddings: np.ndarray) -> np.ndarray:
        """Compute anomaly score per embedding (0-1)."""
        if self.method == "isolation_forest":
            clf = IsolationForest(contamination=self.contamination, random_state=42)
            preds = clf.fit_predict(embeddings)  # -1 (anomaly) or 1 (normal)
            scores = -(clf.score_samples(embeddings))
            scores = (scores - scores.min()) / (scores.max() - scores.min())
            return scores
        
        elif self.method == "lof":
            clf = LocalOutlierFactor(n_neighbors=min(20, len(embeddings)-1))
            preds = clf.fit_predict(embeddings)
            scores = -(clf.negative_outlier_factor_)
            scores = (scores - scores.min()) / (scores.max() - scores.min())
            return scores
        
        else:  # hybrid
            # Ensemble: combine Isolation Forest + cosine distance
            iso_scores = self._isolation_forest_scores(embeddings)
            cos_scores = self._cosine_distance_scores(embeddings)
            return 0.5 * iso_scores + 0.5 * cos_scores
    
    def _cosine_distance_scores(self, embeddings: np.ndarray) -> np.ndarray:
        """Measure cosine distance from collection centroid."""
        centroid = embeddings.mean(axis=0)
        centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-8)
        
        scores = []
        for emb in embeddings:
            emb_norm = emb / (np.linalg.norm(emb) + 1e-8)
            # Cosine distance: 1 - cosine_similarity
            dist = 1.0 - np.dot(emb_norm, centroid_norm)
            scores.append(dist)
        
        scores = np.array(scores)
        return (scores - scores.min()) / (scores.max() - scores.min() + 1e-8)
    
    def _explain_score(self, embedding, all_embeddings, score):
        """Generate human-readable explanation."""
        if score >= 0.8:
            return "High isolation distance + statistical anomaly"
        elif score >= 0.6:
            return "Moderate anomaly; verify source and content"
        else:
            return "Low confidence; monitor closely"
```

### 3.2 Integration into BeigeBox

```python
# beigebox/tools/schemas.py (add to validation_schemas)

class PoisoningDetectorInput(BaseModel):
    """Input for RAG poisoning detector tool."""
    action: str  # "scan_recent", "full_scan", "set_baseline"
    risk_threshold: float = 0.7
    batch_size: int = 1000
    explain: bool = True  # Include remediation hints

class PoisoningDetectorOutput(BaseModel):
    """Output from poisoning detector."""
    findings: list[dict]
    stats: dict
    recommendation: str
    remediation_steps: list[str]
```

```python
# beigebox/tools/registry.py

from beigebox.tools.poisoning_detector import PoisoningDetectorTool

# In ToolRegistry._load_builtin_tools():
if tools_cfg.get("poisoning_detector", {}).get("enabled", False):
    self.tools["poisoning_detector"] = PoisoningDetectorTool(
        vector_store=vector_store,
        method=tools_cfg.get("poisoning_detector", {}).get("method", "hybrid")
    )
```

---

## Part 4: Development Timeline

| Milestone | Effort | Timeline |
|-----------|--------|----------|
| VectorStoreAdapter base + ChromaDB impl | 2d | Week 1 |
| Pinecone + Weaviate adapters | 3d | Week 1-2 |
| Qdrant + Milvus adapters | 2d | Week 2 |
| pgvector adapter | 1d | Week 2 |
| Isolation Forest + hybrid scoring | 2d | Week 2-3 |
| Integration into BeigeBox tools | 1d | Week 3 |
| Unit + integration tests | 3d | Week 3-4 |
| **Total** | **~14 days** | **~4 weeks** |

---

## Part 5: Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| API fragmentation | Adapter code bloat | Use Vextra abstraction pattern; share utilities |
| Performance on large collections | Detection timeout | Paginate; cache baseline; offer incremental mode |
| False positives | Alert fatigue | Start conservative (threshold 0.8); adapt baseline over time |
| Embedding model bias | Skewed detection | Detect per-source; separate baselines for different embedding models |
| Cost on managed stores | Budget blowback | Budget checks; pre-scan estimate; warn Pinecone users |

---

## References

- [PoisonedRAG: USENIX 2025](https://www.usenix.org/system/files/usenixsecurity25-zou-poisonedrag.pdf)
- [RevPRAG: Revealing Poisoning Attacks](https://users.wpi.edu/~jdai/docs/RevPRAG.pdf)
- [RAGForensics: Traceback System](https://dl.acm.org/doi/10.1145/3696410.3714756)
- [Vextra: Vector DB Abstraction](https://arxiv.org/abs/2601.06727)
- [OWASP LLM08:2025 - Vector and Embedding Weaknesses](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
- [Embedding Anomaly Detection in RAG](https://prompt.security/blog/the-embedded-threat-in-your-llm-poisoning-rag-pipelines-via-vector-embeddings)
