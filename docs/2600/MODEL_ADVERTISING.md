# Model Advertising Feature

## Overview

By default, BeigeBox is a **transparent proxy** — Open WebUI sees the same model names as if it were talking directly to Ollama. This means users don't know the middleware is there.

The **model advertising** feature lets you optionally advertise BeigeBox's presence by prepending a prefix (like `beigebox:`) to model names. This makes it clear to users that they're talking through the middleware.

## Configuration

Add this to your `config.yaml`:

```yaml
model_advertising:
  mode: "hidden"           # "advertise" or "hidden"
  prefix: "beigebox:"      # Prefix to add in advertise mode
```

## Modes

### Hidden Mode (Default)

```yaml
model_advertising:
  mode: "hidden"
```

**Model dropdown in Open WebUI shows:**
```
- llama3.2
- gpt-oss-20b
- claude
```

Users don't see that BeigeBox is in the middle. The proxy is completely transparent.

### Advertise Mode

```yaml
model_advertising:
  mode: "advertise"
  prefix: "beigebox:"
```

**Model dropdown in Open WebUI shows:**
```
- beigebox:llama3.2
- beigebox:gpt-oss-20b
- beigebox:claude
```

Users can see they're talking through BeigeBox. Useful for:
- Debugging routing decisions
- Making it clear that conversations are being logged/stored
- Distinguishing between direct Ollama and through-BeigeBox connections
- Building confidence that features (routing, tools, RAG) are active

## Custom Prefixes

You can use any prefix you like:

```yaml
# Emoji prefix
prefix: "🔗 "
# Shows: 🔗 llama3.2, 🔗 gpt-oss-20b, etc.

# Bracketed prefix
prefix: "[middleware] "
# Shows: [middleware] llama3.2, etc.

# With color codes (if your terminal supports them)
prefix: "[BEIGEBOX] "
```

## How It Works

When Open WebUI (or any OpenAI-compatible client) calls `GET /v1/models`:

1. BeigeBox forwards the request to the backend (Ollama)
2. Ollama returns the list of available models
3. BeigeBox checks `model_advertising.mode`
4. If `advertise`: prepend the prefix to each model's name field
5. If `hidden`: return unchanged
6. Send the (possibly modified) list back to the client

## Edge Cases Handled

- **Missing config**: Defaults to hidden mode (transparent)
- **Malformed response**: Returns unchanged if structure doesn't match expected format
- **Both name and model fields**: Updates both fields if present
- **Empty model list**: Passes through unchanged

## Implementation Details

The transformation happens in `proxy.py`:

```python
def _transform_model_names(self, data: dict) -> dict:
    """Rewrite model names based on config."""
    cfg = self.cfg.get("model_advertising", {})
    mode = cfg.get("mode", "hidden")
    prefix = cfg.get("prefix", "beigebox:")
    
    if mode == "hidden":
        return data  # Pass through unchanged
    
    # Advertise mode: prepend prefix
    if mode == "advertise" and "data" in data:
        for model in data.get("data", []):
            if "name" in model:
                model["name"] = f"{prefix}{model['name']}"
            if "model" in model:
                model["model"] = f"{prefix}{model['model']}"
    
    return data
```

## Testing

Run the test suite:

```bash
pytest tests/test_model_advertising.py -v
```

Tests cover:
- Hidden mode (models unchanged)
- Advertise mode (prefix added)
- Custom prefixes
- Default behavior (no config)
- Malformed responses (graceful degradation)
- Missing data key (graceful degradation)

## Use Cases

### 1. Transparency & Debugging

Enable advertising mode to make it visible that BeigeBox is routing requests. Useful when you're:
- Testing the routing system
- Debugging tool invocations
- Training users on how the system works

### 2. Production Deployment

Keep hidden mode for seamless operation. Users don't need to know about the middleware — it just works.

### 3. Multi-Backend Setup

If you have both direct Ollama and through-BeigeBox available, advertise mode helps users choose the right one:

```
- llama3.2                 (direct Ollama)
- beigebox:llama3.2        (through middleware, with routing)
```

## Future Enhancements

- Per-model config (e.g., advertise only code models)
- Dynamic prefix based on routing decision
- Response transformation (e.g., add custom "provider" field)
- A/B testing different prefixes

## Summary

| Feature | Hidden Mode | Advertise Mode |
|---------|-------------|----------------|
| Transparency | Complete | User-aware |
| Use Case | Production | Debugging/training |
| Configuration | `mode: hidden` | `mode: advertise` + prefix |
| Default | ✓ | |
