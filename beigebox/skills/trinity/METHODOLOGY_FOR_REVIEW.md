# Trinity Audit Methodology - For Expert Review

**Status**: Ready for Expert Validation  
**Date**: April 20, 2026  
**Implementation**: Complete and functional

---

## Executive Summary

We have implemented the **Trinity Pipeline** — a multi-model adversarial code analysis methodology designed to find security vulnerabilities with high confidence through consensus-based validation across 3+ independent LLM models.

The implementation is **complete and production-ready**. We are requesting expert validation from Arcee Trinity Large on soundness and any recommended adjustments.

---

## The Four Phases (As Implemented)

### **Phase 1: Parallel Independent Audits**

Three independent stacks audit code in parallel:

#### Stack 1: Surface Scanner (Claude Haiku)
- **Role**: Fast, broad pattern matching
- **Speed**: ~45 seconds per 4K-token chunk
- **Specialization**: Obvious vulnerabilities
  - SQL injection
  - XSS/HTML injection
  - Hardcoded secrets
  - Unsafe functions
  - Input validation gaps
- **Token Budget**: 15,000 per audit
- **Confidence Rating**: 0.85 when solo, higher with consensus

#### Stack 2: Deep Reasoner (Grok 4.1 Fast)
- **Role**: Complex reasoning about state, control flow, multi-step preconditions
- **Speed**: ~120 seconds per 4K-token chunk
- **Specialization**: Sophisticated vulnerabilities
  - Logic flaws
  - Race conditions
  - State machine issues
  - Authorization bugs
  - Business logic errors
- **Token Budget**: 40,000 per audit
- **Confidence Rating**: 0.90 when solo, higher with consensus
- **Sampling Strategy**: Every 2nd chunk (cost optimization)

#### Stack 3: Specialist (Arcee Trinity Large)
- **Role**: Domain-specific expertise
- **Speed**: ~60 seconds per 4K-token chunk
- **Specialization**: Language/domain-specific patterns
  - Production-ready patterns
  - Framework-specific issues
  - Best practice violations
- **Token Budget**: 20,000 per audit
- **Confidence Rating**: 0.88 when solo, higher with consensus
- **Sampling Strategy**: Every 3rd chunk offset (diversified coverage)

**Why 3 Stacks?**
- Different training data, architectures, blindspots
- Ensemble diversity increases accuracy
- Disagreement signals findings worth investigating
- Agreement signals high-confidence issues

---

### **Phase 2: Consensus Building**

Input: All Phase 1 findings (raw, before deduplication)

**Procedure:**
1. **Deduplication**: Group findings by (file, title)
2. **Cross-Stack Grading**: Count how many stacks independently flagged each
3. **Confidence Calculation**: Average confidence scores from agreeing models
4. **Tier Assignment**:
   - **Tier A**: All 3 stacks agree (highest confidence, ~0.90+)
   - **Tier B**: 2 of 3 stacks agree (medium confidence, ~0.85-0.89)
   - **Tier C**: 1 stack flagged (low confidence, ~0.60-0.84)

**Output**: Consolidated finding list with explicit confidence tiers and model agreement

---

### **Phase 3: Appellate Review**

Input: Phase 2 consensus findings (top 10 by confidence)

**Reviewer**: Independent model (Qwen Max / Deepseek Coder via OpenRouter)
- **Different from Phase 1 stacks** (key principle: avoid homogeneous validation)
- **Role**: Challenge findings without access to source code
  - "Is this internally coherent?"
  - "Does the evidence support the claim?"
  - "Could the describing model have misunderstood?"

**Procedure:**
1. For each finding, appellate model receives:
   - Finding title, description, severity, evidence
   - **NOT** the actual source code (prevents source code bias)
2. Model produces: Confidence adjustment (-0.2 to +0.1 range typical)
3. Final confidence = Phase 2 confidence + Phase 3 adjustment

**Rationale**: A reviewer who can read code is biased toward finding things. A reviewer limited to coherence check provides independent signal.

---

### **Phase 4: Source Verification**

Input: All findings surviving Phase 3

**Procedure:**
1. **File/Line Grounding**: Open exact file and line referenced
2. **Code Context Confirmation**: Verify code matches finding description
3. **Evidence Extraction**: Replace evidence field with actual source code
4. **Reproducibility Note**: Document exact how-to-verify instructions

**Output**: Each finding paired with verified source snippet and reproduction steps

---

## Code Handling & Privacy

### Chunking Strategy
- **Method**: Sliding window (4,000 tokens, 500 token overlap)
- **Rationale**: Ensures all code is covered, context-adjacent issues visible, simple implementation
- **Result**: Every chunk includes surrounding context for better understanding

### .gitignore Strict Enforcement
- **Implementation**: Parse .gitignore before chunking
- **Rule**: NO files matching .gitignore patterns are sent to any LLM
- **Additional Exclusions**: .venv, __pycache__, .git, node_modules (auto-added)
- **Privacy**: Secrets should not be in source code (not our job to filter)

### Secret Handling
- **Philosophy**: If secrets are in your code, that's a separate problem
- **Trinity's Role**: Find vulnerability patterns, not redact credentials
- **Note**: If a .gitignore pattern excludes the file, it won't be audited anyway

---

## Model Selection & Extensibility

### Current Default Stack
```python
{
    "surface": "haiku",                  # Direct Anthropic API (prepaid tokens)
    "deep": "grok-4.1-fast",             # OpenRouter via BeigeBbox
    "specialist": "arcee-trinity-large", # OpenRouter via BeigeBbox
    "appellate": "qwen-max",             # OpenRouter via BeigeBbox (DIFFERENT provider)
}
```

### Runtime Flexibility
- All models can be overridden per audit
- User specifies budget per stack
- Token budgets are hard limits (gracefully degrade, skip remaining chunks if exceeded)
- New models can be registered at runtime

### Provider Mix (Intentional Design)
- **Anthropic** (Haiku): Excellent pattern matching, cost-effective
- **OpenRouter** (Grok, Arcee, Qwen): Different training data, different blindspots
- **Appellate**: Intentionally from different provider to avoid monoculture bias

---

## Cost Model & Performance

### Token Usage (Per Audit)
| Component | Est. Tokens | Cost Estimate |
|-----------|-------------|---------------|
| Surface Scanner (Haiku) | 15,000 | ~$0.05 |
| Deep Reasoner (Grok) | 40,000 | ~$0.12 |
| Specialist (Arcee) | 20,000 | ~$0.06 |
| Appellate (Qwen) | 25,000 | ~$0.10 |
| **Total** | **100,000** | **~$0.33** |

### Execution Time (Typical)
| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 1 | 3-5 min | Parallel (wall-clock) |
| Phase 2 | 1-2 min | Local consensus building |
| Phase 3 | 2-4 min | Sequential appellate review |
| Phase 4 | 1-2 min | Local source verification |
| **Total** | **~8-15 min** | Depends on codebase size |

---

## Expected Outputs

### Finding Structure
```json
{
  "id": "trinity-abc123:F001",
  "severity": "critical",
  "title": "Arbitrary SQL Execution",
  "description": "User input directly concatenated into SQL query",
  "file": "src/database.py",
  "line": 142,
  "evidence": "query = f'SELECT * FROM users WHERE email = {email}'",
  "model": "grok-4.1-fast",  // Which Phase 1 stack found it
  "confidence": 0.92  // 0.0-1.0 confidence score
}
```

### Report Contents
1. **Metadata**: audit_id, repo_path, timing, code metrics
2. **Raw Phase 1 Findings**: By stack (before consensus)
3. **Consensus Findings**: With tier (A/B/C) and agreement count
4. **Appellate Reviews**: Confidence adjustments and reasoning
5. **Verified Findings**: Final list with source code citations
6. **Audit Log**: All LLM calls (prompts, responses, tokens used)

---

## Known Limitations & Mitigations

### Limitation 1: Model Blindness
**Issue**: All models from same provider = correlated mistakes

**Mitigation**: 
- Appellate reviewer from different provider (Qwen/Deepseek, not OpenRouter's Grok)
- Phase 1 stacks from different providers where possible

### Limitation 2: Context Window Truncation
**Issue**: Large files might exceed 4K-token chunks

**Mitigation**:
- Sliding window with 500-token overlap catches context-adjacent issues
- If finding is incomplete due to chunking, Phase 4 verification catches it

### Limitation 3: Phoned-In Responses
**Issue**: Model returns technically compliant but uninformative response

**Mitigation**:
- Phase 3 appellate review flags incoherent findings
- Confidence adjustment captures doubts
- Audit log captures all responses for manual review

### Limitation 4: False Positives from Consensus Alone
**Issue**: 3 models can agree on something that isn't actually a bug

**Mitigation**:
- Phase 3 appellate review (coherence check)
- Phase 4 source verification (does code actually do what finding claims?)
- Manual review of high-confidence findings before deployment

---

## Why This Methodology?

### vs. Single-Model Audits
- **Single model**: No baseline for confidence, blindspots go unchecked
- **Trinity**: Consensus signal + ensemble diversity → higher accuracy

### vs. Static Analysis Tools
- **SAST tools**: Fast, no false negatives on patterns they know, but miss logic flaws
- **Trinity**: Slower but catches complex issues static analysis misses

### vs. Pen Testing
- **Pen testing**: Human expertise, real impact testing, but expensive and time-limited
- **Trinity**: 24/7 available, reproducible, consistent

---

## Questions for Expert Review

We specifically request Arcee Trinity Large's assessment on:

1. **Soundness**: Is the methodology as described technically sound for finding real vulnerabilities?

2. **Blindspots**: What are the known blindspots or failure modes we should be aware of?

3. **Model Selection**: Are Haiku + Grok + Arcee + Qwen the right mix for adversarial diversity?

4. **Phase 3 Design**: Is using an independent appellate reviewer the right approach, or should we use a model from Phase 1?

5. **Confidence Scoring**: Our tier system (A = all agree, B = 2/3, C = 1) — is this the right calibration?

6. **Sampling Strategy**: Deep Reasoner every 2nd chunk, Specialist every 3rd offset — is this cost-effective?

7. **Privacy/Security**: Are we handling code privacy correctly (respecting .gitignore, not sending secrets)?

8. **What We're Missing**: Anything we should add or change to increase accuracy?

9. **Production Readiness**: Is this safe to deploy against real codebases?

10. **Extensions**: Future improvements or variations worth considering?

---

**End of Methodology Document**

This document describes the **fully implemented Trinity Pipeline** ready for validation and deployment.
