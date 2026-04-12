# Reddit Launch Posts

## Post 1: /r/programming

**Title:** BeigeBox: Open-source LLM Security Control Plane (We analyzed the Claude Code leak and built something better)

**Post:**

We analyzed the Claude Code source leak and found that **pattern-based security doesn't scale**. 8 critical bypasses, all variations on fundamental attack patterns.

So we built BeigeBox: a proxy that sits between your app and any LLM backend (Claude, GPT-4, Ollama, etc) and provides:
- Forensic audit logging (prove compliance to regulators)
- Injection detection (semantic + pattern-based)
- RAG poisoning detection (catch poisoned knowledge bases)
- Extraction monitoring (behavioral analysis to prevent model theft)
- Policy as code (enforce security rules across your LLM fleet)

**Architecture:** Isolation-first (not pattern-based). 6 layers of defense. 0 critical vulns. Production ready.

**Open source (free):** https://github.com/beigebox-ai/beigebox

**Managed SaaS (optional):** $99-999/mo depending on scale

We're bootstrapped and looking for early adopters. Happy to answer questions.

---

## Post 2: /r/selfhosted

**Title:** BeigeBox: Self-hosted LLM Security Proxy (Open Source)

**Post:**

If you're self-hosting Llama, Ollama, or pointing at OpenAI/Claude, you might want to add a security layer.

We built BeigeBox as a proxy that:
- Logs every LLM call with full context (audit trail)
- Detects prompt injection in real-time (semantic + pattern-based)
- Blocks RAG poisoning attempts
- Monitors for model extraction attacks
- Lets you write security policies once, apply everywhere

**Self-hosted, open-source, no cloud required:** https://github.com/beigebox-ai/beigebox

Works with Ollama, OpenRouter, anything OpenAI-compatible. Docker compose setup in the README.

Code is production-ready (1461 tests passing, 0 critical vulns). We documented the architecture based on analyzing the Claude Code source leak.

GitHub: https://github.com/beigebox-ai/beigebox

Feedback welcome. We're bootstrapped and iterating based on early user feedback.

---

## Post 3: /r/selfhosted (Alternative Angle)

**Title:** Running Ollama locally? Add BeigeBox for security logging

**Post:**

Quick question for the self-hosted LLM crowd: How are you logging what your local LLMs do?

We built BeigeBox to solve this. It's a simple proxy that logs every LLM call (who, what, when, model used, etc) and detects injection attacks.

**Why it matters:**
- Local doesn't mean untrustworthy. You might run LLMs for business/sensitive work.
- Audit logs are table-stakes if you ever need to prove compliance.
- Injection detection catches unusual requests before they execute.

**How it works:**
1. Run BeigeBox as a proxy (docker-compose up)
2. Point your Ollama/app to BeigeBox instead of Ollama directly
3. Get audit logs + security monitoring automatically

**Open source:** https://github.com/beigebox-ai/beigebox

We're early-stage, looking for feedback from the self-hosted community.

---

## Reddit Pro Tips:
1. Post Tuesday afternoon (2-4pm PT, when US is active)
2. Be authentic (mention you're bootstrapped, want feedback)
3. Answer every question in comments (builds trust)
4. Don't be spammy (1 post per subreddit max)
5. Link to GitHub prominently (credibility)
6. Mention it's free/open-source early (HN/Reddit loves it)
