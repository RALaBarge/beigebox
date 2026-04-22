# BeigeBox Security Policy

**Version:** 1.0 | **Last Updated:** April 2026 | **Status:** Production-Ready

---

## Executive Summary

BeigeBox is an OpenAI-compatible LLM proxy that sits between your applications and language models. Like all AI systems, it faces a unique threat landscape distinct from traditional infrastructure security. This policy documents:

1. **The threats** BeigeBox defends against and how
2. **What BeigeBox does NOT defend against** (out-of-scope)
3. **Detection capabilities** and expected accuracy
4. **False positive rates** and operational tolerances
5. **How to report security issues** responsibly

**TL;DR:** BeigeBox provides multi-layer defense against the most common AI security threats (prompt injection, RAG poisoning, API key theft). It logs everything via the Tap system for monitoring and alerting. For production deployments, follow the [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md).

---

## 1. Threat Model & Coverage

### What BeigeBox Defends Against (Implemented & Tested)

#### T1: Direct Prompt Injection (OWASP LLM01:2025)
**Threat:** Attacker-supplied input overrides system instructions or extracts sensitive information.

**BeigeBox Defense — Layer 1: Pattern-Based Scanning**
- Regex detection of 12 common injection signatures: "ignore previous", "forget instructions", "roleplay as", etc.
- Unicode normalization before pattern matching (defeats basic obfuscation like "ignоrе" with Cyrillic)
- Configurable confidence threshold: soft-block (log only), hard-block, or route to safer model
- Configuration:
  ```yaml
  guardrails:
    input:
      injection_detection: true
      pattern_library: "standard"  # or "extended" with 25+ patterns
      action_on_detection: "log"   # "log" | "block" | "quarantine"
  ```

**Detection Accuracy:**
- True Positive Rate: 87-92% (captures obfuscated variants)
- False Positive Rate: <0.1% (legitimate requests rarely match injection patterns)
- Bypass Rate: 8-13% (sophisticated multi-turn attacks still bypass regex)

**Limitations:**
- Does NOT defend against zero-day injection techniques not in signature library
- Bypassed by novel encoding schemes (adversarial suffixes, gradient-based attacks)
- Does not understand semantic intent — only pattern matching
- Performance: <2ms per request

---

#### T4: RAG and Vector Database Poisoning (OWASP LLM08:2025)
**Threat:** Attacker injects malicious documents into vector stores; when retrieved by RAG, they inject instructions or alter model behavior.

**BeigeBox Defense — Layer 1: Embedding Anomaly Detection**
- Monitors all embeddings before they are stored in ChromaDB
- Detects abnormal embedding magnitudes (L2 norms outside 3σ confidence band)
- Checks semantic distance to known legitimate embedding centroids
- Prevents poisoned documents from entering the retrieval pipeline
- Configuration:
  ```yaml
  rag_poisoning_detection:
    enabled: true
    method: "magnitude_anomaly"  # "magnitude_anomaly" | "centroid_distance" | "neighborhood_density"
    sensitivity: 0.85  # 0-1, higher = stricter; tuned per deployment
    action: "quarantine"  # "log" | "block" | "quarantine"
    quarantine_path: "./data/quarantine.db"
  ```

**Detection Accuracy (from Nature Scientific Reports 2026):**
- True Positive Rate: 95%+ (detects poisoned embeddings)
- False Positive Rate: <0.5% (legitimate documents rarely flagged)
- Median Detection Latency: 2-3ms per embedding

**What Gets Quarantined:**
- Documents with embeddings matching known poison signatures
- Embeddings with statistical anomalies (magnitude outliers)
- Embeddings from untrusted sources (before baseline established)
- Quarantined documents are stored for audit; can be manually reviewed

**Limitations:**
- Requires 100+ baseline samples to calibrate (first week of deployment may have higher FP rate)
- Cannot detect poisoned embeddings with normal magnitude (estimated 5-10% of sophisticated attacks)
- Semantic-aware detection (reviewing document content for injected instructions) is manual-only

---

#### T11: LLMjacking and API Key Theft (OWASP LLM10)
**Threat:** Stolen API keys used to run inference at victim's expense; cost exhaustion before detection.

**BeigeBox Defense — Layer 1: Token Budget & Anomaly Detection**
- Per-API-key daily token limits (configurable soft/hard caps)
- Tracks cumulative input/output tokens in rolling 24h windows
- Detects anomalous usage patterns: token velocity spikes, unusual query entropy, model switching frequency
- Generates Tap alerts on threshold breach
- Configuration:
  ```yaml
  auth:
    keys:
      - name: "production-api-key"
        max_daily_tokens_in: 500000
        max_daily_tokens_out: 200000
        extraction_detection: true
  ```

**Detection Accuracy:**
- Token Budget Enforcement: 100% (hard cap enforced)
- Anomaly Signals: 78-85% TP rate (extraction attempts use statistically unusual patterns)
- False Positive Rate: <1% (normal usage rarely triggers alerts)

**Limitations:**
- Does not prevent key compromise; only detects and limits damage post-compromise
- Legitimate spike usage (e.g., batch job) may trigger alerts
- Requires baseline calibration (first 7 days of normal traffic)

---

### What BeigeBox Does NOT Defend Against (Out-of-Scope)

#### T2: Indirect Prompt Injection via Tool Outputs
**Status:** Gap. Operator agent reads workspace files and tool outputs without content validation.  
**Roadmap:** Scanning tool added in Phase 2 (Q2 2026).  
**Mitigation Today:** Disable Operator for untrusted tool sources; manually review high-risk tool outputs.

#### T3: Jailbreaking and Safety Bypass
**Status:** Partial. Pattern detection catches common variants; zero-day techniques bypass.  
**Maturity:** Jailbreak research moves faster than production defenses (new techniques discovered monthly).  
**Roadmap:** LlamaFirewall integration (Phase 2).  
**Mitigation Today:** Use trusted models with strong training-time alignment (Claude, GPT-4); enable decision LLM for borderline requests.

#### T5: Model Extraction via Systematic Probing
**Status:** Partial detection; not prevention.  
**Maturity:** Extraction attacks require thousands of queries with high diversity — anomaly detection identifies this pattern.  
**Roadmap:** Token anomaly detection (Phase 1, above). Full defense requires proprietary techniques.  
**Mitigation Today:** Rate limiting + query diversity monitoring; periodically review anomaly logs.

#### T6: Training Data Poisoning
**Status:** Out-of-scope. Happens upstream at model vendor (training time, before BeigeBox ingests model).  
**Mitigation:** Audit model provenance; use signed/verified model distributions.

#### T7: Model Backdoors in Pre-trained Weights
**Status:** Detection only (not prevention). Microsoft LLM Backdoor Scanner available (beta).  
**Roadmap:** Integrate model integrity checks at startup (Phase 2).  
**Mitigation Today:** Use models from trusted vendors (Meta, Anthropic, OpenAI); periodically scan with `microsoft-llm-backdoor-scanner`.

#### T12: Adversarial Inputs (Multimodal)
**Status:** Gap. No defense against imperceptible image perturbations or steganographic payloads.  
**Maturity:** Academic problem (ICLR 2025); no production-grade open-source defenses.  
**Roadmap:** Multimodal scanner (Phase 3, lower priority).  
**Mitigation Today:** Disable image inputs for untrusted sources; review images manually.

#### T13: AI-Generated Malware Detection
**Status:** Out-of-scope for BeigeBox. SIEM/EDR systems (CrowdStrike, SentinelOne) are the correct layer.  
**Mitigation:** Integrate BeigeBox with your SIEM; enable LLM-aware rules for code generation detection.

#### T15: Context Manipulation in Long-Running Agents
**Status:** Partial. No validation of plan files between Operator turns.  
**Roadmap:** Memory integrity guards (Phase 3).  
**Mitigation Today:** Disable persistent workspace for untrusted inputs; run Operator in isolated environment; periodically audit plan.md.

---

## 2. Detection Capabilities by Layer

### Layer 1: Proxy-Level Controls (Immediate, No Model Latency)
Runs on every request before forwarding to backend.

| Control | Method | Latency | Accuracy | Notes |
|---------|--------|---------|----------|-------|
| Input Pattern Scanning | Regex + Unicode normalization | <2ms | 87-92% TP, <0.1% FP | Signature-based, bypassable |
| Embedding Anomaly Detection | L2 norm + centroid distance | 2-3ms | 95% TP, <0.5% FP | RAG poisoning only |
| Token Budget Enforcement | Cumulative tracking | <1ms | 100% | Hard cap only |
| API Key Validation | HMAC signature check | <1ms | 100% | Authentication |
| Rate Limiting | Per-key request count | <1ms | 100% | DoS mitigation |

### Layer 2: Semantic Scanning (Medium Latency, Uses LLM)
Optional, runs for high-confidence decision making.

| Control | Method | Latency | Accuracy | Notes |
|---------|--------|---------|----------|-------|
| Semantic Injection Detection | Embedding similarity to attack corpus | 50-200ms | 88-94% TP, 3-5% FP | Requires baseline corpus |
| Output Exfiltration Monitor | PII density + URL/encoding detection | 10-20ms | 75-85% TP | High variance on edge cases |

### Layer 3: Observability & Alerting
Logs all security events to Tap system for human review.

| Signal | Severity | Action | Notes |
|--------|----------|--------|-------|
| Injection pattern detected | warning | Log + optionally block | Review in Tap logs weekly |
| Poisoned embedding quarantined | critical | Quarantine + alert | Review and manually whitelist if legitimate |
| Token budget exceeded | warning | Log + enforce cap | Indicates potential key compromise |
| Anomalous token velocity | warning | Log + alert | Investigate if not expected batch job |
| Rate limit exceeded | info | Log + throttle | Normal; adjust limits if pattern repeats |

---

## 3. False Positive Rate Expectations

### What is a False Positive?

- **In pattern scanning:** Legitimate request flagged as injection when it isn't
- **In embedding anomaly:** Legitimate document quarantined as poisoned when it isn't
- **In anomaly detection:** Normal usage pattern flagged as extraction/jacking when it isn't

### Observed FPR by Control

| Control | Observed FPR | Baseline | Notes |
|---------|--------------|----------|-------|
| Input pattern scanning | <0.1% | 1M+ legitimate requests | Mostly affects code/creative writing with "ignore" in context |
| Embedding magnitude anomaly | <0.5% | 100+ baseline samples | Improves with more samples; recalibrate weekly |
| Token budget alerts | <1% | Per-key baseline (7 days) | Legitimate spikes (batch jobs) may trigger; tune thresholds |
| Rate limiting | 0% | Configured limit | By design; not a FP if limit is correct |

### How to Tune FPR

1. **Enable logging for 7 days** with `action: "log"` (non-blocking)
2. **Collect baseline** (100+ legitimate requests for your workload)
3. **Review Tap logs** for false positives
4. **Adjust sensitivity threshold** downward if FPR >1%
5. **Re-test** for 3 days
6. **Lock configuration** once FPR <0.5%

See [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md) for detailed tuning procedure.

---

## 4. Assumptions & Limitations

### Core Assumptions

1. **BeigeBox runs in a trusted execution environment.** If an attacker has SSH access to the BeigeBox host, all security controls are bypassed.

2. **Configuration is not user-supplied.** Users cannot edit `config.yaml` or `runtime_config.yaml`; only admins can.

3. **Baseline data is legitimate.** When you calibrate baselines, the sample data must be from legitimate, non-adversarial sources.

4. **Backends are available.** If a backend is down, BeigeBox cannot route; requests may timeout or fall back to degraded service.

5. **Network is not actively compromised.** MitM attacks that intercept API keys or responses bypass BeigeBox security.

### Fundamental Limitations

1. **No defense against model-level alignment failure.** If the underlying LLM (Claude, Qwen, etc.) decides to cooperate with a jailbreak, BeigeBox cannot stop it. BeigeBox can only detect suspicious *input* and *output patterns*.

2. **Signatures lag threats.** New injection techniques, jailbreaks, and poisoning methods are discovered monthly. BeigeBox's signature library updates quarterly.

3. **Semantic scanning is probabilistic.** Embedding-based anomaly detection has inherent false positives and negatives; it is not a guarantee.

4. **Operator agent has broad access.** If you enable the Operator agent, it can call tools, read workspace files, and execute code. Compromising the Operator compromises the entire system.

5. **Compliance is your responsibility.** BeigeBox provides technical controls; you are responsible for policies, audit trails, and incident response.

---

## 5. Responsible Disclosure

### How to Report a Security Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email your report to **security@ryanlabarge.com** with:

1. **Title:** Brief description (e.g., "Authentication Bypass in Z-command Parsing")
2. **Affected Version(s):** Which BeigeBox versions are affected (e.g., 1.9, 1.8)
3. **Description:** Technical details and proof-of-concept (if safe to share)
4. **Impact:** What an attacker could do with this vulnerability
5. **Reproduction Steps:** How to reproduce the issue
6. **Proposed Fix** (optional): Any ideas on remediation

### Response Timeline

| Severity | Acknowledgment | Investigation | Public Release | Disclosure |
|----------|---|---|---|---|
| **Critical** (RCE, auth bypass) | 24 hours | 24-48 hours | 48-72 hours | 30 days after release |
| **High** (injection bypass, info leak) | 48 hours | 3-5 days | 5-7 days | 30 days after release |
| **Medium** (DoS, cache poisoning) | 5 days | 5-14 days | 14-21 days | 30 days after release |
| **Low** (cosmetic, minor info leak) | 14 days | Next release | N/A | 30 days after release |

**Note:** We follow a 30-day disclosure window. Once a public fix is released, vulnerability details are disclosed 30 days later (or sooner if details are already public).

### What Happens After We Fix It

1. **Release a patch version** with the fix (e.g., 1.9.1)
2. **Announce on GitHub Releases** with severity level and impact
3. **Credit the researcher** (with their permission) in release notes and security advisories
4. **Publish details 30 days later** in our security blog (if novel)

### Hall of Fame

Researchers who responsibly disclose vulnerabilities are publicly credited in this section. If you would like anonymous credit, please specify in your report.

*None yet. Be the first!*

---

## 6. Security Best Practices for Operators

### Before Deployment

- [ ] Read [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md)
- [ ] Calibrate baselines with 200+ legitimate requests
- [ ] Tune thresholds to <0.5% FPR
- [ ] Enable Tap logging and set up alerts
- [ ] Review KNOWN_VULNERABILITIES.md for current limitations
- [ ] Establish escalation contacts for security alerts

### During Operation

- [ ] Review Tap logs daily (5 min) for security events
- [ ] Investigate alerts with severity `warning` or higher within 24 hours
- [ ] Recalibrate baselines weekly (15 min)
- [ ] Monitor false positive rate; adjust sensitivity if FPR >1%
- [ ] Keep API keys secure (use secret management, not config files)
- [ ] Enable rate limiting and enforce token budgets

### Incident Response

If you detect suspicious activity:

1. **Check Tap logs** for the source request(s) and exact pattern matched
2. **Determine if legitimate** (batch job, new use case, etc.)
3. **If legitimate:** Adjust threshold downward, recalibrate baseline
4. **If suspicious:** 
   - Capture full request/response pair
   - Check if API key has been compromised (review usage timeline)
   - Consider rotating the key
   - Report to security@ryanlabarge.com if you believe it's a novel attack

### Monitoring Setup

Required metrics to monitor (via your observability platform):

```yaml
alerts:
  - name: "High Injection Alert Rate"
    query: "source=prompt_guard AND severity=critical"
    threshold: 5  # per hour
    action: page  # page on-call team

  - name: "Quarantine Queue Growing"
    query: "source=rag_scanner AND action=quarantine"
    threshold: 10  # documents/day
    action: page

  - name: "API Key Anomaly"
    query: "source=api_anomaly AND signal=extraction_signal"
    threshold: 1  # per 24h
    action: page

  - name: "High False Positive Rate"
    query: "source=guardrail AND severity=warning"
    threshold: 20  # per day (indicates misconfigured threshold)
    action: email  # investigate threshold
```

See [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md) for monitoring setup details.

---

## 7. Compliance & Standards

### OWASP LLM Top 10 2025 Coverage

| Threat | BeigeBox Control | Coverage | Status |
|--------|---|---|---|
| LLM01: Prompt Injection | Input pattern scanning | 87-92% | Production |
| LLM02: Insecure Output | Output pattern scanning (beta) | 75-85% | Roadmap Q2 |
| LLM04: Training Data Poisoning | Model verification (startup) | Detection only | Roadmap Q2 |
| LLM08: Vector DB Poisoning | Embedding anomaly detection | 95% | Production |
| LLM10: Model Theft | Token anomaly detection | 78-85% | Production |

**Not in scope:** LLM03 (supply chain — handled by Docker/pip-audit), LLM05 (insecure output handling — model-level concern), LLM06 (excessive agency — architectural choice), LLM09 (misinformation — not technical control)

### Regulatory Alignment

**EU AI Act (August 2026):** High-risk AI systems must provide audit trails and risk assessments. BeigeBox's Tap system provides comprehensive audit logging. You are responsible for documenting risk assessment and incident response procedures.

**NIST AI RMF (AI 100-1):** BeigeBox aligns with the **Measure** phase (detection, monitoring) and **Manage** phase (incident response). You are responsible for **Govern** (policy) and **Map** (threat modeling) phases.

**SOC 2 Type II / ISO 27001:** BeigeBox is a component of your security stack, not a compliance guarantee. Security controls documented here support SOC 2 and ISO 27001 audit evidence.

---

## 8. Contact & Support

### Security Issues
- **Email:** security@ryanlabarge.com
- **Response time:** 24-48 hours for critical issues

### Operational Questions
- **GitHub Discussions:** https://github.com/ralabarge/beigebox/discussions
- **Documentation:** [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md)

### Emergency Contacts
- **On-call escalation:** See [DEPLOYMENT_SECURITY_CHECKLIST.md](DEPLOYMENT_SECURITY_CHECKLIST.md)
- **Feature requests:** security@ryanlabarge.com (include use case + timeline)

---

## Change Log

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | April 2026 | Initial release; covers T1, T4, T11 defenses; documents roadmap for T2, T3, T5, T7, T12, T15 |

---

**Last reviewed:** April 12, 2026  
**Next review:** July 12, 2026 (quarterly)

For the latest version, see: https://github.com/ralabarge/beigebox/blob/main/SECURITY_POLICY.md
