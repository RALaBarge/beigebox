# Vector Poisoning Library (`embeddings-guardian`) — Complete Components Breakdown

## Library Overview

**Package:** `embeddings-guardian` (PyPI)  
**Repository:** `github.com/beigebox-ai/embeddings-guardian`  
**License:** Apache 2.0  
**Dependencies:** numpy, scikit-learn (minimal)  
**Supported Stores:** ChromaDB, Pinecone, Weaviate, Qdrant, Milvus, pgvector, FAISS  

---

## Part 1: Core Module (`embeddings_guardian/core/`)

### 1.1 `detector.py` — Base Detection Engine

**Purpose:** Abstract poisoning detector with pluggable scoring algorithms

**Key Classes:**

```python
class PoisoningDetector:
    """Main detection interface"""
    - __init__(algorithm='magnitude', sensitivity=0.95, baseline_window=1000)
    - update_baseline(embedding: np.array) → None
    - is_poisoned(embedding: np.array) → tuple[bool, float, str]
    - get_stats() → dict  # baseline stats, detection rate, etc
    - reset_baseline() → None
```

**Supports Multiple Detection Algorithms:**
1. **Magnitude** — L2 norm z-score (fastest, default)
2. **Centroid** — Distance to semantic centroids
3. **Neighborhood** — k-NN density analysis (RevPRAG)
4. **Dimension** — Per-dimension z-score
5. **Fingerprinting** — LLM-based semantic fingerprints (optional, expensive)

**Configuration:**
- `sensitivity: float` — Z-score threshold (default 0.95 = 3.0σ)
- `baseline_window: int` — Rolling window size for baseline stats
- `magnitude_bounds: tuple` — Valid range for embedding norms [min, max]
- `algorithm: str` — Which detection method to use
- `mode: str` — Response mode (warn, quarantine, strict)

---

### 1.2 `adapters.py` — Vector Store Abstraction Layer

**Purpose:** Unified interface to different vector databases

**Abstract Base Class:**

```python
class VectorStoreAdapter(ABC):
    """Protocol that all vector stores must implement"""
    
    @abstractmethod
    def upsert_embeddings(self, ids, embeddings, metadata) → None:
        """Store embeddings with optional metadata"""
        
    @abstractmethod
    def query_embeddings(self, query_embedding, k=10) → list[tuple[str, float]]:
        """Find k-nearest neighbors"""
        
    @abstractmethod
    def get_baseline_embeddings(self, sample_size=1000) → np.ndarray:
        """Sample existing embeddings for baseline calibration"""
        
    @abstractmethod
    def quarantine_embedding(self, id: str, reason: str) → None:
        """Move suspicious embedding to quarantine table"""
        
    @abstractmethod
    def get_statistics() → dict:
        """Return store-specific stats (document count, embedding dim, etc)"""
```

**Adapter Implementations (7 total):**
1. ChromaDB — Native integration with ChromaDB backend
2. Pinecone — REST API adapter with async support
3. Weaviate — GraphQL adapter
4. Qdrant — gRPC adapter for performance
5. Milvus — Python SDK adapter
6. pgvector — PostgreSQL DBAPI adapter
7. FAISS — In-memory index adapter (research/testing)

**Key Design Pattern:** Adapter holds credentials/connection, detector talks to adapter via abstract interface

---

### 1.3 `scoring.py` — Anomaly Detection Algorithms

**Purpose:** Core math for all detection methods

**Available Algorithms:**

1. **MagnitudeAnomaly**
   ```python
   class MagnitudeAnomaly:
       def score(embedding: np.array) → float:
           """Return z-score of embedding magnitude"""
           mag = np.linalg.norm(embedding)
           z = (mag - self.baseline_mean) / self.baseline_std
           return abs(z)
   ```
   - Cost: O(1), <0.5ms
   - Best for: Real-time detection

2. **CentroidDistance**
   ```python
   class CentroidDistance:
       def score(embedding: np.array) → float:
           """Return minimum cosine distance to any centroid"""
           distances = [cosine_dist(embedding, c) for c in self.centroids]
           return min(distances)  # lower = more anomalous
   ```
   - Cost: O(k), ~1ms (k = number of centroids)
   - Best for: Semantic outlier detection

3. **NeighborhoodDensity** (RevPRAG)
   ```python
   class NeighborhoodDensity:
       def score(embedding: np.array, k=5) → float:
           """Return z-score of k-NN neighborhood density"""
           distances = self.knn(embedding, k=k)
           neighborhood_mean = np.mean(distances)
           z = (neighborhood_mean - self.corpus_mean) / self.corpus_std
           return z
   ```
   - Cost: O(log n) with index, ~5-10ms
   - Best for: Sophisticated poisoning

4. **DimensionOutlier**
   ```python
   class DimensionOutlier:
       def score(embedding: np.array) → float:
           """Return max z-score across all dimensions"""
           z_scores = np.abs((embedding - self.dim_means) / self.dim_stds)
           return np.max(z_scores)
   ```
   - Cost: O(d), <0.5ms (d = embedding dim)
   - Best for: Quick secondary check

5. **SemanticFingerprint** (LLMPrint)
   ```python
   class SemanticFingerprint:
       def score(document_text: str, llm_client) → float:
           """Generate fingerprint and compare to known patterns"""
           fp = self.create_fingerprint(document_text, llm_client)
           distance = self.distance_to_nearest_known(fp)
           return distance  # higher = more anomalous
   ```
   - Cost: O(n) LLM calls, ~10-30 seconds
   - Best for: Deep forensics, not real-time

**Ensemble Option:**
```python
class EnsembleDetector:
    """Combine multiple algorithms for higher accuracy"""
    def __init__(self, algorithms=['magnitude', 'centroid']):
        pass
    
    def score(embedding) → float:
        """Return weighted average of all algorithm scores"""
        scores = [alg.score(embedding) for alg in self.algorithms]
        return np.average(scores, weights=self.weights)
```

---

## Part 2: Backend Adapters (`embeddings_guardian/backends/`)

### 2.1 ChromaDB Adapter

```python
class ChromaDBAdapter(VectorStoreAdapter):
    """Native ChromaDB integration"""
    
    def __init__(self, collection, detector: PoisoningDetector):
        self.collection = collection
        self.detector = detector
        
    def upsert_embeddings(self, ids, embeddings, metadata):
        # Pre-check each embedding
        for i, emb in enumerate(embeddings):
            is_poisoned, confidence, reason = self.detector.is_poisoned(emb)
            if is_poisoned:
                # Log to Tap/metrics
                logger.warning(f"Poisoned embedding detected: {reason}")
                # Move to quarantine if configured
                if self.detector.mode == 'quarantine':
                    self.quarantine_embedding(ids[i], reason)
                    continue
        
        # Store the embeddings
        self.collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadata)
```

**Features:**
- Pre-upsert validation hook
- Automatic baseline extraction from existing collection
- Quarantine table (metadata flagged as poisoned)
- Stats API: `adapter.get_statistics()`

---

### 2.2 Pinecone Adapter

```python
class PineconeAdapter(VectorStoreAdapter):
    """REST API adapter for Pinecone serverless"""
    
    def __init__(self, index_name: str, api_key: str, detector: PoisoningDetector):
        self.index = pinecone.Index(index_name)
        self.detector = detector
        
    async def upsert_embeddings(self, ids, embeddings, metadata):
        # Async validation loop
        for i, emb in enumerate(embeddings):
            is_poisoned, conf, reason = self.detector.is_poisoned(emb)
            if not is_poisoned:
                await self.index.upsert([(ids[i], emb, metadata[i])])
            else:
                # Pinecone: use metadata tag for quarantine
                poisoned_metadata = {**metadata[i], '__poisoned': reason}
                await self.index.upsert([(ids[i], emb, poisoned_metadata)])
```

**Features:**
- Async/await for high-throughput scenarios
- Metadata-based quarantine (no separate table)
- Query API integration (sample existing embeddings for baseline)

---

### 2.3 Weaviate Adapter

```python
class WeaviateAdapter(VectorStoreAdapter):
    """GraphQL adapter for Weaviate"""
    
    def __init__(self, client: weaviate.Client, class_name: str, detector):
        self.client = client
        self.class_name = class_name
        self.detector = detector
```

**Features:**
- GraphQL mutation for embedding upsert
- Schema validation (custom properties for quarantine)

---

### 2.4 Qdrant Adapter

```python
class QdrantAdapter(VectorStoreAdapter):
    """gRPC adapter for Qdrant (high-performance)"""
    
    def __init__(self, client: qdrant_client.QdrantClient, collection_name: str, detector):
        self.client = client
        self.collection_name = collection_name
```

**Features:**
- gRPC for low-latency operations
- Point updates for quarantine
- Payload-based metadata

---

### 2.5 Milvus Adapter

```python
class MilvusAdapter(VectorStoreAdapter):
    """Python SDK adapter for Milvus"""
    
    def __init__(self, alias: str, db_name: str, detector):
        self.conn = connections.connect(alias=alias)
```

**Features:**
- Collection insert/upsert
- Scalar field for quarantine flag
- Batch operations for efficiency

---

### 2.6 pgvector Adapter

```python
class PgvectorAdapter(VectorStoreAdapter):
    """PostgreSQL + pgvector adapter"""
    
    def __init__(self, connection_string: str, table_name: str, detector):
        self.conn = psycopg.connect(connection_string)
```

**Features:**
- SQL transactions
- Native vector operations (similarity search)
- Quarantine table in same schema

---

### 2.7 FAISS Adapter

```python
class FAISSAdapter(VectorStoreAdapter):
    """FAISS in-memory index (testing/research)"""
    
    def __init__(self, index: faiss.Index, detector):
        self.index = index
```

**Features:**
- Pure Python, no external service
- Fast prototyping
- Not for production (data loss on restart)

---

## Part 3: Utilities (`embeddings_guardian/utils/`)

### 3.1 `metrics.py` — Performance Evaluation

```python
class DetectionMetrics:
    """Track detection performance"""
    
    def __init__(self):
        self.tp = 0      # True positives (correctly detected poisoned)
        self.fp = 0      # False positives (false alarm on clean)
        self.tn = 0      # True negatives (correctly accepted clean)
        self.fn = 0      # False negatives (missed poisoned)
    
    def precision(self) → float:
        """TP / (TP + FP)"""
        
    def recall(self) → float:
        """TP / (TP + FN)"""
        
    def f1_score(self) → float:
        """2 * (precision * recall) / (precision + recall)"""
        
    def false_positive_rate(self) → float:
        """FP / (FP + TN)"""
```

**Use case:** Benchmark detector on test corpus before deployment

---

### 3.2 `reporting.py` — Export & Visualization

```python
class Reporter:
    """Generate detection reports"""
    
    def json_report(self) → dict:
        """Summary of detections as JSON"""
        {
            "summary": {
                "total_scanned": 10000,
                "poisoned_detected": 47,
                "detection_rate": 0.0047,
                "confidence_avg": 0.92
            },
            "detections": [
                {
                    "id": "doc_123",
                    "confidence": 0.99,
                    "algorithm": "magnitude",
                    "reason": "magnitude_z_score=4.2",
                    "timestamp": "2026-04-12T15:23:00Z"
                }
            ]
        }
    
    def csv_report(self) → str:
        """CSV export for spreadsheet analysis"""
        
    def html_dashboard(self) → str:
        """Interactive HTML dashboard with charts"""
```

---

### 3.3 `logging.py` — Structured Logging

```python
class StructuredLogger:
    """Log detections with context"""
    
    def log_poisoning_detected(self, embedding_id, algorithm, confidence, reason):
        # Structured JSON to stdout/file for aggregation
        {
            "event": "poisoning_detected",
            "embedding_id": "doc_123",
            "algorithm": "magnitude",
            "confidence": 0.99,
            "reason": "magnitude_z_score=4.2",
            "timestamp": "2026-04-12T15:23:00Z"
        }
```

---

## Part 4: Tests (`tests/`)

### 4.1 Unit Tests (`test_detector.py`)

- Test each algorithm independently
- Test baseline calculation
- Test threshold tuning
- Test edge cases (NaN, Inf, empty vectors)
- Test false positive rate on synthetic clean embeddings
- Test false negative rate on synthetic poisoned embeddings

**Target coverage:** 95%+ code coverage

---

### 4.2 Backend Tests (`backends/test_*.py`)

- ChromaDB: Integration with real ChromaDB instance
- Pinecone: Mock API tests (avoid rate limits)
- Weaviate: GraphQL endpoint mocking
- Qdrant: gRPC endpoint mocking
- Milvus: Docker container tests
- pgvector: PostgreSQL Docker container tests

---

### 4.3 Performance Tests (`test_performance.py`)

- Benchmark each algorithm (latency per vector)
- Test ensemble performance
- Test baseline calibration speed
- Verify <5ms per vector requirement

---

## Part 5: Documentation (`docs/`)

### 5.1 `README.md` — Quick Start

```markdown
# embeddings-guardian

Detect poisoned embeddings in your vector database.

## Installation
pip install embeddings-guardian

## Quick Start
from embeddings_guardian import ChromaDBAdapter, PoisoningDetector

detector = PoisoningDetector(algorithm='magnitude')
adapter = ChromaDBAdapter(collection, detector)

# Scan for poisoned embeddings
adapter.upsert_embeddings(ids, embeddings)  # Pre-checks!
```

---

### 5.2 `getting_started.md` — Full Tutorial

- Setup for each vector store
- Baseline calibration
- Configuration tuning
- Monitoring & alerting

---

### 5.3 `algorithms.md` — Deep Dive

- Explain each detection algorithm
- Math and theory
- When to use each one
- Performance characteristics

---

### 5.4 `examples/` — Code Samples

1. **chromadb_example.py** — ChromaDB integration
2. **pinecone_example.py** — Pinecone integration
3. **batch_detection.py** — Scan existing corpus
4. **ensemble_example.py** — Use multiple algorithms
5. **monitoring.py** — Real-time Prometheus metrics

---

## Part 6: Packaging (`pyproject.toml`)

### Dependencies Strategy

**Core dependencies (always):**
- numpy >=1.21
- scikit-learn >=1.0

**Optional dependencies (install separately):**
- chromadb >=0.4
- pinecone-client >=3.0
- weaviate-client >=4.0
- qdrant-client >=2.0
- pymilvus >=2.3
- psycopg[binary] >=3.1

**Why?** Users only install support for vector stores they use

---

## Part 7: Integration with BeigeBox

### How BeigeBox Uses the Library

```python
# In beigebox/storage/backends/chroma.py
from embeddings_guardian import ChromaDBAdapter, PoisoningDetector

self.detector = PoisoningDetector(
    algorithm=config.security.rag_poisoning.algorithm,
    sensitivity=config.security.rag_poisoning.sensitivity,
    mode=config.security.rag_poisoning.mode
)
self.adapter = ChromaDBAdapter(collection, self.detector)

# In upsert call:
self.adapter.upsert_embeddings(ids, embeddings, metadata)
```

### Tap Integration

All detections logged to Tap/Wiretap:

```python
self.wire.log(
    direction="internal",
    role="rag_poisoning_detector",
    content=f"Poisoned embedding detected",
    event_type="security_event",
    meta={"confidence": 0.99, "algorithm": "magnitude"}
)
```

---

## Library Statistics

| Component | Lines of Code | Purpose |
|-----------|---------------|---------|
| `detector.py` | ~400 | Base detector + algorithms |
| `adapters.py` | ~150 | Abstract interface |
| `chromadb.py` | ~200 | ChromaDB adapter |
| `pinecone.py` | ~180 | Pinecone adapter |
| `weaviate.py` | ~150 | Weaviate adapter |
| `qdrant.py` | ~150 | Qdrant adapter |
| `milvus.py` | ~150 | Milvus adapter |
| `pgvector.py` | ~150 | PostgreSQL adapter |
| `faiss.py` | ~100 | FAISS adapter |
| `metrics.py` | ~200 | Metrics tracking |
| `reporting.py` | ~250 | Report generation |
| `logging.py` | ~150 | Structured logging |
| Tests | ~1500 | Unit + integration tests |
| Docs | ~500 | README, guides, examples |
| **Total** | **~4500** | Production-ready library |

---

## Release Timeline

| Phase | Timeline | Effort | Deliverable |
|-------|----------|--------|-------------|
| **Phase 1** | April 2026 | ✅ DONE | BeigeBox detector (425 LOC) |
| **Phase 2** | May 2026 | 2 weeks | Extract `embeddings-guardian` library |
| **Phase 3** | June 2026+ | 1 week/month | Monitor, maintenance, SaaS planning |

---

## Success Criteria

- **Functionality:** Detects 95%+ of poisoned embeddings with <1% false positives
- **Performance:** <5ms per vector on all backends
- **Adoption:** 300+ GitHub stars by Dec 2026
- **Community:** 10+ external contributors by Q4 2026
- **Market:** Recognized as industry standard by OWASP 2027

