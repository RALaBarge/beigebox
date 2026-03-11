# Operator Model Configuration Audit

## Problem
The operator model is **defined in multiple places but NOT consistently configurable** from the runtime config/web UI.

---

## Operator Model Definition Points

### 1. **config.yaml** (Static Config)
- `operator.model: "qwen3:4b"` — Primary source, loaded once at startup
- Also has sub-configs: `pre_hook.model`, `post_hook.model`

### 2. **main.py — Config Endpoint** (Line 667)
```python
"model":  cfg.get("operator", {}).get("model", "")
```
- ✅ **Displays** current operator model on config page
- ❌ **NOT in runtime config allowed keys** (line 835)
- ❌ User sees the value but **cannot change it** via web UI

### 3. **Operator.__init__** (agent/operator.py:274-277)
```python
self._model = (
    model_override
    or self.cfg.get("operator", {}).get("model")
    or self.cfg.get("backend", {}).get("default_model", "")
)
```
Priority chain:
1. `model_override` param (passed from caller)
2. `operator.model` from config
3. `backend.default_model` fallback

### 4. **Wire Logging** (main.py:1938-2087)
- Logs use `_op_model = op._model` (extracted from Operator instance)
- ✅ Correct — gets the actual model used after fallback

### 5. **Council Mode** (main.py:2184-2195)
```python
operator_model = (
    body.get("model", "").strip()
    or cfg.get("operator", {}).get("model")
    or cfg.get("backend", {}).get("default_model", "")
)
```
- ✅ Allows `model` override in request body
- ✅ Falls back to config chains

### 6. **Harness Orchestrator** (agents/harness_orchestrator.py:77-81)
```python
self.model = (
    model
    or cfg.get("operator", {}).get("model")
    or cfg.get("backend", {}).get("default_model", "")
)
```
- ✅ Same chain as Operator

### 7. **Web UI Display** (web/index.html:5628)
```html
html += kv('operator model', esc(c.operator?.model || '—'));
```
- ✅ Shows the value from `/api/v1/config`
- ❌ Read-only (no input field to change it)

---

## Issues Summary

| Location | Issue | Severity |
|----------|-------|----------|
| `/api/v1/config` allowed keys | Missing `operator_model` | 🔴 HIGH |
| Web UI Config tab | Displays model but can't edit | 🔴 HIGH |
| `POST /api/v1/config` | Can't accept `operator_model` param | 🔴 HIGH |
| Sub-hook models (`pre_hook.model`, `post_hook.model`) | No separate config keys | 🟡 MEDIUM |
| Documentation | Not clear that operator model is not user-settable via web UI | 🟡 MEDIUM |

---

## Call Chain Examples

### Direct Operator Call (main.py:1920)
```python
op = Operator(
    vector_store=vs,
    blob_store=blob_store,
    model_override=model_override  # ← from request body
)
_op_model = op._model
```

### Council Propose (main.py:2184-2195)
```python
operator_model = (
    body.get("model", "").strip()       # User can override here
    or cfg.get("operator", {}).get("model")
    or cfg.get("backend", {}).get("default_model", "")
)
await _council_propose(query, backend_url, operator_model, allowed_models=allowed_models)
```

### Harness (endpoint params would need to be added)
Currently no model override support in `/api/v1/harness` endpoints.

---

## Recommended Fixes

### 1. Add operator model to runtime config (REQUIRED)
In `main.py` line 835, add:
```python
"operator_model":             "operator_model",
```

### 2. Update config page endpoint (REQUIRED)
In `main.py` line 667, change to:
```python
"model":  rt.get("operator_model") or cfg.get("operator", {}).get("model", ""),
```
This allows runtime override to take effect.

### 3. Update web UI to make model editable (REQUIRED)
In `web/index.html` line 5628, change from read-only display to text input:
```javascript
html += cfgInput('operator_model', 'Operator Model', c.operator?.model || '');
```

### 4. Add model support to Harness endpoints (OPTIONAL)
Allow `model` param in `/api/v1/harness/orchestrate` and other harness endpoints.

### 5. Document sub-hook models (OPTIONAL)
Add separate config keys:
- `operator_pre_hook_model`
- `operator_post_hook_model`

---

## Testing Checklist
- [ ] Change operator model via web UI Config tab
- [ ] Verify `/api/v1/config` returns new value
- [ ] Verify operator actually uses new model in next call
- [ ] Verify wire logs show correct model name
- [ ] Test fallback when model not set (should use default)
- [ ] Test request-body override still works
