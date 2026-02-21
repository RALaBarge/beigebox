# File Transfer Support for Chat & Harness Interfaces

**Date**: February 21, 2026  
**Status**: Design Proposal  
**Scope**: v1.0+ enhancement

---

## Overview

Currently, both the **chat** (`/v1/chat/completions`) and **harness** (`/api/v1/harness/orchestrate`) endpoints accept text-only JSON payloads. This document outlines two architectural approaches for adding file transfer capabilities while maintaining transparency and observability.

---

## Use Cases

1. **Chat with file context** — User uploads a document, code file, or image for the model to analyze
2. **Harness with distributed artifacts** — Multiple models analyze the same file in parallel
3. **Operator agent with file processing** — The ReAct agent receives files as tool inputs
4. **Pipeline composition** — Files flow through routing decisions with versioning/tracking

---

## Design Options

### Option A: Base64 Encoding (Chat)

**Pros:**
- Single JSON payload, no multipart complexity
- Compatible with all HTTP clients (curl, web, mobile)
- Fits naturally into existing chat message structure
- Files are observable in wiretap logs (if truncated)
- Simple to implement, zero new dependencies

**Cons:**
- ~33% size overhead (base64 encoding)
- Poor for large files (>10MB problematic)
- Entire payload in memory at once

**Implementation:**

```json
// Chat request with attached file (base64)
{
  "messages": [
    {
      "role": "user",
      "content": "Analyze this CSV file:",
      "attachments": [
        {
          "filename": "sales_data.csv",
          "media_type": "text/csv",
          "data": "base64_encoded_content_here...",
          "size_bytes": 15234
        }
      ]
    }
  ],
  "model": "llama3.2:3b",
  "stream": false
}
```

**Endpoint Changes:**

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    
    # Decode attachments
    for msg in body.get("messages", []):
        if "attachments" in msg:
            for att in msg["attachments"]:
                att["data"] = base64.b64decode(att["data"])  # binary
                # File now accessible as bytes
    
    stream = body.get("stream", False)
    # ... rest of logic
```

**Wire Log Format:**
```jsonl
{"timestamp":"2026-02-21T...","direction":"in","role":"user","message":{"content":"Analyze this CSV file:","attachments":[{"filename":"sales_data.csv","media_type":"text/csv","size_bytes":15234,"data":"<truncated base64, first 500 chars>"}]}}
```

---

### Option B: Multipart Form-Data (Harness)

**Pros:**
- Standard HTTP mechanism for file uploads
- Efficient streaming for large files
- Files don't load entirely into memory
- Better for orchestrator (handles parallel dispatch)
- Familiar to web developers

**Cons:**
- Requires form-data parsing (FastAPI handles it)
- More complex client-side code
- Not directly JSON-serializable (but metadata is)

**Implementation:**

```python
from fastapi import UploadFile, Form

@app.post("/api/v1/harness/orchestrate")
async def api_harness_orchestrate(
    request: Request,
    query: str = Form(...),
    targets: str = Form(default="[]"),  # JSON array as string
    model: str = Form(default=None),
    max_rounds: int = Form(default=8),
    files: list[UploadFile] = None,
):
    targets = json.loads(targets)
    
    # Read files into memory or stream to temp storage
    file_contents = {}
    for file in files or []:
        content = await file.read()
        file_contents[file.filename] = {
            "media_type": file.content_type,
            "size_bytes": len(content),
            "data": content,
        }
    
    orch = HarnessOrchestrator(
        available_targets=targets,
        model=model,
        max_rounds=max_rounds,
        attachments=file_contents,  # Pass to orchestrator
    )
    
    async def _event_stream():
        async for event in orch.run(query, attachments=file_contents):
            yield f"data: {json.dumps(event)}\n\n"
```

**Client Example (curl):**
```bash
curl -X POST http://localhost:8000/api/v1/harness/orchestrate \
  -F "query=Analyze these files" \
  -F "targets=['operator','model:llama3.2:3b']" \
  -F "files=@report.pdf" \
  -F "files=@notes.txt" \
  -N  # streaming
```

**Wire Log Format:**
```jsonl
{"timestamp":"2026-02-21T...","direction":"in","role":"harness_orchestrate","request":{"query":"Analyze these files","targets":["operator","model:llama3.2:3b"],"attachments":[{"filename":"report.pdf","media_type":"application/pdf","size_bytes":256000},{"filename":"notes.txt","media_type":"text/plain","size_bytes":3400}]}}
```

---

## Storage & Observability

### Conversation Storage

Both approaches require extending the `Message` schema in `storage/models.py`:

```python
class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"))
    role = Column(String)
    content = Column(String)
    # NEW: File attachments
    attachments = Column(JSON, nullable=True)  # List of {filename, media_type, size_bytes}
    # NOT stored in DB: actual file content (too large, stored separately)
    
    # NEW optional: file storage references
    attachment_file_ids = Column(JSON, nullable=True)  # UUIDs of stored files
```

### File Storage Strategy

**Recommended**: Store files in `./data/attachments/{conversation_id}/{message_id}/{filename}` with references in the DB.

```
./data/
  attachments/
    {conv_id}/
      {msg_id}/
        report.pdf
        notes.txt
      metadata.json  # {"report.pdf": {"size": 256000, "hash": "sha256:..."}}
```

### Wire Log Enhancement

Extend wiretap to include attachment metadata (not full content):

```jsonl
{"timestamp":"2026-02-21T...","direction":"in","endpoint":"/v1/chat/completions","message":{"role":"user","content":"...","attachments":[{"filename":"data.csv","media_type":"text/csv","size_bytes":15234,"hash":"sha256:abc123..."}]}}
```

---

## Integration Points

### Chat Pipeline

1. **Input validation** — Validate file types, sizes (config-driven limits)
2. **Routing decision** — Files may influence classifier (complexity increased)
3. **Tool invocation** — Operator agent receives file paths or content
4. **Storage** — Save message + attachment references to SQLite + disk
5. **Wire log** — Log attachment metadata (name, size, hash)

### Harness Orchestrator

1. **Parallel dispatch** — All workers receive same files
2. **Task planning** — Master LLM sees file list in context
3. **Worker execution** — Workers can access attachment paths
4. **Result collection** — Workers may return derived files
5. **Event stream** — Include attachment references in plan/dispatch events

---

## Configuration

Add to `config.yaml`:

```yaml
attachments:
  enabled: false
  max_file_size_mb: 100
  max_files_per_request: 10
  allowed_types:
    - "text/*"
    - "application/pdf"
    - "application/json"
    - "image/*"
  storage_path: "./data/attachments"
  cleanup_after_days: 30  # Auto-delete old files
  include_in_wiretap: true  # Log attachment metadata
  truncate_wire_at_bytes: 500  # Max base64 in wiretap
```

---

## Recommended Implementation Order

### Phase 1 (v1.1): Chat + Base64
- Modify `chat_completions()` endpoint
- Update `Message` schema to support attachments
- Add storage to `SQLiteStore`
- Update wiretap logging

### Phase 2 (v1.2): Harness + Multipart
- Modify `api_harness_orchestrate()` endpoint
- Extend `HarnessOrchestrator` to pass files to workers
- Add file metadata to SSE events

### Phase 3 (v1.3): Operator Agent Integration
- Operator agent receives file paths as tool inputs
- Add `read_file` and `list_files` tools
- File-aware web scraper (process downloaded PDFs, etc.)

---

## Security Considerations

1. **File type validation** — Whitelist MIME types, reject executables
2. **Size limits** — Enforce per-file and per-request caps
3. **Storage isolation** — Store outside web root, restrict access
4. **Cleanup** — Auto-delete after TTL to prevent disk exhaustion
5. **Virus scanning** (optional) — Integrate ClamAV for uploaded files
6. **Path traversal** — Sanitize filenames, store with UUIDs

---

## Testing Strategy

```python
# test_file_transfers.py
def test_chat_with_base64_attachment():
    csv_data = b"name,age\nAlice,30\nBob,25"
    encoded = base64.b64encode(csv_data).decode()
    
    response = client.post("/v1/chat/completions", json={
        "messages": [{
            "role": "user",
            "content": "Analyze this CSV:",
            "attachments": [{
                "filename": "data.csv",
                "media_type": "text/csv",
                "data": encoded,
            }]
        }],
        "model": "llama3.2:3b"
    })
    
    assert response.status_code == 200
    assert "Alice" in response.json()["choices"][0]["message"]["content"]

def test_harness_multipart_upload():
    with open("test.pdf", "rb") as f:
        files = {"files": ("test.pdf", f, "application/pdf")}
        response = client.post(
            "/api/v1/harness/orchestrate",
            data={"query": "Summarize this PDF", "targets": ["operator"]},
            files=files,
        )
    
    assert response.status_code == 200
    # Check SSE events include file metadata
```

---

## FAQ

**Q: Won't base64 encoding blow up request sizes?**  
A: Yes, ~33% overhead. For typical use (small docs/CSVs), acceptable. For heavy image/video processing, use multipart instead.

**Q: Should workers in harness get the actual files or just paths?**  
A: Start with paths (files pre-stored to disk by master). Later add streaming if needed.

**Q: How do I prevent disk exhaustion?**  
A: TTL-based cleanup + size limits in config. Monitor `/data/attachments` growth via dashboard.

**Q: Can I serve stored files back to the client?**  
A: Yes, add `GET /api/v1/attachment/{message_id}/{filename}` endpoint with auth.

---

## References

- SQLite binary storage: https://www.sqlite.org/blob.html
- FastAPI file uploads: https://fastapi.tiangolo.com/tutorial/request-files/
- Base64 RFC: https://tools.ietf.org/html/rfc4648
- Multipart MIME: https://tools.ietf.org/html/rfc2388
