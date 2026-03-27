# ✅ COMPLETE — Routing bugs diagnosed and fixed. Explicit model selection bypass (step 1.8 in _hybrid_route) prevents OpenRouter models from being overridden by embedding classifier. Backend partition logic (_can_attempt_model) verified correct.

# "No backends available" — Root Cause Diagnosis

**Branch:** `fix/ui-tools-bugs`
**Date:** 2026-03-16
**Symptom:** Chat returns `{"error": "All backends failed: No backends available"}` injected into the SSE stream (HTTP 200, error in body).

---

## Root Cause 1 — Empty model name reaches the router

### How it happens

`index.html` line ~3326:
```javascript
if (model) body.model = model;
```

When a pane's target model (`pane.target`) is empty or unset, `model` is falsy and `body.model` is **never set**. The proxy receives a request body with no `model` field.

In `proxy.py`, `_inject_generation_params()` (line ~588) does **not** inject a default model into the body. So `body.get("model", "")` evaluates to `""`.

In `backends/router.py`, `_partition_backends(model="")`:
- OllamaBackend: `_available_models` is populated after first model list; `"" not in _available_models` → returns False → ollama excluded
- OpenRouterBackend: `supports_model("")` → empty string is not a `/`-containing slug → returns True, but backend is unhealthy (no API key configured) → fails
- Result: **"No backends available"**

### Recommended fix

In `proxy.py`, before calling the router, normalize the model field:
```python
if not body.get("model"):
    rt = get_runtime_config()
    body["model"] = rt.get("default_model") or cfg.get("backend", {}).get("default_model", "")
```

Alternatively, do this in the UI: always populate `body.model` from `pane.target || defaultModel`.

---

## Root Cause 2 — HuggingFace model IDs blocked by OllamaBackend

### How it happens

`backends/ollama.py`, `supports_model()`:
```python
if "/" in model:
    return False
```

This was intended to reject OpenRouter-style IDs (`provider/model`), but it also rejects valid Ollama-native HuggingFace model IDs like `hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF`.

If a user sets `pane.target` to an `hf.co/...` model, the proxy will never route it to Ollama even though Ollama supports it.

### Recommended fix

Check `_available_models` first before applying the slash rejection:
```python
def supports_model(self, model: str) -> bool:
    if self._available_models:
        return model in self._available_models
    # fallback heuristic when model list not yet populated
    if "/" in model and not model.startswith("hf.co/"):
        return False
    return True
```

Or more simply: if the model is in `_available_models`, always return True regardless of name format.

---

## What was already fixed on this branch

1. `api_backends_apply` endpoint now calls `get_effective_backends_config()` instead of using the raw resolved list — ensures ollama fallback injection runs on live router rebuilds (same as startup).
2. `runtime_config.yaml` in the docker volume was corrected to include an explicit `ollama-local` backend entry (removed orphaned BitNet openai_compat backend).

These fixes resolved the initial failure mode (missing ollama entry) but the empty-model-name case persists.

---

## Files to change for full fix

| File | Change |
|---|---|
| `beigebox/proxy.py` | Inject `default_model` into body when `body.get("model")` is falsy |
| `beigebox/backends/ollama.py` | `supports_model`: check `_available_models` before slash heuristic |
| `beigebox/web/index.html` | (optional defense-in-depth) always set `body.model` to fallback default |

---

## Verification steps

1. Open a fresh pane with no model selected → send a message → should route to default model, not error
2. Add `hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF` as pane target → should route to Ollama
3. `curl -s localhost:1337/api/backends` → both ollama-local healthy, openrouter degraded (expected without API key)
