# Email Launch Template

**To:** Your network (CTOs, security leads, developers you know)

**Subject:** BeigeBox launched — LLM security proxy (open source + SaaS)

---

## Email Body (Customize as Needed)

Hi [Name],

I built something I think you'll find useful: **BeigeBox**, a security control plane for LLM deployments.

**Quick background:** I analyzed the Claude Code source leak and realized pattern-based security doesn't scale. So I built an isolation-first architecture with 6 layers of defense (audit logging, injection detection, RAG poisoning protection, extraction monitoring, honeypots, policy-as-code).

**What it does:**
- Sits between your app and any LLM backend (Claude, GPT-4, Ollama, etc)
- Logs every LLM call with full context (prove compliance to regulators)
- Detects injection attacks in real-time
- Blocks RAG poisoning
- Monitors for model extraction

**Open source (free):** Self-host on Ollama or any backend. No credit card.
https://github.com/beigebox-ai/beigebox

**Managed SaaS (optional):** $99/mo (indie) → $999/mo (enterprise)
https://aisolutionsunlimited.com/beigebox

**Current status:** 
- Phase 1 complete (0 critical vulns, 1461 passing tests, production ready)
- Bootstrapped, looking for early adopters & feedback
- We're planning to support compliance certifications (SOC 2, HIPAA, GDPR) in the coming months

I'd love your feedback, especially if you're:
- Running LLMs in production
- Working on security/compliance
- Interested in the "LLM security as control plane" idea

Give the open-source version a try if you have 30 minutes. Would love to hear what you think.

GitHub: https://github.com/beigebox-ai/beigebox  
SaaS: https://aisolutionsunlimited.com/beigebox

Questions? Hit reply or email hello@aisolutionsunlimited.com

Cheers,  
[Your Name]

---

## Who to Send This To

Priority order:
1. **CTOs / Security leads** at companies running LLMs
2. **Developers** you know who are interested in security/AI
3. **Startup founders** using LLMs
4. **Compliance officers** (might want the managed SaaS)
5. **GitHub followers** if you have any

Target: 20-50 personal emails. Keep it genuine (no mass mailing tone).

---

## Subject Line Alternatives

If the default doesn't feel right:
- "Built something for LLM security (open source)"
- "We're launching BeigeBox — security for LLMs"
- "Quick question: How do you log your LLM calls?"
- "New project: LLM audit logging + injection detection"

---

## Personalizing Tips

For each email, customize:
- Use their name
- Reference something you have in common (previous conversation, mutual interest)
- Make it short (60 seconds to read)
- Ask for specific feedback: "Curious what you think about the isolation-first approach" or "Would you use this for your stack?"

---

## Follow-up (If No Response)

After 1 week, if they don't respond:
- Don't send a follow-up (too pushy)
- Just mention it naturally in your next conversation
