# Show HN Post Candidates — My 2 Favorites

## #1: Qwen 3.6 Plus — Temperature 0.7 (261 words) ⭐⭐⭐

Code review at scale breaks down because context windows fill up, models drift across files, and architectural contradictions slip through when reviewers only see diffs. I built two production tools to address this:

**garlicpress** (https://github.com/RALaBarge/garlicpress) is an open-source distributed code review engine. It uses a stateless map/reduce/swap architecture to reset context per file, compress directory structure, and surface cross-file contradictions.

**beigebox** (https://github.com/RALaBarge/beigebox) is the production LLM proxy that powers it. It's a FastAPI gateway handling latency-aware routing, semantic caching, a decision-tier router, and an operator agent with web search, shell, and filesystem tools.

This matters because traditional AI reviewers drown in bloated prompts and miss systemic bugs. By forcing stateless, hierarchical evaluation, we get reproducible, architecture-aware audits instead of vague suggestions.

I ran garlicpress across 7 production codebases (C, Go, TypeScript, Python, Lua, Elixir, Haskell) and cross-validated with 12 LLMs (3 local, 9 cloud). The consensus was consistent: it surfaced 39 critical issues with PR-ready fixes at a transparent cloud cost of $0.694. On a 4-file C repo called `andsh`, for example, it caught an architectural assumption gap invisible at the single-file level.

This isn't AI magic—just a structured pipeline that makes model outputs deterministic and auditable. The full evaluation data, cost breakdowns, and reproduction scripts are public. Both projects are used internally, have zero stylistic debt, and ship with exactly one TODO for phase two.

Try it on your own repos, route it through Ollama or OpenRouter, and let me know what breaks. I'm particularly interested in edge cases across unfamiliar languages and how the proxy handles concurrent traffic spikes.

---

**Why this one:** 
- Natural cadence, grounded language
- Specific examples (andsh)
- Transparent about what it is (not magic)
- Invites feedback
- Strong call to action
- Hits HN tone perfectly

---

## #2: DeepSeek — Temperature 0.9 (237 words) ⭐⭐⭐

**Show HN: garlicpress and beigebox – Code review at scale and LLM proxy**

Code review at scale breaks down because developers lack architectural context across files, and LLMs often lose coherence when reviewing large codebases.

We built **garlicpress** (https://github.com/RALaBarge/garlicpress) to solve this: an open-source distributed code review engine that evaluates codebases by resetting context per file (eliminating drift), compressing through directory hierarchy, and finding architectural contradictions across files. For example, it spotted an architectural assumption gap in a 4-file C repo (`andsh`) where two files disagreed on error-handling logic.

We also built **beigebox** (https://github.com/RALaBarge/beigebox), a production-grade LLM proxy that handles routing, semantic caching, and logging. It sits between your frontend and any backend (Ollama, OpenRouter, etc.), ensuring low latency and reliability.

Why this matters: Both tools are validated against real-world use. For garlicpress, we tested 7 production codebases (C, Go, TypeScript, etc.) using 12 LLM models (3 local free, 9 cloud paid) and found 39 critical issues with PR-ready fixes. Total cloud validation cost? $0.694. For beigebox, it handled 2,929 API calls under stress with zero failures.

This isn't AI magic—just good engineering. Both are production-grade, used internally, and built with clean, documented code (zero stylistic debt, one legitimate TODO for phase 2 work).

Try it: Spin up garlicpress on your codebase or deploy beigebox as your LLM proxy. Let me know what you find—feedback welcome! Reproducible results, transparent data, no hype.

Links:
garlicpress: https://github.com/RALaBarge/garlicpress
beigebox: https://github.com/RALaBarge/beigebox

---

**Why this one:**
- Crisp problem statement
- Concrete example with error handling
- "Why this matters" section adds credibility
- Emphasis on "not AI magic"
- Results speak for themselves (2,929 calls, 0 failures)
- Clean structure
- Shorter (better for HN reader attention)

---

## Recommendation

Post at **9 AM EST tomorrow**. I'd lean **#1 (Qwen)** for the HN audience—it reads more naturally and has better flow. But both are solid and hit all your constraints.

Edit as you see fit—these are templates.
