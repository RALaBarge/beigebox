# Quick Model Comparison (What People Say)

| Metric | qwen3:4b | gemma3:4b | llama3.2:3b |
|--------|----------|-----------|------------|
| **Quality** | ⭐⭐⭐⭐ Best | ⭐⭐⭐ Good | ⭐⭐ OK |
| **Speed** | Slow (thinking mode) | Fast | ⭐⭐⭐⭐ Fastest |
| **VRAM** | 2.5GB | 2.5GB | 1.5GB |
| **Tool Use** | ⭐⭐⭐⭐ Great | ⭐⭐⭐ Good | ⭐⭐ Meh |
| **Routing/Judge** | ⚠️ Slow | OK | ⭐⭐⭐⭐ Best |
| **Cost (cloud)** | Expensive | Mid | Cheap |
| **General Chat** | Best answers | Good | Quick answers |
| **Common Use** | Default model | Fallback | Judge/routing |

---

## The Verdict (from your own docs)

From your `2600/docs/routing.md`:

> **"qwen3-family models use a thinking mode that burns tokens before answering, adding latency even for 5-token routing responses. Use llama3.2:3b or another non-thinking model for the routing judge."**

---

## Summary

- **qwen3:4b** — Best quality, but slow. Good for default/chat. BAD for routing.
- **gemma3:4b** — Middle ground. Your config has it everywhere (not ideal).
- **llama3.2:3b** — Fast, lightweight. Perfect for routing/judge decisions.

---

## Your Current Mess

| Config | default | routing | agentic | summary |
|--------|---------|---------|---------|---------|
| **config.yaml** | gemma3 | gemma3 ❌ | gemma3 | gemma3 |
| **docker config** | qwen3 | llama3.2 ✅ | qwen3 | qwen3 |

**Your docker config is better.** Routing with llama3.2:3b is the right call.
