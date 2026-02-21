# Multi-Modal Inference Support for BeigeBox

**Date**: February 21, 2026  
**Version**: v1.1+ Enhancement  
**Status**: Architecture & Feasibility Study

---

## Executive Summary

BeigeBox can support **images, videos, audio, and arbitrary media** with minimal changes to the existing architecture because:

1. **Catch-all passthrough exists** — Any unknown endpoint is forwarded transparently to backends
2. **Wiretap already logs all traffic** — Media endpoints are visible in the wire log
3. **Backend abstraction is endpoint-agnostic** — The proxy doesn't care what data flows through
4. **Audio endpoints already implemented** — `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/audio/translations` forward transparently

**What's needed**: Explicit routing and observability for the most common modalities (vision, video) rather than relying solely on catch-all forwarding.

---

## Current State

### Already Forwarded (Catch-all or Explicit)

| Endpoint | Type | Status | Notes |
|----------|------|--------|-------|
| `/v1/chat/completions` | Text LLM | ✅ Explicit + Routing | Core endpoint with full decision pipeline |
| `/v1/audio/transcriptions` | STT | ✅ Explicit | Forwards transparently, logged as `audio/transcriptions` |
| `/v1/audio/speech` | TTS | ✅ Explicit | Forwards transparently, logged as `audio/speech` |
| `/v1/audio/translations` | Translation | ✅ Explicit | Forwards transparently |
| `/v1/files/*` | File ops | ✅ Explicit catch-all | For fine-tuning file uploads |
| `/v1/embeddings` | Embeddings | ✅ Explicit | Ollama native endpoint, forwards transparently |
| `/api/embed` | Ollama embed | ✅ Explicit | Ollama native, forwards transparently |
| `/api/chat` | Ollama native chat | ✅ Explicit | Ollama native, forwards transparently |
| `/api/generate` | Ollama native generate | ✅ Explicit | Ollama native, forwards transparently |
| `/{path:path}` | **Catch-all** | ✅ Explicit | Any unmatched endpoint forwarded transparently |

### Missing Explicit Routes

| Endpoint | Type | Capability |
|----------|------|-----------|
| `/v1/vision` (hypothetical) | Vision LLM | Not in OpenAI spec but used by some models |
| `/v1/images/generations` | DALL-E | Standard OpenAI endpoint |
| `/v1/images/edits` | Image editing | Standard OpenAI endpoint |
| `/v1/images/variations` | Image variations | Standard OpenAI endpoint |
| `/v1/video/*` | Video generation/analysis | Not standard but emerging |
| Custom vision endpoints | Model-specific | Varies by provider (e.g., Claude's vision) |

---

## Architecture for Multi-Modal Support

### Option 1: Rely on Catch-All (Current)

**Pros:**
- Zero code changes
- Works immediately for any new endpoint
- Transparent to backends
- Already logging to wiretap

**Cons:**
- Can't route vision requests intelligently (no special handling)
- Can't track modality-specific costs
- No observability on which models handle images
- Vision requests go to catch-all, not through routing pipeline

**When it works**: If you only need visibility and transparency. Good for low-volume image requests.

---

### Option 2: Explicit Vision Endpoint + Router Enhancement (Recommended)

**Architecture**:

```python
# Add vision-aware routing to the proxy
# Images influence routing decision (complexity increases)
```

**Key changes**:

1. **Add explicit `/v1/vision` endpoint** (or support vision in `/v1/chat/completions` with image content)
2. **Extend Message schema** to store image metadata (size, resolution, format)
3. **Update embedding classifier** to account for image complexity
4. **Track image costs** separately from text (important for API backends)
5. **Log image metadata** to wiretap (not the actual pixels)

#### Endpoint Implementation

```python
@app.post("/v1/vision")
async def vision_request(request: Request):
    """
    Vision inference endpoint.
    
    Body format (compatible with Claude/GPT-4V):
    {
      "model": "llava:7b",
      "messages": [
        {
          "role": "user",
          "content": [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "https://..."}}
            // OR
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...}}
          ]
        }
      ]
    }
    """
    body = await request.json()
    
    # Extract image metadata for routing
    images = _extract_images(body)
    
    # Route based on image count + complexity
    # (vision requests may need different models)
    route_decision = await _route_vision_request(body, images)
    
    # Log to wiretap (metadata only, not pixels)
    _log_vision_request(images, route_decision)
    
    # Forward to selected backend
    response = await proxy.forward_vision(body, route_decision)
    
    # Store conversation with image refs
    _store_vision_conversation(body, response, images)
    
    return response
```

#### Supporting `vision` Content in Chat

Alternatively (OpenAI-compatible approach):

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    
    # Check if messages contain images
    has_images = any(
        isinstance(msg.get("content"), list) and
        any(item.get("type") == "image_url" for item in msg["content"])
        for msg in body.get("messages", [])
    )
    
    if has_images:
        # Vision routing tier
        # Models that support vision: llava, gpt-4-vision, claude-3-vision
        route_decision = await _route_vision_chat(body)
    else:
        # Text routing tier (existing pipeline)
        route_decision = await _route_text_chat(body)
    
    # ... rest of logic
```

---

### Option 3: Modality Router Layer (Advanced)

Add a pre-router that classifies requests by modality and dispatches to appropriate pipeline:

```
Request
  ↓
Modality Classifier
  ├─ Text only         → Text routing (existing)
  ├─ Text + Images     → Vision routing (new)
  ├─ Text + Video      → Video routing (new)
  ├─ Audio only        → Audio routing (new)
  └─ Mixed multimodal  → Multimodal orchestrator (advanced)
  
Each pipeline has:
  - Backend selection (which models support this modality)
  - Cost tracking (per-modality pricing)
  - Observability (role in wiretap: "vision", "video", "audio")
```

**Implementation**:

```python
class ModalityClassifier:
    """Inspect request and determine primary modality."""
    
    @staticmethod
    def classify(body: dict) -> str:
        """Return: 'text', 'vision', 'video', 'audio', 'multimodal'."""
        messages = body.get("messages", [])
        
        has_text = False
        has_images = False
        has_video = False
        has_audio = False
        
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for item in content:
                    item_type = item.get("type", "")
                    if item_type == "text":
                        has_text = True
                    elif item_type in ["image_url", "image"]:
                        has_images = True
                    elif item_type == "video":
                        has_video = True
                    elif item_type == "audio":
                        has_audio = True
            elif isinstance(content, str):
                has_text = True
        
        # Priority: multimodal > video > vision > audio > text
        if (has_images or has_video) and (has_audio or has_text):
            return "multimodal"
        elif has_video:
            return "video"
        elif has_images:
            return "vision"
        elif has_audio:
            return "audio"
        else:
            return "text"
```

---

## Detailed Proposal: Vision as First Multi-Modal Support

### Why Vision First?

1. **Most requested** — Vision APIs (Claude, GPT-4V, LLaVA) are most common
2. **Simple integration** — Image URLs or base64 in existing message format
3. **Routing value** — Not all models support vision (routing matters)
4. **Cost tracking** — Vision requests expensive (AWS Bedrock, OpenRouter pricing)
5. **Observability** — Image complexity influences routing

### Storage Schema Extension

```python
# storage/models.py
class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"))
    role = Column(String)
    content = Column(String)
    
    # NEW: Image metadata (not actual pixels)
    images = Column(JSON, nullable=True)  # [{"url": "...", "type": "image/jpeg", "size_bytes": 15234, "dimensions": [1920, 1080]}]
    
    # NEW: Modality hint
    primary_modality = Column(String, default="text")  # text|vision|video|audio|multimodal
```

### Routing Decision Enhancement

Extend `Decision` and `DecisionAgent` to account for images:

```python
class Decision:
    model: str
    reason: str
    modality: str  # "text" | "vision" | "video" | etc.
    tier: int  # 0=cache, 1=z-cmd, 2=agentic, 3=embedding, 4=llm
    
async def decide_vision(
    goal: str,
    image_count: int,
    image_types: list[str],  # ["image/jpeg", "image/png", ...]
    total_pixels: int,
    conversation_id: str,
) -> Decision:
    """
    Vision-specific routing.
    
    Factors:
    - Which backends support vision?
    - How many images? (batch processing?)
    - Image complexity? (high-res photos vs. diagrams?)
    - Is there text too? (multimodal cost?)
    """
```

### Configuration

```yaml
# config.yaml
vision:
  enabled: false  # Feature flag
  
  # Which models handle vision?
  capable_models:
    - "llava:7b"
    - "gpt-4-vision"
    - "claude-3-vision"
  
  # Image constraints
  max_file_size_mb: 50
  max_images_per_request: 10
  allowed_formats:
    - "image/jpeg"
    - "image/png"
    - "image/webp"
    - "image/gif"
  
  # Routing
  complexity_threshold: 0.6  # If image "complexity" > this, use large model
  
  # Cost tracking
  track_costs: true
  tokens_per_image: 850  # Approximate for encoding

# runtime_config.yaml
vision_enabled: false
vision_model: "llava:7b"
vision_force_route: ""  # Force all vision requests to specific model
```

### Wiretap Integration

Log vision requests with image metadata (never actual pixels):

```jsonl
{"ts":"2026-02-21T...","dir":"inbound","role":"vision","model":"llava:7b","conv":"abc123...","images":[{"type":"image/jpeg","size_bytes":102400,"resolution":[1920,1080]}],"content":"What's in this image?"}
{"ts":"2026-02-21T...","dir":"outbound","role":"vision","model":"llava:7b","conv":"abc123...","len":245,"content":"The image shows a cat sitting on a keyboard..."}
```

### Cost Tracking Enhancement

```python
class CostTracker:
    def log_vision_request(
        self,
        model: str,
        image_count: int,
        image_tokens: int,
        text_tokens: int,
        cost_usd: float,
        conversation_id: str,
    ):
        """Track vision request with image token costs."""
```

### Example Flow

```
POST /v1/chat/completions
{
  "model": "auto",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What's in this image?"},
        {
          "type": "image_url",
          "image_url": {"url": "https://example.com/photo.jpg"}
        }
      ]
    }
  ]
}

→ ModalityClassifier.classify() → "vision"
→ Route to vision decision pipeline
→ Query: Which models support vision + user's model preference
→ Decision: "llava:7b" (local, supports vision)
→ Log to wiretap: role="vision", images=[...metadata...]
→ Store to SQLite: primary_modality="vision", images=[...metadata...]
→ Forward to Ollama: /v1/chat/completions with image URL
→ Stream response back
```

---

## Video Support

### Considerations

**Challenges:**
- Video is large (GB files common)
- Need to handle external URLs vs. upload
- Video processing slow (tokenization/embedding)
- Most models don't support video yet
- Multi-frame extraction complexity

**Approach:**
1. Accept video URLs (don't store actual files)
2. Extract key frames server-side (optional, feature-flagged)
3. Process as sequence of images
4. Fallback to transcription (if audio track available)
5. Or just forward transparently (let backend handle)

```python
@app.post("/v1/video/analyze")
async def video_analyze(request: Request):
    """
    Video analysis endpoint (non-standard, custom).
    
    Body:
    {
      "model": "gpt-4-vision",
      "video_url": "https://...",
      "max_frames": 10,  # Extract N key frames
      "question": "What happens in this video?"
    }
    """
```

---

## Audio Support (Enhancement)

### Current State
- `/v1/audio/transcriptions` — forwards transparently
- `/v1/audio/speech` — forwards transparently
- `/v1/audio/translations` — forwards transparently

### Enhancement Ideas

1. **Add voice chat** — Stream audio messages in chat completions
   ```python
   {"role": "user", "content": [{"type": "audio", "audio_url": "..."}]}
   ```

2. **Track STT/TTS costs** — OpenRouter charges for audio
   ```python
   def log_audio_request(model: str, duration_seconds: float, cost_usd: float)
   ```

3. **Audio routing** — Not all models support audio input/output
   ```python
   async def route_audio(duration_seconds, language, quality) -> model
   ```

---

## Implementation Roadmap

### Phase 1 (v1.1): Vision Baseline
- [x] Add `/v1/chat/completions` vision support (images in content)
- [x] Extend Message schema with image metadata
- [x] Vision routing decision
- [x] Image metadata to wiretap
- [x] Cost tracking for vision (approximate tokens)
- [x] Config flags for vision models
- Timeline: 2-3 weeks

### Phase 2 (v1.2): Video Exploration
- [ ] `/v1/video/analyze` endpoint
- [ ] Key frame extraction (optional)
- [ ] Video routing decision
- [ ] Cost tracking for video processing
- Timeline: 3-4 weeks

### Phase 3 (v1.3): Audio Enhancement
- [ ] `/v1/chat/completions` with audio content
- [ ] Audio routing (which models support?)
- [ ] STT/TTS cost tracking
- [ ] Audio streaming in web UI
- Timeline: 2-3 weeks

### Phase 4 (v1.4+): Multimodal Advanced
- [ ] Orchestrator for mixed-modality tasks
- [ ] Image → Video → Audio pipelines
- [ ] Adaptive routing based on content mix
- [ ] Batch image processing
- Timeline: Open-ended

---

## Testing Strategy

```python
# tests/test_multimodal.py

async def test_vision_request_routing():
    """Vision requests route to capable models only."""
    body = {
        "model": "auto",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "https://..."}}
            ]
        }]
    }
    response = await chat_completions(body)
    # Verify model selected supports vision
    # Verify wiretap entry has images metadata

async def test_image_metadata_logged():
    """Image metadata stored without actual pixels."""
    # Send vision request
    # Check SQLite message.images field populated
    # Check wiretap contains image size/type, not base64 content

def test_vision_config_validation():
    """Config parsing for vision settings."""
    cfg = get_config()
    assert "vision" in cfg
    assert "capable_models" in cfg["vision"]

async def test_large_image_rejected():
    """Images > max_file_size_mb rejected."""
    large_image = "data:image/jpeg;base64," + ("A" * 100_000_000)
    # Should return 413 Payload Too Large
```

---

## FAQs

**Q: Will videos work immediately?**  
A: If your backend (Ollama, OpenRouter) supports them, yes — catch-all forwards it. But without explicit routing/logging.

**Q: Do I need to change OpenAI SDK code?**  
A: No. OpenAI SDK already supports vision (images in `content`). Just point at BeigeBox and it works.

**Q: What about audio files from users?**  
A: Files use the catch-all and are logged. For structured handling, integrate file transfer proposal (see `file-transfer-design.md`).

**Q: Can I route images to specific models?**  
A: Yes. Routing decision pipeline can inspect modality and select appropriate model.

**Q: How are costs tracked for vision?**  
A: Approximate image token count (e.g., 850 tokens per image) multiplied by backend pricing. Refine with actual token counts from backend response if available.

---

## References

- OpenAI vision: https://platform.openai.com/docs/guides/vision
- Claude vision: https://docs.anthropic.com/claude/reference/vision
- LLaVA: https://llava-vl.github.io/
- Current proxy implementation: `beigebox/proxy.py`
- Backend abstraction: `beigebox/backends/base.py`
- Catch-all endpoint: `beigebox/main.py` (last route)
