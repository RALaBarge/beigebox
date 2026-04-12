# HackerNews Launch Post

**Title:** BeigeBox: Security Control Plane for LLM Deployments (Open Source + SaaS)

**URL:** https://aisolutionsunlimited.com/beigebox

---

## Post Text (Copy-Paste into HN)

We built BeigeBox because we analyzed the Claude Code source leak and found 8 critical security bypasses. The root cause? Pattern-based security doesn't scale.

**What is BeigeBox?**
A proxy that sits between your app and any LLM backend (Claude, GPT-4, Ollama, etc.). Provides:
- Forensic audit logging (prove compliance to regulators)
- Injection detection (block prompt injection in real-time)
- RAG poisoning detection (catch poisoned knowledge bases)
- Extraction monitoring (behavioral analysis to prevent model theft)
- Policy as code (enforce rules across 10+ LLM instances)

**Architecture:**
We ditched pattern-based security and built isolation-first:
1. Isolation validator (actual filesystem behavior, not regex)
2. Allowlist enforcement (approved commands/options only)
3. Semantic detection (embedding anomalies)
4. Rate limiting (detect fuzzing)
5. Honeypots (canary files that trigger alerts)
6. Audit logging (forensic trail)

Result: 0 critical vulnerabilities. 1461 passing tests. Production ready.

**Open Source:**
GitHub: https://github.com/beigebox-ai/beigebox
Self-host for free on Ollama or point at any backend.

**Managed SaaS (Optional):**
$99/mo (indie) → $999/mo (enterprise)

**Why Now?**
LLM inference pricing is dropping 40-50% every 18 months. By 2027, it'll be nearly free. When that happens, security/governance becomes the profit center. We're positioning for that.

We're bootstrapped, looking for early adopters to validate the thesis.

HN, happy to answer questions in the comments.

---

## HN Pro Tips:
1. Post on Tuesday morning (9-10am PT for max visibility)
2. Be prepared to answer technical questions in comments
3. Link to GitHub prominently
4. Mention "open source" early (HN loves it)
5. Be humble about being bootstrapped (relatable)
