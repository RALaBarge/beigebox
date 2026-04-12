# Open-Source Poisoning Detection Strategy

**Last Updated:** April 12, 2026  
**Author:** Product & Strategy Team  
**Status:** Strategic Planning

---

## Executive Summary

**Recommended approach: Extract poisoning detector as standalone open-source library (`embeddings-guardian`)** with BeigeBox as the primary production consumer.

This strategy:
- Maximizes community reach (15,000+ RAG developers)
- Builds moat through reference implementation leadership
- Generates goodwill (open-source credibility)
- Enables future monetization (premium hosted service)
- Avoids dependency on ChromaDB's roadmap

---

## Part 1: Library vs. In-Repo vs. Proprietary Analysis

### 1.1 Three Strategic Options

#### Option A: In-Repo (BeigeBox-Only)

**Approach:** Poisoning detector lives in `beigebox/tools/poisoning_detector.py`

**Pros:**
- ✅ Tight integration with BeigeBox ecosystem
- ✅ No versioning/packaging overhead
- ✅ Full control of feature roadmap
- ✅ Faster iteration (no external dependency management)

**Cons:**
- ❌ Limited reach (10-50 BeigeBox users)
- ❌ Can't be used standalone (requires BeigeBox)
- ❌ Minimal community contribution
- ❌ Perceived as proprietary/locked

**Market impact:** Low. Feature becomes "BeigeBox thing" rather than "industry thing"

**Recommended if:** BeigeBox targets niche market (compliance tools); not a general-purpose play

---

#### Option B: Open-Source Library (`embeddings-guardian`)

**Approach:** Separate PyPI package, independent of BeigeBox

**Pros:**
- ✅ Reach 15,000+ RAG/LLM developers
- ✅ Industry standard credibility (OWASP mentions it)
- ✅ Attract external contributors
- ✅ Build moat through best-practices reference implementation
- ✅ Foundation for premium/hosted service later
- ✅ Marketing halo (we care about RAG security)

**Cons:**
- ⚠️ Versioning complexity (BeigeBox depends on library)
- ⚠️ External contribution review burden (2-3 hours/week)
- ⚠️ Must keep dependencies minimal (numpy, scikit-learn only)
- ⚠️ Slower iteration (API stability requirements)

**Market impact:** High. Positions BeigeBox/team as thought leaders in RAG security

**Recommended if:** Goal is market leadership, long-term positioning, ecosystem health

---

#### Option C: Proprietary/Closed (`rag-shield`, premium tier)

**Approach:** Closed-source, paid feature; available on premium BeigeBox tier or standalone SaaS

**Pros:**
- ✅ Immediate revenue stream ($50-500/month per customer)
- ✅ Competitive advantage (only vendor with this capability)
- ✅ Can charge for advanced features (custom thresholds, integrations)
- ✅ Higher margins (no community support burden)

**Cons:**
- ❌ Limited market size (<500 customers likely)
- ❌ High marketing cost (educating about the threat)
- ❌ Integration complexity (need API, licensing)
- ❌ Moat is temporary (will be commoditized 2027+)
- ❌ Perception risk (security should be open)

**Market impact:** Moderate revenue, limited market reach. Commoditized within 2 years.

**Recommended if:** Primary goal is short-term revenue; okay with being acquired/absorbed

---

### 1.2 Recommendation: Option B + Option A Hybrid

**Strategy:**
1. **Short-term (2026 Q2):** Build poisoning detector in BeigeBox (Option A)
2. **Mid-term (2026 Q3):** Extract as `embeddings-guardian` library (Option B)
3. **Long-term (2027):** Consider monetization layers (hosted detection API, enterprise support)

**Rationale:**
- BeigeBox users get feature quickly
- Open-source library establishes market leadership
- Later monetization preserved (SaaS layer on top of library)
- No lock-in to ChromaDB or any single vendor

---

## Part 2: Open-Source Library Design

### 2.1 Specification: `embeddings-guardian`

**Repository:** `github.com/beigebox-ai/embeddings-guardian`

**PyPI Package:** `embeddings-guardian`

**Version:** 0.1.0 (stable for production use)

```
embeddings-guardian/
├── embeddings_guardian/
│   ├── __init__.py
│   ├── core/
│   │   ├── detector.py          # PoisoningDetector base class
│   │   ├── adapters.py          # VectorStoreAdapter abstract base
│   │   └── scoring.py           # Anomaly detection algorithms
│   ├── backends/
│   │   ├── chromadb.py
│   │   ├── pinecone.py
│   │   ├── weaviate.py
│   │   ├── qdrant.py
│   │   ├── milvus.py
│   │   ├── pgvector.py
│   │   └── faiss.py
│   └── utils/
│       ├── metrics.py           # F1, precision, recall
│       ├── reporting.py         # JSON/CSV export
│       └── logging.py           # Structured logging
├── tests/
│   ├── test_detector.py
│   ├── backends/
│   │   ├── test_chromadb.py
│   │   └── test_pinecone.py   # Mock API tests
│   └── test_scoring.py
├── docs/
│   ├── README.md
│   ├── getting_started.md
│   ├── backends.md
│   ├── algorithms.md
│   └── examples/
│       ├── chromadb_example.py
│       ├── pinecone_example.py
│       └── batch_detection.py
├── LICENSE                      # Apache 2.0
├── pyproject.toml              # Modern Python packaging
├── requirements.txt
└── requirements-dev.txt
```

### 2.2 Dependency Strategy

**Goal:** Minimize deps, maximize portability

```toml
# pyproject.toml
[project]
name = "embeddings-guardian"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "numpy>=1.21",
    "scikit-learn>=1.0",
]

[project.optional-dependencies]
chromadb = ["chromadb>=0.4"]
pinecone = ["pinecone-client>=3.0"]
weaviate = ["weaviate-client>=4.0"]
qdrant = ["qdrant-client>=2.0"]
milvus = ["pymilvus>=2.3"]
pgvector = ["psycopg[binary]>=3.1"]
all = [
    "chromadb>=0.4",
    "pinecone-client>=3.0",
    "weaviate-client>=4.0",
    "qdrant-client>=2.0",
    "pymilvus>=2.3",
    "psycopg[binary]>=3.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "black>=23.0",
    "ruff>=0.1",
    "mypy>=1.0",
]
```

**Rationale:**
- Core deps: numpy + scikit-learn (industry standard)
- All backends are optional
- Users install only what they need
- Easy pip install embeddings-guardian[chromadb,pinecone]

### 2.3 API Design

```python
# Simple, intuitive API

from embeddings_guardian import PoisoningDetector
from embeddings_guardian.backends import ChromaDBAdapter

# Initialize
adapter = ChromaDBAdapter(collection=my_chroma_collection)
detector = PoisoningDetector(adapter=adapter, method="hybrid")

# Detect
findings = detector.detect(full_scan=False, risk_threshold=0.7)

# Report
report = detector.generate_report(findings, format="json")
print(report)

# Export
detector.export_findings(
    findings,
    output_path="poisoning_findings.csv",
    include_remediation=True
)
```

### 2.4 Documentation Quality

**README.md** (500 words):
- What is poisoning detection?
- Why it matters
- Quick-start with ChromaDB
- 2-minute example

**Getting Started** (1500 words):
- Installation
- Per-backend setup (ChromaDB, Pinecone, etc.)
- Configuration options
- Running detection

**Backends** (2000 words):
- API differences per store
- Performance characteristics
- Cost implications
- Troubleshooting

**Algorithms** (2500 words):
- Isolation Forest explanation
- Cosine distance isolation
- Hybrid scoring methodology
- Parameter tuning guide

**Examples** (code repo):
- 5+ real-world examples (RAG, semantic search, etc.)
- Benchmarks on sample datasets
- Integration with SIEM tools

---

## Part 3: Market Positioning

### 3.1 Positioning Statement

**For:** Data teams building secure RAG systems  
**Who:** Need to detect and mitigate poisoned embeddings  
**embeddings-guardian is:** An open-source, vector-DB-agnostic security tool  
**That:** Detects poisoned documents with 90%+ accuracy using anomaly detection  
**Unlike:** Manual review or vendor-specific solutions  
**Our tool:** Works with ChromaDB, Pinecone, Weaviate, and more

### 3.2 Target Audiences

| Audience | Motivation | Pitch |
|----------|-----------|-------|
| **Security teams** | Compliance (healthcare, finance) | Detect poisoned RAG data before it reaches users |
| **Data engineers** | Operational reliability | Automated data quality checks in ML pipelines |
| **RAG framework builders** | Feature completeness | Embed poisoning detection into your framework |
| **Enterprise RAG vendors** | Differentiation | Ship security out-of-the-box |

### 3.3 Go-to-Market Timeline

```
April 2026 (Q2):
  - Build poisoning_detector in BeigeBox
  - Beta test with 2-3 early customers
  - Gather feedback on false positive rate

May 2026:
  - Extract as embeddings-guardian library
  - Publish to PyPI (version 0.1.0)
  - Announce on Twitter, Hacker News, r/LocalLLM
  - Blog post: "Defending RAG Systems Against Poisoning Attacks"

June 2026:
  - Collect GitHub stars (target: 200+)
  - Fix community-reported issues (fast iteration)
  - Write 3 technical blog posts (Isolation Forest, per-backend deep-dives)

July-August 2026:
  - Community contributions arrive (adapters, docs)
  - Version 0.2.0: Add integrations (Grafana, DataDog, Splunk)
  - Version 0.3.0: Performance optimizations (streaming detection)

September 2026 onwards:
  - Monitor adoption metrics
  - If >500 stars: Consider premium layer (SaaS detection API)
  - If <100 stars: Accept niche positioning; maintain as long-term library
```

### 3.4 Metrics & Success Criteria

| Metric | 6-month target | 12-month target |
|--------|---|---|
| GitHub stars | 300+ | 1000+ |
| PyPI downloads/month | 500+ | 2000+ |
| Community issues resolved | 80% < 48h | 90% < 24h |
| Blog traffic | 2000/month | 5000/month |
| Enterprise pilots | 2-3 | 5-10 |

---

## Part 4: Monetization Options (2027+)

### 4.1 Premium Tiers (Freemium Model)

**Free Tier (Open-Source):**
- Self-hosted poisoning detection
- All backends supported
- Community support (GitHub issues)
- **Addressable market:** Startups, hobbyists, internal tools
- **Revenue:** None directly, but builds goodwill

**Pro Tier (Commercial, $50-200/month):**
- Hosted detection API
- Batch processing (10k+ docs/day)
- Priority support
- Custom thresholds per collection
- **Addressable market:** Scaling startups, mid-market RAG apps
- **Revenue:** $500-10k/month (10-50 customers)

**Enterprise Tier (Custom pricing):**
- Multi-tenant SaaS
- API integrations (Splunk, DataDog, PagerDuty)
- Custom detection algorithms
- SLA + dedicated support
- **Addressable market:** Fortune 500 with RAG at scale
- **Revenue:** $50k-500k/year (1-10 large accounts)

### 4.2 Revenue Projection (Conservative)

```
Year 1 (2027):
  - Free tier: 2000 users
  - Pro tier: 5-10 customers @ $100/month avg = $6-12k/year
  - Enterprise: 0 customers
  - Total revenue: $6-12k

Year 2 (2028):
  - Free tier: 5000 users
  - Pro tier: 30-50 customers @ $150/month = $54-90k/year
  - Enterprise: 1-2 customers @ $200k/year = $200-400k/year
  - Total revenue: $254-490k/year

Year 3+ (2029):
  - Free tier: 10k+ users (industry standard)
  - Pro tier: 100+ customers @ $150/month = $180k/year
  - Enterprise: 5-10 customers @ $300k/year = $1.5-3M/year
  - Total revenue: $1.7-3.2M/year
```

**Key assumption:** Poisoning detection becomes compliance requirement (healthcare/finance) in 2028-2029.

### 4.3 Alternative: Acquired by Major Vendor

**Timeline:** 2027-2028

**Potential acquirers:**
- **ChromaDB** (add security tier to their offering)
- **Pinecone** (defensibility against Weaviate)
- **Anthropic** (security layer for Claude RAG)
- **OpenAI** (integrate into platform)
- **Databricks** (part of LLM stack)

**Acquisition value:** $2-10M (based on GitHub adoption, customer traction)

**Pros:** Accelerated growth, resources, distribution  
**Cons:** May be shelved or integrated less-than-ideal

---

## Part 5: Licensing & Legal

### 5.1 License Choice

**Recommendation: Apache 2.0**

**Rationale:**
- ✅ Permissive (allows commercial use)
- ✅ Patent grant (protects us from frivolous lawsuits)
- ✅ Industry standard (npm, AWS libraries use it)
- ✅ OSI-approved
- ❌ Not copyleft (no virality requirement)

**Alternative considered:** MIT
- Simpler but no patent clause
- Prefer Apache 2.0 for security-critical code

### 5.2 CLA (Contributor License Agreement)

**Recommendation: Lightweight CLA for external PRs**

**Threshold:** Only required for commits >100 lines of new code

**Purpose:**
- Protect against IP disputes
- Enable future commercialization (premium tier)
- Standard for high-value open-source projects

**Tool:** Use EasyCLA (Linux Foundation) or DCO (Developer Certificate of Origin)

### 5.3 Code of Conduct

**Adopt:** Contributor Covenant 2.1

**Enforcement:** Community manager (or rotate among maintainers)

---

## Part 6: Integration Strategy

### 6.1 BeigeBox Integration

```python
# beigebox/tools/poisoning_detector.py

from embeddings_guardian import PoisoningDetector
from embeddings_guardian.backends import ChromaDBAdapter

class PoisoningDetectorTool(BaseTool):
    """
    BeigeBox tool that uses embeddings-guardian library.
    
    This tool is a wrapper that:
    1. Receives input from Operator agent
    2. Instantiates appropriate backend adapter
    3. Calls embeddings-guardian detection
    4. Logs findings to Tap event stream
    5. Returns structured JSON to agent
    """
    
    name = "poisoning_detector"
    
    async def execute(self, action: str, **kwargs):
        detector = PoisoningDetector(
            adapter=self.adapter,
            method=self.config.get("method", "hybrid")
        )
        findings = detector.detect(
            full_scan=action == "full_scan",
            risk_threshold=kwargs.get("risk_threshold", 0.7)
        )
        
        # Log to Tap
        self.tap_event("poisoning_detection_complete", {
            "findings_count": len(findings["findings"]),
            "risk_distribution": self._compute_distribution(findings),
        })
        
        return findings
```

### 6.2 External Integration Points

**SIEM/Monitoring:**
- Export to Splunk/DataDog/New Relic via JSON
- Webhook integration for alerting
- Custom reporters (CSV, Parquet)

**Remediation:**
- Integration with document deletion workflows
- Quarantine collections (read-only)
- Re-embedding triggers (for revised documents)

**Compliance:**
- Audit log export (SEC, healthcare)
- Policy documentation (for compliance teams)

---

## References

- [Vextra: Vector DB Abstraction Pattern](https://arxiv.org/abs/2601.06727)
- [Apache 2.0 License](https://opensource.org/licenses/Apache-2.0)
- [Contributor Covenant](https://www.contributor-covenant.org/)
- [EasyCLA](https://easycla.lfx.dev/)
- [Open Source Business Models](https://en.wikipedia.org/wiki/Business_model_for_open-source_software)
