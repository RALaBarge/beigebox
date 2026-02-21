# Cost Tracking Design (v0.6.0)

## Overview

**Cost Tracking** logs costs for OpenRouter API calls (local models are free/$0).

## Problem Statement

When using OpenRouter:
- How much am I spending?
- Which models are expensive?
- Cost per conversation?
- Trends over time?

Cost Tracking answers these.

## Design

### Data Capture

OpenRouter returns cost with each response:
```json
{
  "choices": [...],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 50
  },
  "cost_usd": 0.0015
}
```

BeigeBox stores this in SQLite:
```sql
INSERT INTO messages (id, cost_usd, ...) VALUES (..., 0.0015, ...)
```

Local models: cost_usd = NULL (implicitly $0).

### Querying

```sql
-- Total cost last 7 days
SELECT SUM(cost_usd) FROM messages WHERE cost_usd IS NOT NULL AND timestamp > date('now', '-7 days')

-- Cost by model
SELECT model, SUM(cost_usd) FROM messages WHERE cost_usd IS NOT NULL GROUP BY model

-- Cost by day
SELECT DATE(timestamp), SUM(cost_usd) FROM messages WHERE cost_usd IS NOT NULL GROUP BY DATE(timestamp)

-- Cost by conversation
SELECT conversation_id, SUM(cost_usd) FROM messages WHERE cost_usd IS NOT NULL GROUP BY conversation_id
```

## Implementation

### Code Structure

```python
class CostTracker:
    def log_usage(self, message_id: str, cost_usd: float):
        """Store cost for message (from OpenRouter response)."""
        # Already stored in messages table via proxy
        # This method just aggregates/reports
        pass
    
    def get_stats(self, days: int = 30) -> dict:
        """Get cost stats for period."""
        with self.sqlite.connect() as conn:
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM messages "
                "WHERE cost_usd IS NOT NULL AND timestamp > date('now', ?)",
                (f'-{days} days',)
            ).fetchone()[0]
            
            by_model = conn.execute(
                "SELECT model, COUNT(*), COALESCE(SUM(cost_usd), 0) "
                "FROM messages WHERE cost_usd IS NOT NULL GROUP BY model"
            ).fetchall()
            
            by_day = conn.execute(
                "SELECT DATE(timestamp), COALESCE(SUM(cost_usd), 0) "
                "FROM messages WHERE cost_usd IS NOT NULL "
                "AND timestamp > date('now', ?) "
                "GROUP BY DATE(timestamp)",
                (f'-{days} days',)
            ).fetchall()
        
        return {
            "total": total,
            "by_model": {row[0]: row[2] for row in by_model},
            "by_day": {row[0]: row[1] for row in by_day},
            "average_daily": total / days
        }
```

### Integration

In `proxy.py`, when storing message:
```python
def _log_response(self, conversation_id: str, content: str, model: str, cost_usd: float = None):
    message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        model=model,
        token_count=tokens,
        cost_usd=cost_usd  # From OpenRouter response
    )
    self.sqlite.store_message(message)
```

### API Endpoint

```
GET /api/v1/costs?days=30

Response:
{
  "total": 1.23,
  "average_daily": 0.041,
  "by_model": {
    "gpt-4-turbo": 0.89,
    "claude-3": 0.34
  },
  "by_day": {
    "2026-02-20": 0.05,
    "2026-02-19": 0.04,
    ...
  }
}
```

### CLI

```bash
beigebox flash  # Shows last 7 days costs
```

## Configuration

```yaml
cost_tracking:
  enabled: false
  track_openrouter: true  # Log API costs
  track_local: false      # Don't track local (always $0)
```

## Storage

New column in `messages` table:
```sql
cost_usd REAL DEFAULT NULL
```

- Local models: NULL
- OpenRouter: numeric value from API

No migration needed. Existing rows get NULL.

## Testing

- [ ] Log cost from OpenRouter
- [ ] Query total cost
- [ ] Query by model
- [ ] Query by day
- [ ] Handle NULL values (local models)
- [ ] API endpoint returns correct data
- [ ] CLI shows costs

## Future Enhancements

- Budget alerts (warn if daily cost > threshold)
- Model cost comparison (which is cheapest for task?)
- ROI calculation (cost vs quality)
- Forecast (project monthly spend)
- Multi-account tracking (if using multiple API keys)
