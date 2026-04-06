# Agents & Orchestration

BeigeBox includes agentic features for multi-turn orchestration, browser automation, and RAG.

## Operator (Agentic automation)

The **operator** is a long-horizon agent that can:
- Control a real browser (navigate, click, type, screenshot)
- Search and index documents (RAG)
- Execute tools (API calls, calculations)
- Manage multi-turn workflows

Enable in `config.yaml`:

```yaml
feature_flags:
  operator:
    enabled: true
```

Then use via API:

```bash
curl -X POST http://localhost:1337/api/v1/operator/execute \
  -H "Authorization: Bearer sk-..." \
  -d '{
    "task": "Find the price of Tesla stock and summarize the latest news",
    "context": {}
  }'
```

Response:
```json
{
  "run_id": "op-abc123",
  "status": "queued"
}
```

Poll for status:
```bash
curl http://localhost:1337/api/v1/operator/op-abc123
```

## Harness (Multi-turn orchestration)

The **harness** coordinates multi-turn interactions between agents:

```python
from beigebox.agents.harness_orchestrator import Harness

harness = Harness(
    models=["llama3.1:8b", "qwen2.5:7b"],
    mode="ensemble",  # or "orchestrated"
    num_turns=5
)

result = harness.run(
    task="Explain quantum computing",
    context={}
)
```

Modes:
- **ensemble** — parallel, all models respond
- **orchestrated** — sequential, judges best response per turn

## Multi-turn conversation

Link requests via `session_id` to maintain context:

```json
{
  "model": "llama3.1:8b",
  "messages": [{"role": "user", "content": "What is 2+2?"}],
  "_window_config": {
    "session_id": "sess-abc123"
  }
}
```

Second request on same session reuses context.

## Group chat

Multiple participants discussing a topic:

```bash
curl -X POST http://localhost:1337/api/v1/group-chat \
  -d '{
    "topic": "Best programming languages for AI",
    "participants": ["user", "llama3.1:8b", "qwen2.5:7b"],
    "num_turns": 5
  }'
```

Each model responds in turn, with full conversation history.

## RAG (Document search)

Search your indexed documents:

```bash
curl "http://localhost:1337/api/v1/document-search?q=machine+learning&limit=5"
```

Enable in `config.yaml`:

```yaml
feature_flags:
  rag_enabled: true

rag:
  doc_path: /app/data/docs
  chunk_size: 512
```

See [Configuration](configuration.md) for setup.

---

See [Architecture](architecture.md) for how these integrate into the request pipeline.
