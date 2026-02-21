# Conversation Replay Design (v0.6.0)

## Overview

**Conversation Replay** reconstructs any conversation with full routing context, showing which models were used, why, and what tools were invoked.

## Problem Statement

After a conversation, hard to understand:
- Why was model X chosen for that message?
- Did the routing decision make sense?
- What tools were used and why?
- How did the conversation flow evolve?

Replay answers all these questions.

## Design

### Data Captured

For each message in a conversation:
- Content
- Role (user/assistant)
- Model used
- Routing method (cache/z-command/embedding/decision-llm)
- Routing confidence
- Tools invoked
- Token counts
- Timestamp

### Reconstruction

Pull from multiple sources:
1. **messages** table (SQLite) — message content, model, tokens
2. **wiretap log** (JSONL) — routing decisions, tools, details
3. **ChromaDB** — embeddings for similarity analysis

Reconstruct timeline:
- Match messages to routing decisions (by timestamp)
- Extract tool invocations (from wiretap)
- Calculate stats (total tokens, models used, etc.)

### Output Format

```
CONVERSATION REPLAY: conv-abc123

[Conversation created: 2026-02-20 14:00:00]
[Total messages: 12 | Total tokens: 4,567 | Duration: 15 minutes]

────────────────────────────────────────────────────────

MESSAGE 1: User → "What is Docker?"
  Model Used: llama3.2
  Routing Method: embedding_classifier
  Confidence: 0.23
  Tools Used: none

MESSAGE 2: User → "How do I use it with Python?"
  Model Used: llama3.2 (CACHED)
  Routing Method: session_cache
  Confidence: 1.0 (cache hit)
  Tools Used: web_search (3 results)

MESSAGE 3: User → "z: code Show me an example"
  Model Used: deepseek-coder
  Routing Method: z_command
  Confidence: 1.0 (user explicit)
  Tools Used: none

────────────────────────────────────────────────────────

STATS:
  Models: 2 (llama3.2, deepseek-coder)
  Cache Hits: 2
  Tools Used: web_search (1x)
  Routing Methods: embedding_classifier (1), z_command (1), cache (10)
```

## Implementation

### Data Flow

```python
class ConversationReplayer:
    def __init__(self, conversation_id: str, sqlite, vector, wiretap):
        self.conv_id = conversation_id
        self.sqlite = sqlite
        self.vector = vector
        self.wiretap = wiretap
    
    def replay(self) -> dict:
        messages = self.sqlite.get_conversation(self.conv_id)
        decisions = self._extract_decisions_from_wiretap()
        tools = self._extract_tools_from_wiretap()
        
        timeline = []
        for msg in messages:
            decision = decisions.get(msg['timestamp'], {})
            msg_tools = tools.get(msg['id'], [])
            timeline.append({
                "message": msg,
                "routing": decision,
                "tools": msg_tools
            })
        
        return {
            "conversation_id": self.conv_id,
            "timeline": timeline,
            "stats": self._compute_stats(timeline)
        }
    
    def render_text(self) -> str:
        # Format nicely for user
        pass
```

### Storage

No new tables needed. All data exists:
- `messages` — message content, model, tokens
- `wire.jsonl` — routing decisions, tools

Just query and correlate.

## API Endpoints

```
GET /api/v1/conversation/{conv_id}/replay

Response:
{
  "conversation_id": "conv-abc",
  "timeline": [
    {
      "message": {...},
      "routing": {
        "method": "embedding_classifier",
        "confidence": 0.92,
        "model": "llama3.2"
      },
      "tools": ["web_search"]
    },
    ...
  ],
  "stats": {...},
  "text": "CONVERSATION REPLAY: ..."
}
```

## CLI Usage

```bash
beigebox replay conv-abc123
# Renders full replay to stdout
```

## Configuration

```yaml
conversation_replay:
  enabled: false
```

## Testing

- [ ] Retrieve conversation from SQLite
- [ ] Extract routing decisions from wiretap
- [ ] Extract tools from wiretap
- [ ] Match messages to decisions
- [ ] Compute stats
- [ ] Render text format
- [ ] Export JSON

## Future Enhancements

- Routing decision accuracy analysis
- Tool effectiveness tracking
- Model comparison (what would other model say?)
- Conversation branching/forking from any message
