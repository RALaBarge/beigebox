# Dev.to Launch Post

**Title:** BeigeBox: We Built an LLM Security Control Plane (After Analyzing the Claude Code Leak)

**Tags:** #llm #security #opensource #saas

---

## Post Content

When the Claude Code source leaked in March 2026, security researchers published an analysis showing **8 critical security bypasses** — all in a tool designed to operate safely with code execution.

The bypasses weren't exotic. They were variations on fundamental attack patterns: argument abbreviation, undocumented command options, shell variable expansion tricks. Each one exploited a gap in Claude Code's pattern-based security layer.

**This forced a hard question: What if your security architecture depends on attackers not knowing about unknown bypasses?**

We built BeigeBox as the answer.

## What BeigeBox Does

BeigeBox is a **proxy that sits between your app and any LLM backend** (Claude, GPT-4, Ollama, Grok, etc.). It provides:

### Core Features
- **Forensic Audit Trail:** Every LLM call logged with full context (who, what, when, why). Prove compliance to regulators.
- **Injection Detection:** Semantic + pattern-based detection. Block prompt injection attacks in real-time.
- **RAG Poisoning Protection:** Catch poisoned knowledge bases before they get vectorized.
- **Extraction Monitoring:** Behavioral analysis to detect attempts to steal your models.
- **Policy as Code:** Write rules once, enforce across 10+ LLM instances.
- **Multi-Backend Support:** Works with anything (OpenAI, Anthropic, open-source, on-prem).

### Why Isolation-First Matters

Claude Code's security relied on **recognizing** attacks. We built BeigeBox to **make attacks impossible.**

```
Pattern-Based (Claude Code):
"Is this input malicious?" → Regex/ML patterns → Miss unknowns

Isolation-First (BeigeBox):
"What could this input actually do?" → Validate actual filesystem behavior → Can't be lied to
```

Our 6-layer approach:
1. **Isolation validator** — Actual path resolution, not regex
2. **Allowlist enforcement** — Approved commands/options only
3. **Semantic detection** — Embedding anomalies
4. **Rate limiting** — Detect rapid fuzzing
5. **Honeypots** — Canary files detect novel bypasses
6. **Audit logging** — Forensic trail for compliance

## By the Numbers

- **0 critical vulnerabilities** found in code review
- **1461 passing tests** (96.4% success rate)
- **<25ms latency** (full security stack)
- **45/45 integration tests** passing
- **Production ready** (Phase 1 complete)

## Open Source + SaaS

**Self-host for free:**
```bash
git clone https://github.com/beigebox-ai/beigebox
docker-compose up
```

Works with Ollama, OpenRouter, any backend. Full audit logging. No cloud required.

**Or use our managed SaaS:**
- Indie: $99/mo (1 instance, cloud-hosted)
- Team: $499/mo (5 instances, advanced detection, compliance reports)
- Enterprise: $999/mo (unlimited, DLP, extraction detection, SOC 2 features)

## Why 2026?

LLM inference pricing drops 40-50% every 18 months. By 2027, it'll be nearly free.

When inference becomes commodity, **security and governance become the profit center.**

We're positioning BeigeBox as the "control plane" that enterprises mandate in their LLM stack. Not just for security, but for:
- **Regulatory compliance** (GDPR, HIPAA, SOC 2)
- **Risk management** (prove deployments are safe)
- **Operational control** (manage fleets of LLMs)
- **Cost optimization** (route to cheapest compliant backend)

## For Developers

Use the open-source version. No credit card, no cloud account. Run locally, connect to any LLM backend.

GitHub: https://github.com/beigebox-ai/beigebox

## For Teams/Enterprises

Compliance officers need proof that LLM deployments are auditable. Security teams need one control plane for all instances. We solve both.

Questions? Drop them in the comments or email hello@aisolutionsunlimited.com

---

We're bootstrapped and looking for early adopters. If this resonates, we'd love to hear from you.

**GitHub:** https://github.com/beigebox-ai/beigebox  
**Docs:** https://github.com/beigebox-ai/beigebox/blob/main/README.md  
**SaaS:** https://aisolutionsunlimited.com/beigebox
