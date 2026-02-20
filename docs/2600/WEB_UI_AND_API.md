# BeigeBox Web UI & API v1 Endpoints

## Overview

BeigeBox now includes:
1. **Web UI** — lightweight vanilla HTML/CSS/JS chat interface
2. **API v1 endpoints** — standardized, discoverable endpoints for the web UI (and other clients)
3. **Failover resilience** — chat always works, even if other features break

---

## Web UI

### Accessing It

```
http://localhost:8000/
http://localhost:8000/ui
```

### Features

- **Model selector** — dropdown that refreshes models every time you click (live from backend)
- **Chat interface** — send messages, get responses, track token usage
- **Status indicator** — shows connection status and available models
- **Conversation tracking** — auto-generated conversation ID, visible in info bar
- **Z-commands support** — type `z: operator what models are running?` to query the operator

### Design

- Pure vanilla HTML/CSS/JS — **zero dependencies**, **never needs updating**
- No frameworks, no build tools, no npm
- Single file (index.html) — hand-editable
- Responsive, dark theme with lavender accents
- Auto-resizing textarea
- Ctrl+Enter to send (also works with Cmd on Mac)

### Error Handling

- Connection fails? Shows error message, user can still type/retry
- No models? Shows "No models available" but chat interface stays up
- Bad response? Displays error in chat with status update

---

## API v1 Endpoints

All responses are JSON. All endpoints gracefully degrade (no 500 errors on partial failures).

### Core Chat

**POST /v1/chat/completions**
- OpenAI-compatible chat endpoint
- Required body:
  ```json
  {
    "model": "model-name",
    "messages": [
      {"role": "user", "content": "Hello"},
      {"role": "assistant", "content": "Hi!"}
    ]
  }
  ```
- Optional:
  - `stream: true/false` (default: false)
  - `conversation_id: "uuid"` (auto-tracked)
- Returns: OpenAI format

**GET /v1/models**
- List available models
- Returns:
  ```json
  {
    "data": [
      {"id": "llama3.2", "name": "llama3.2", "model": "llama3.2"},
      ...
    ]
  }
  ```

---

## System Information & Status

### GET /api/v1/info

General system information — what features are available.

```json
{
  "version": "0.2.0",
  "name": "BeigeBox",
  "description": "Transparent Pythonic LLM Proxy",
  "server": {"host": "0.0.0.0", "port": 8000},
  "backend": {"url": "http://localhost:11434", "default_model": "llama3.2"},
  "features": {
    "routing": true,
    "decision_llm": false,
    "embedding_classifier": false,
    "storage": true,
    "tools": true,
    "hooks": true,
    "operator": true
  },
  "model_advertising": "hidden"
}
```

Use this to:
- Check which features are enabled
- Know if decision LLM is available
- Check if storage is working
- See model advertising mode

---

### GET /api/v1/config

Current configuration (safe to expose to clients).

```json
{
  "backend": {"url": "...", "default_model": "..."},
  "server": {"host": "0.0.0.0", "port": 8000},
  "embedding": {"model": "nomic-embed-text"},
  "storage": {"log_conversations": true},
  "tools": {"enabled": true},
  "decision_llm": {
    "enabled": false,
    "model": ""
  },
  "model_advertising": {"mode": "hidden", "prefix": "beigebox:"}
}
```

Use this to:
- Verify storage is enabled
- Check tools availability
- See model advertising settings
- Confirm decision LLM status

---

### GET /api/v1/status

Detailed status of all subsystems.

```json
{
  "proxy": {
    "running": true,
    "backend_url": "http://localhost:11434",
    "default_model": "llama3.2"
  },
  "storage": {
    "sqlite": true,
    "vector": true,
    "stats": {
      "conversations": 42,
      "messages": 156,
      "tokens": 12345
    }
  },
  "routing": {
    "decision_llm": {"enabled": false, "model": ""},
    "embedding_classifier": {"ready": false}
  },
  "tools": {
    "enabled": true,
    "available": ["web_search", "calculator", "memory", ...]
  },
  "operator": {
    "model": "llama3.2:2b",
    "shell_enabled": true
  }
}
```

Use this to:
- Monitor system health
- Check available tools
- Verify storage stats
- See if operator is configured

---

### GET /api/v1/stats

Usage statistics.

```json
{
  "conversations": {
    "total": 42,
    "today": 5,
    "average_length": 3.7
  },
  "embeddings": {
    "total": 156,
    "indexed": 155
  },
  "timestamp": "2026-02-20T01:30:00.000Z"
}
```

Use this to:
- Track conversation metrics
- Monitor storage capacity
- Time-based analysis

---

### GET /api/v1/tools

List available tools.

```json
{
  "enabled": true,
  "tools": [
    "web_search",
    "web_scraper",
    "calculator",
    "datetime",
    "system_info",
    "memory"
  ]
}
```

Use this to:
- Build dynamic UI for tool selection
- Check tool availability before requesting them

---

## Search & Data

### GET /beigebox/search?q=query&n=5&role=user

Semantic search over conversations.

Query params:
- `q` — search query (required)
- `n` — number of results (default: 5)
- `role` — filter by "user" or "assistant" (optional)

Returns:
```json
{
  "query": "docker networking",
  "results": [
    {
      "distance": 0.15,
      "metadata": {"role": "user", "model": "llama3.2", "timestamp": "..."},
      "content": "How do I configure Docker networks?..."
    },
    ...
  ]
}
```

---

## Operator Agent

### POST /api/v1/operator

Run the operator agent (local LLM with access to data, web, shell).

Request:
```json
{
  "query": "Show me conversations about authentication"
}
```

Response:
```json
{
  "success": true,
  "query": "Show me conversations about authentication",
  "answer": "Based on semantic search, you discussed authentication in 3 conversations..."
}
```

On failure:
```json
{
  "success": false,
  "error": "Operator not initialized",
  "query": "..."
}
```

**Use this to**:
- Query the operator from web UI
- Run semantic searches programmatically
- Ask complex questions about your data
- Analyze system state

---

## Health & Monitoring

### GET /beigebox/health

Quick health check.

```json
{
  "status": "ok",
  "version": "0.2.0",
  "decision_llm": false
}
```

Use this for:
- Monitoring/alerting systems
- Load balancer health checks
- Basic connectivity tests

---

## Design Principles

### 1. **Data-Driven**
All endpoints return complete, structured data. Web UI (or any client) builds interface dynamically from responses. Never hardcoded assumptions.

### 2. **Discoverable**
Client can call `/api/v1/info` to learn what's available, then adapt behavior accordingly.

### 3. **Versioned**
All new endpoints under `/api/v1/`. Old endpoints stay forever. Breaking changes → `/api/v2/`.

### 4. **Graceful Degradation**
- Missing feature? Returns `enabled: false`
- Vector store down? Returns empty search results
- Operator unavailable? Returns `success: false` instead of 500
- Chat always works, even if everything else breaks

### 5. **No Frontend Baggage**
Single HTML file, vanilla JS. Doesn't need updating because it doesn't assume anything about the server. If you add a new endpoint, web UI adapts automatically (e.g., model list refreshes on dropdown click).

---

## Integration Examples

### Python Client

```python
import requests
import httpx

# Chat
response = httpx.post("http://localhost:8000/v1/chat/completions", json={
    "model": "llama3.2",
    "messages": [{"role": "user", "content": "Hello"}],
    "conversation_id": "conv-123"
})
print(response.json()["choices"][0]["message"]["content"])

# Check system info
info = requests.get("http://localhost:8000/api/v1/info").json()
print(f"Features: {info['features']}")

# Search conversations
results = requests.get("http://localhost:8000/beigebox/search", 
    params={"q": "docker", "n": 5}).json()
for hit in results["results"]:
    print(f"- {hit['content'][:100]}...")

# Run operator
response = requests.post("http://localhost:8000/api/v1/operator", json={
    "query": "What models are loaded?"
})
print(response.json()["answer"])
```

### cURL Examples

```bash
# List models
curl http://localhost:8000/v1/models | jq

# Send message
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"hi"}]}'

# System info
curl http://localhost:8000/api/v1/info | jq

# Operator query
curl -X POST http://localhost:8000/api/v1/operator \
  -H "Content-Type: application/json" \
  -d '{"query":"Show recent conversations"}'

# Search
curl "http://localhost:8000/beigebox/search?q=docker&n=5" | jq
```

---

## Future-Proof Design

**Why this will never need updating:**

1. **Web UI is vanilla HTML/JS** — web standards are stable, backward compatible by design
2. **Endpoints are versioned** — breaking changes go to v2, v1 stays forever
3. **Responses are self-describing** — new fields can be added without breaking clients
4. **No assumptions about structure** — web UI reads what's there, ignores what's not
5. **Graceful failures** — missing features don't crash, just return `false`

Add new features? Add new endpoints. Deprecate old ones? Keep them for 5+ years. The web UI and API clients will keep working because they're designed to adapt, not assume.

---

**Status**: ✓ Ready to use  
**Compatibility**: ✓ Backward compatible  
**Maintenance**: ✓ Zero — web standards don't change in breaking ways  
**Extensibility**: ✓ Add endpoints without touching existing code
