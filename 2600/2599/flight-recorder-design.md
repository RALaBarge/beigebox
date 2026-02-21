# Flight Recorder Design (v0.6.0)

## Overview

**Flight Recorder** captures detailed timeline of each request's lifecycle, showing exactly when each stage runs and how long it takes.

## Problem Statement

When things go slow, hard to know why:
- Was it the embedding classifier?
- Was it the LLM?
- Was it storage?
- Was it network?

Flight Recorder makes it transparent.

## Design Decisions

### 1. What Gets Recorded

Each request gets a timeline with milestones:
- User message received
- Z-command parse
- Session cache lookup
- Pre-request hooks
- Tool decision
- Embedding classifier
- Decision LLM (if needed)
- Request forwarding
- Response received
- Post-response hooks
- Storage
- Response sent

Each milestone includes:
- Timestamp (ISO format)
- Elapsed time since request start
- Stage name
- Details (confidence, tool name, etc.)

### 2. Data Structure

```python
class FlightRecord:
    id: str                    # Unique request ID
    conversation_id: str
    timestamp: datetime        # Request start time
    events: list[dict]         # Timeline events
    summary: dict              # Stats (total latency, breakdown)
```

Event:
```python
{
    "timestamp": "2026-02-20T14:32:01.000Z",
    "elapsed_ms": 0,
    "stage": "User Message Received",
    "details": {
        "model": "llama3.2",
        "tokens": 6
    }
}
```

### 3. Storage Strategy

```
In-memory cache (LRU, max 1000 records)
├─ Fast lookup
├─ Survives server restart? No
└─ Good for debugging

Optional: SQLite for persistence
├─ `flight_records` table
├─ Retention policy (24 hours)
└─ Query old records
```

Config:
```yaml
flight_recorder:
  enabled: false
  retention_hours: 24
  max_records: 1000
  persistent: false  # Keep in memory only
```

### 4. Rendering

Text format:
```
REQUEST TIMELINE: uuid-abc123

[14:32:01.000] ➜ User Message Received
  Model: llama3.2
  Tokens: 6

[14:32:01.010] ➜ Session Cache Lookup
  Hit: true
  Model: llama3.2
  Age: 2m

[14:32:01.090] ➜ Embedding Classifier
  Input: [768-dim vector]
  Simple: 0.15
  Complex: 0.87 ← WINNER
  Confidence: 0.92

[14:32:02.350] ➜ Response Received
  Tokens: 256

[14:32:02.370] → Response Sent

SUMMARY:
  Total: 2370ms
  Backend: 1200ms (51%)
  Routing: 90ms (4%)
  Storage: 10ms (0.4%)
  Other: 1070ms (45%)
```

## Implementation Notes

### Code Structure

```python
class FlightRecorder:
    def __init__(self, conversation_id: str):
        self.id = uuid4().hex
        self.events = []
        self.start_time = time.time()
    
    def log(self, stage: str, **details):
        """Log a stage with elapsed time."""
        self.events.append({
            "timestamp": datetime.now().isoformat(),
            "elapsed_ms": (time.time() - self.start_time) * 1000,
            "stage": stage,
            "details": details
        })
    
    def render_text(self) -> str:
        """Render as readable timeline."""
        # Format events nicely
        pass
    
    def to_json(self) -> dict:
        """Export as JSON."""
        return {"id": self.id, "events": self.events}
```

### Integration Points

In `proxy.py`:
```python
async def forward_chat_completion(self, body: dict):
    recorder = FlightRecorder(conversation_id)
    recorder.log("User Message Received", model=model, tokens=...)
    
    # ... routing and processing ...
    recorder.log("Embedding Classifier", confidence=0.92, winner="complex")
    
    # ... forward request ...
    recorder.log("Response Received", tokens=256)
    
    # Store recorder for retrieval
    self.flight_recorders[recorder.id] = recorder
```

### Cleanup

Old records purged via background task:
```python
async def cleanup_flight_records():
    while True:
        await asyncio.sleep(3600)  # Every hour
        cutoff = time.time() - (24 * 3600)
        stale = [id for id, rec in self.flight_recorders.items()
                 if rec.start_time < cutoff]
        for id in stale:
            del self.flight_recorders[id]
```

## API Endpoints

**Get Flight Record**:
```
GET /api/v1/flight-recorder/{request_id}

Response:
{
  "id": "abc123",
  "conversation_id": "conv-xyz",
  "events": [...],
  "summary": {...},
  "text": "REQUEST TIMELINE: ...\n..."
}
```

## Configuration

```yaml
flight_recorder:
  enabled: false
  retention_hours: 24
  max_records: 1000
```

## Example Usage

User makes a request. BeigeBox captures timeline. User can see:
```
GET /api/v1/flight-recorder/abc123
→ Full timeline + summary
```

Or via TUI: New "Flight Recorder" screen showing last 10 requests.

## Testing

- [ ] Record all stages
- [ ] Calculate elapsed time correctly
- [ ] Render text format
- [ ] Export JSON
- [ ] Cleanup old records
- [ ] Handle failed requests (log partial timeline)
- [ ] Concurrent requests (separate records)

## Future Enhancements

- Flamegraph visualization (show % time per stage)
- Timeline comparison (this request vs historical)
- Anomaly detection (request took 10x longer than usual)
- Trace replay (recreate request execution)
