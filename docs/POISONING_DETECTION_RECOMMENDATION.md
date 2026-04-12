# RAG Poisoning Detection: Strategic Recommendation

**Last Updated:** April 12, 2026  
**Author:** Security Research & Product Strategy Team  
**Decision Date:** April 12, 2026  
**Status:** Ready for Implementation

---

## TL;DR: What Should BeigeBox Do?

### Recommendation: Implement Tier 1 + 2 Strategy (In-Repo + Open-Source Library)

**Decision:** Build poisoning detector in BeigeBox **immediately** (2026 Q2), then extract as open-source library `embeddings-guardian` (2026 Q3).

**Timeline:**
- **2026 Q2 (4 weeks):** Poisoning detector tool in BeigeBox
- **2026 Q3 (2 weeks):** Extract as Python library on PyPI
- **2027 Q1+:** Monitor adoption; consider premium SaaS layer

**Expected outcome:** Market leadership in RAG security, 300-1000 GitHub stars, $6-12k revenue in year 1.

---

## Part 1: Decision Rationale

### 1.1 Why NOW?

**Threat landscape:**
- PoisonedRAG attack achieves 97% success on NQ, 99% on HotpotQA (USENIX 2025)
- 5-10 malicious docs in 1M-doc collection = 90% attack success
- OWASP LLM Top 10 2025 formally lists LLM08: Vector and Embedding Weaknesses
- Zero vector DB vendors have native detection (market gap)

**Market signal:**
- Healthcare/finance/defense contractors asking for poisoning detection
- ChromaDB issue #1488 (validation hooks) gathering interest
- HubScan (detection tool) published, achieving 90% recall
- Industry expects poisoning detection to be standard by 2027-2028

**Competitive advantage:**
- First-mover advantage in production-grade detection
- Can publish reference implementation (drives adoption)
- BeigeBox becomes synonymous with "secure RAG"

### 1.2 Why Open-Source?

**Not closed/proprietary because:**
- ❌ Security features should be auditable (white-box > black-box)
- ❌ Vector DB vendors won't adopt closed solution
- ❌ Temporary moat (will be commoditized 2027+)
- ❌ Regulatory bodies prefer open standards (healthcare, finance)

**Not in-repo-only because:**
- ❌ Limited to BeigeBox users (10-50 today)
- ❌ Can't be used standalone or in competing frameworks
- ❌ Misses 15,000+ RAG developers in Python ecosystem
- ❌ Weak positioning (seen as BeigeBox feature, not industry solution)

**Open-source library because:**
- ✅ Reach entire RAG ecosystem (15,000+ devs)
- ✅ Industry standard credibility
- ✅ Foundation for future monetization
- ✅ Thought leadership positioning
- ✅ Community contributions accelerate development

### 1.3 Why NOT Upstream to ChromaDB?

**ChromaDB misalignment:**
1. **Philosophy mismatch:** ChromaDB values simplicity; poisoning detection adds complexity
2. **Validation hooks missing:** ChromaDB lacks architectural foundation (issue #1488 still open)
3. **Domain specificity:** Only 15-20% of ChromaDB users need poisoning detection
4. **Operational burden:** BeigeBox would maintain code in someone else's repo (2-3h/month)
5. **Timeline risk:** 4-6 months from PR to production; BeigeBox users need it now

**Better alternative:** Build library, prove market demand, THEN approach ChromaDB (2027+).

---

## Part 2: Three-Phase Execution Plan

### Phase 1: BeigeBox-Native Tool (Weeks 1-4, April-May 2026)

**Goals:**
- Get poisoning detection into BeigeBox users' hands
- Validate detection accuracy on real-world RAG collections
- Gather user feedback

**Scope:**
```
Effort breakdown:
├─ Core detector + scoring (Isolation Forest, cosine distance) — 3 days
├─ ChromaDB adapter — 1 day
├─ BeigeBox tool integration — 1 day
├─ Tap event logging — 1 day
├─ Unit + integration tests — 2 days
├─ Documentation + examples — 1 day
└─ Buffer/bug fixes — 1 day
Total: ~2 weeks of development
```

**Deliverables:**
1. `beigebox/tools/poisoning_detector.py` (tool implementation)
2. `beigebox/storage/adapters/poisoning_adapter.py` (abstraction)
3. Unit tests in `tests/test_poisoning_detector.py`
4. Example config in `config.example.yaml`
5. Integration with Operator (optional workflow)

**Success criteria:**
- ✅ Detector runs on real BeigeBox collections
- ✅ <2% false positive rate (validated manually)
- ✅ Completes full 10k-doc scan in <30s
- ✅ 2+ early customers pilot the tool

**Example usage:**
```
Agent: "Analyze my RAG collection for poisoning. Use risk threshold 0.7."

BeigeBox tool: poisoning_detector
Input: {"action": "scan_recent", "risk_threshold": 0.7, "batch_size": 1000}

Output: {
  "findings": [
    {
      "id": "doc_xyz123",
      "risk_score": 0.92,
      "reason": "High cosine isolation + Isolation Forest outlier",
      "metadata": {"source": "web_crawler", "timestamp": "2026-04-10T..."}
    }
  ],
  "stats": {"scanned": 5432, "flagged": 12},
  "recommendation": "Review high-risk documents; consider removing top 5"
}
```

---

### Phase 2: Open-Source Library (Weeks 5-6, May-June 2026)

**Goals:**
- Extract detector as standalone library
- Publish to PyPI
- Drive adoption in broader RAG ecosystem

**Scope:**
```
Effort breakdown:
├─ Refactor for external API — 2 days (expose clean interfaces)
├─ Add adapter abstraction + Pinecone adapter — 2 days
├─ Packaging (pyproject.toml, setup) — 1 day
├─ Documentation (README, examples, API docs) — 2 days
├─ GitHub setup + CI/CD — 1 day
└─ PyPI release + announcement — 1 day
Total: ~1 week
```

**Deliverables:**
1. New GitHub repo: `github.com/beigebox-ai/embeddings-guardian`
2. PyPI package: `embeddings-guardian` (version 0.1.0)
3. Comprehensive README + docs
4. 5-10 example scripts
5. GitHub Actions CI: pytest, coverage, type checking
6. Apache 2.0 LICENSE

**Package structure:**
```
embeddings-guardian/
├── embeddings_guardian/
│   ├── core/
│   │   ├── detector.py       # Main detector class
│   │   ├── adapters.py       # VectorStoreAdapter ABC
│   │   └── scoring.py        # Isolation Forest + cosine distance
│   ├── backends/
│   │   ├── chromadb.py       # ChromaDB adapter
│   │   ├── pinecone.py       # Pinecone adapter
│   │   └── ...               # Others later
│   └── utils/
│       ├── metrics.py        # Precision, recall, F1
│       └── reporting.py      # JSON/CSV export
├── tests/
├── docs/
├── LICENSE (Apache 2.0)
└── pyproject.toml
```

**Installation:**
```bash
pip install embeddings-guardian
pip install embeddings-guardian[chromadb,pinecone]  # with optional backends
```

**Success criteria:**
- ✅ >100 PyPI downloads in first month
- ✅ >50 GitHub stars
- ✅ Zero critical bugs reported
- ✅ Mentions in 3+ technical blogs/discussions

**Launch announcement:**
```
Tweet: "Announcing embeddings-guardian: Open-source RAG poisoning detection. 
Works with ChromaDB, Pinecone, Weaviate, Qdrant, Milvus, pgvector. 
Detects 90%+ of poisoned documents. First-mover in production security."

Blog: "Defending RAG Systems Against Embedding Poisoning Attacks"
- What poisoning attacks are
- Why they matter (OWASP LLM08:2025)
- How embeddings-guardian detects them
- Integration examples
```

---

### Phase 3: Growth & Monetization (July 2026+)

**July-August 2026: Community Building**
- Fix community-reported issues (fast cycle)
- Add more backend adapters (Milvus, Qdrant if not done)
- Publish 3 technical blog posts
- Target: 200+ GitHub stars, 1000+ monthly PyPI downloads

**September-December 2026: Evaluation Point**
- Monitor metrics:
  - GitHub stars: Target 300-500 (strong signal)
  - PyPI downloads: Target 1000+/month
  - Community issues: Healthy engagement
  - Enterprise interest: 2-3 pilot customers

- **Decision gate:** If adoption is strong (>300 stars), proceed to Phase 3B

**Phase 3B (if justified): Premium Tier (2027)**
- Hosted SaaS API for detection
- Batch processing pipeline
- Integrations with SIEM tools (Splunk, DataDog)
- Target: $50-200/month per customer
- Realistic revenue: $5-20k/month by end of 2027

---

## Part 3: Resource & Timeline Summary

### 3.1 Effort Estimate

| Phase | Duration | Dev FTE | Product | Outcome |
|-------|----------|---------|---------|---------|
| Phase 1 (BeigeBox tool) | 4 weeks | 0.5 FTE | 0.1 FTE | In-repo tool |
| Phase 2 (Open-source lib) | 2 weeks | 0.5 FTE | 0.2 FTE | PyPI package |
| Phase 3 (Growth) | Ongoing | 0.2 FTE | 0.1 FTE | Community health |
| **Total (year 1)** | **~6 weeks + ongoing** | **~0.25 FTE avg** | **~0.1 FTE avg** | **Production tool + library** |

### 3.2 Resource Requirements

**Team:**
- 1 senior engineer (4 weeks Phase 1, 2 weeks Phase 2, 1h/week ongoing)
- 1 product manager (0.5h/week Phase 1-2, 1h/week growth)
- 1 technical writer (optional, speeds up documentation)

**Infrastructure:**
- GitHub repo (free)
- PyPI account (free)
- GitHub Actions CI/CD (free for public repos)
- Read the Docs (free, for documentation hosting)

**Cost:** Minimal (mostly engineering time)

### 3.3 Timeline: Gantt-Style

```
April 2026:
  Week 1-2: Core detector + ChromaDB adapter
  Week 3-4: BeigeBox integration, tests, docs

May 2026:
  Week 1: Open-source library setup (GitHub, PyPI)
  Week 2: Library documentation + examples
  Late May: Release 0.1.0 to PyPI, announce

June 2026 onwards:
  Week 1-4: Community issues, feedback loop, bug fixes
  Week 5+: New adapters (Milvus, Qdrant), features (streaming detection)

July-Dec 2026:
  Ongoing: Community growth, technical blog posts, monitoring metrics

Jan 2027:
  Decision gate: Evaluate adoption metrics
  If strong (>300 stars): Plan Phase 3B (premium tier)
  If modest (<100 stars): Accept niche positioning, maintain long-term
```

---

## Part 4: Risk Analysis

### 4.1 Key Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **High false positive rate** | Medium | High | Start conservative (threshold 0.8); gather manual validation data; iterate on algorithm |
| **Slow adoption (<50 stars)** | Medium | Medium | If adoption is slow, library remains niche (still valuable to BeigeBox users) |
| **ChromaDB adds native detection first** | Low | Medium | Our library works with all DBs, not just ChromaDB (competitive advantage) |
| **Vector DB API changes break adapters** | Low | Low | Adapters are isolated; update specific backend on version change |
| **Community contribution burden too high** | Medium | Low | Manage scope tightly; close low-priority issues with explanation |
| **Competitor launches similar tool** | Medium | Low | We have first-mover + production credibility; beat them on quality/docs |

### 4.2 Kill Criteria

**If any of these occur, pause/stop Phase 3:**
- ChromaDB or Pinecone launch native poisoning detection (market consolidation)
- <50 GitHub stars after 6 months (lack of interest)
- Zero enterprise pilot interest after active outreach (market doesn't exist yet)

**If kill criteria met:** Keep library maintained but low priority; repurpose team to other initiatives.

---

## Part 5: Competitive Landscape

### 5.1 Current Market State

**No native poisoning detection in production vector DBs:**
- ChromaDB: None
- Pinecone: None
- Weaviate: None
- Qdrant: None
- Milvus: None

**Academic tools (research, not production):**
- RAGForensics (traceback system, not prevention)
- RevPRAG (detection via LLM, not embeddings)
- HubScan (hubness poisoning, specific attack vector)

**Conclusion:** Market gap. First-mover advantage exists.

### 5.2 Timeline to Commoditization

**2026 (now):** Niche topic, research-driven  
**2027:** Industry standard emerges, compliance demand grows  
**2028:** Major vector DB vendors add native detection  
**2029+:** Commoditized; everyone has it  

**BeigeBox window:** 18-24 months to establish thought leadership.

---

## Part 6: Success Metrics

### 6.1 Phase 1 Success (Q2 2026)

- ✅ Tool deployed to 2+ BeigeBox production customers
- ✅ <2% false positive rate (manual validation)
- ✅ Completes 10k-doc scan in <30s
- ✅ Operator workflow can trigger detection
- ✅ Tap integration logs detection events

### 6.2 Phase 2 Success (Q3 2026)

- ✅ 100+ GitHub stars
- ✅ 500+ PyPI downloads/month
- ✅ 0 critical bugs in first month
- ✅ 1 external code contribution (e.g., docs, tests)
- ✅ Featured in 2+ technical blogs/newsletters

### 6.3 Phase 3 Success (2027)

- ✅ 300-500+ GitHub stars (industry recognition)
- ✅ 1000+ PyPI downloads/month
- ✅ 5-10 active community contributors
- ✅ 2-3 enterprise pilot customers
- ✅ Revenue (if SaaS tier): $5-20k/month

---

## Part 7: Decision Approval

**Recommendation:** Proceed with Phase 1 (BeigeBox tool) immediately.

**Next steps:**
1. ✅ Research complete (this document)
2. ✅ Architecture documented (POISONING_DETECTION_ARCHITECTURE.md)
3. ✅ Open-source strategy defined (OPEN_SOURCE_STRATEGY.md)
4. ⬜ Engineering sprint planning (start Week 1 April 2026)
5. ⬜ User interview phase (identify 2-3 pilot customers)
6. ⬜ Begin Phase 1 development

**Go/No-Go criteria:**
- Go: If any BeigeBox customer expresses interest in poisoning detection
- No-Go: If threat landscape shifts (e.g., vector DB vendors add detection first)

---

## References

**Threat Research:**
- [PoisonedRAG: USENIX 2025](https://www.usenix.org/system/files/usenixsecurity25-zou-poisonedrag.pdf)
- [OWASP LLM08:2025 Vector and Embedding Weaknesses](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
- [RAGForensics Traceback System](https://dl.acm.org/doi/10.1145/3696410.3714756)
- [Embedding Poisoning Attack Survey](https://prompt.security/blog/the-embedded-threat-in-your-llm-poisoning-rag-pipelines-via-vector-embeddings)

**Architecture & Technology:**
- [Vextra: Vector DB Abstraction](https://arxiv.org/abs/2601.06727)
- [ChromaDB GitHub Issues #1488, #5848](https://github.com/chroma-core/chroma)
- [HubScan: Hubness Poisoning Detection](https://arxiv.org/html/2602.22427)

**Business & Positioning:**
- [Open-Source Software Business Models](https://en.wikipedia.org/wiki/Business_model_for_open-source_software)
- [Apache 2.0 License](https://opensource.org/licenses/Apache-2.0)

---

**Approval:** Security Research Team, Product Management  
**Reviewed by:** [Names TBD]  
**Date:** April 12, 2026
