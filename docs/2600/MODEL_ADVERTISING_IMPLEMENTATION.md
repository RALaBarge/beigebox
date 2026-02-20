# Model Advertising Feature — Implementation Summary

## What Was Added

You now have a configurable feature to control how BeigeBox advertises itself in the model list shown by Open WebUI.

## Configuration (config.yaml)

```yaml
model_advertising:
  mode: "hidden"           # Options: "advertise" or "hidden" (default)
  prefix: "beigebox:"      # Prefix to prepend when in advertise mode
```

### Two Modes

**Hidden Mode** (default):
- Models appear unchanged: `[llama3.2, gpt-oss-20b, claude]`
- BeigeBox is transparent — users don't know it's there
- Good for production

**Advertise Mode**:
- Models have prefix: `[beigebox:llama3.2, beigebox:gpt-oss-20b, beigebox:claude]`
- Users clearly see they're talking through the middleware
- Good for debugging and transparency

## Example Configurations

### Transparent (default)
```yaml
model_advertising:
  mode: "hidden"
```

### Clearly Advertised
```yaml
model_advertising:
  mode: "advertise"
  prefix: "beigebox:"
```

### Emoji Prefix (fancy!)
```yaml
model_advertising:
  mode: "advertise"
  prefix: "🔗 "
```

### Bracket Style
```yaml
model_advertising:
  mode: "advertise"
  prefix: "[middleware] "
```

## Files Modified

1. **beigebox/proxy.py**
   - Updated `list_models()` method
   - Added new `_transform_model_names()` helper method
   
2. **config.yaml**
   - Added `model_advertising` section with documentation

3. **tests/test_model_advertising.py** (new)
   - Comprehensive test suite covering all scenarios
   - Tests both modes, custom prefixes, edge cases, graceful degradation

## How It Works

When Open WebUI requests the model list (`GET /v1/models`):

```
1. BeigeBox receives request
2. Forwards to Ollama backend
3. Ollama returns: {data: [{name: "llama3.2", ...}, ...]}
4. BeigeBox checks config:
   - If hidden: return response unchanged
   - If advertise: prepend prefix to each model name
5. Return to Open WebUI
```

The transformation is clean and safe:
- Gracefully handles malformed responses
- Defaults to hidden mode if config missing
- Only modifies the response structure, never crashes

## Testing

Complete test coverage:

```bash
pytest tests/test_model_advertising.py -v
```

Tests verify:
- ✓ Hidden mode keeps names unchanged
- ✓ Advertise mode adds prefix
- ✓ Custom prefixes work
- ✓ Default behavior (no config)
- ✓ Malformed responses handled gracefully
- ✓ Missing data keys don't break

## Implementation Details

### Code Changes in proxy.py

```python
# Old (line 595-600):
async def list_models(self) -> dict:
    """Forward /v1/models request to backend."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{self.backend_url}/v1/models")
        resp.raise_for_status()
        return resp.json()

# New (line 595-614):
async def list_models(self) -> dict:
    """Forward /v1/models request to backend, optionally rewriting model names."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{self.backend_url}/v1/models")
        resp.raise_for_status()
        data = resp.json()
    
    # Apply model name transformation if configured
    data = self._transform_model_names(data)
    return data

def _transform_model_names(self, data: dict) -> dict:
    """
    Rewrite model names in the response based on config.
    Supports two modes:
      1. advertise: prepend "beigebox:" to all model names
      2. hidden: don't advertise beigebox's presence
    """
    cfg = self.cfg.get("model_advertising", {})
    mode = cfg.get("mode", "hidden")  # "advertise" or "hidden"
    prefix = cfg.get("prefix", "beigebox:")
    
    if mode == "hidden":
        # Don't modify model names — just pass through
        return data
    
    # Mode: advertise — prepend prefix to all models
    if mode == "advertise" and "data" in data:
        try:
            for model in data.get("data", []):
                if "name" in model:
                    model["name"] = f"{prefix}{model['name']}"
                if "model" in model:
                    model["model"] = f"{prefix}{model['model']}"
        except (TypeError, KeyError):
            # If structure doesn't match, return unchanged
            logger.warning("Could not rewrite model names — unexpected response structure")
    
    return data
```

## Usage

1. **Update your config.yaml** with the new `model_advertising` section (already done if you used our config)

2. **Restart BeigeBox**:
   ```bash
   beigebox dial
   ```

3. **Open WebUI will show transformed model names** in the dropdown

4. **Toggle between modes** anytime by editing config and restarting

## Questions?

Refer to `MODEL_ADVERTISING.md` for detailed documentation, examples, and use cases.

---

**Status**: ✓ Ready for use  
**Breaking Changes**: None (defaults to transparent behavior)  
**Testing**: ✓ Full coverage  
**Documentation**: ✓ Complete
