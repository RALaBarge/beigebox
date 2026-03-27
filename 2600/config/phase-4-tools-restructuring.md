# 🚫 WON'T DO — Decision made 2026-03-23: Option B (skip). Current tools: section works fine. Progressive tool disclosure benefit covered by MCP tool limiting (discover_tools) instead. No structural tools: reorganization planned.

---
name: Phase 4 Tools Restructuring Strategy
description: Tool configuration reorganization - core vs advanced vs plugins; tradeoffs analysis
type: config
---

# Phase 4: Tool Configuration Restructuring

## Problem Statement

The current `tools:` section conflates concerns and creates confusion:

```yaml
tools:
  enabled: true
  registry:                    # Array of tool definitions
    - name: web_search
      enabled: true

  browserbox:                  # Individual advanced tool config
    enabled: false
    ws_url: ...

  cdp:                        # Another advanced tool
    enabled: true
    ws_url: ...

  memory:                     # Another advanced tool
    enabled: true
    max_results: 3

  plugins:                    # Plugin system
    enabled: true
    path: ./plugins
```

**Issues:**
1. **Mental model confusion:** Tools have `enabled` in registry AND in individual sections (duplication)
2. **Missing separation:** No clear distinction between core tools (built-in), advanced tools (external deps), and plugins (user-defined)
3. **Inconsistent structure:** Registry is array, individual tools are objects — two data models for same thing
4. **No tier control:** Can't enable/disable "all advanced tools" without touching each one

## Proposed Structure

```yaml
tools:
  enabled: true                    # Master switch for entire tool system

  # Core tools (no external dependencies, always available if enabled)
  core:
    web_search:
      enabled: true
      provider: "duckduckgo"       # configurable provider
    calculator:
      enabled: true
    datetime:
      enabled: true
    system_info:
      enabled: true
      # Per-tool options can expand here

  # Advanced tools (external deps: websockets, APIs, models)
  advanced:
    memory:                        # Vector search + semantic similarity
      enabled: true
      max_results: 3
      min_score: 0.3
      query_preprocess: false

    cdp:                          # Chrome DevTools Protocol
      enabled: true
      ws_url: ws://localhost:9222
      timeout: 10

    browserbox:                    # BrowserBox relay
      enabled: false
      ws_url: ${BROWSERBOX_WS_URL:-ws://localhost:9009}
      timeout: 10

  # User-defined plugins
  plugins:
    enabled: true
    path: ./plugins

    # Per-plugin overrides
    overrides:
      zip_inspector:
        enabled: true
      doc_parser:
        enabled: true
        ocr_model: ""
        max_ref_tokens: 4000
        chunk_size: 800
        chunk_overlap: 80
```

## Benefits

### 1. Clarity
- **Tier system explicit:** Core (built-in) vs Advanced (external) vs Plugins (user)
- **Single data model:** All tools use same `name: {enabled, config}` pattern
- **No duplication:** `enabled` appears once per tool, not in registry + individual section

### 2. Scalability
- **Easy to add tools:** New core tool? Add to `core:`. New advanced tool? Add to `advanced:`
- **Tier-level control:** Operator can query "what tools are in tier X?" for allocation decisions
- **Future-proof:** Can add new tiers (e.g., `experimental:`, `deprecated:`) without config chaos

### 3. Maintainability
- **Clear relationships:** registry no longer separate from configs
- **Single source of truth:** Tool state (enabled/disabled) lives once per tool
- **Simpler validation:** Pydantic model is flat per tier, not mixed array+object

### 4. UX Improvement
- Web UI Config tab shows: "Core Tools" / "Advanced Tools" / "Plugins" sections
- User can see at a glance which tier each tool belongs to
- Can enable/disable all of tier with a single checkbox (future enhancement)

## Downsides & Tradeoffs

### 1. Migration Burden
**Cost:** Moderate (3-4 files affected)
- Config loading code in `config.py` needs new validation
- Tool discovery in `tools/plugin_loader.py` needs to iterate over tiers
- Web UI Config section needs restructure
- Runtime config overrides need tier awareness

**Mitigation:** Config migration script can auto-convert old → new format

### 2. Breaking Change
**Impact:** Existing config.yaml files will not load until updated

```python
# Migration logic needed:
def migrate_tools_config(old: dict) -> dict:
    """Flatten registry + individual tools into tier structure"""
    core_tools = {t['name']: {**t, 'enabled': t.get('enabled', True)}
                  for t in old.get('registry', [])}
    advanced = {
        'memory': old.get('memory', {}),
        'cdp': old.get('cdp', {}),
        'browserbox': old.get('browserbox', {}),
    }
    return {'core': core_tools, 'advanced': advanced, 'plugins': old.get('plugins', {})}
```

**Mitigation:** Provide auto-migration at startup, log warnings for old format

### 3. Operator Code Impact
**Current:** Operator queries `config.tools` dictionary freely
```python
def get_available_tools(self):
    enabled_tools = [t for t in config['tools']['registry'] if t['enabled']]
```

**New:** Operator needs to understand tiers
```python
def get_available_tools(self, include_advanced=True, include_plugins=True):
    tools = list(config['tools']['core'].values())
    if include_advanced:
        tools += list(config['tools']['advanced'].values())
    if include_plugins:
        tools += list(config['tools']['plugins']['overrides'].values())
    return [t for t in tools if t.get('enabled', True)]
```

**Mitigation:** Add helper methods in config.py to abstract tier iteration

### 4. Runtime Override Complexity
**Current:** `tools_disabled: ['web_search', 'memory']` (flat list)

**New:** Could be more explicit about tier
```yaml
runtime:
  tools:
    disabled: ['web_search', 'memory']
    disabled_tiers: ['advanced']  # Disable all advanced at once
```

**Mitigation:** Keep backward compatibility with flat `tools_disabled` list

## Implementation Plan

1. **Config Validation (config.py):**
   - Define Pydantic models for ToolTier
   - Add migration logic for old → new format
   - Warn on startup if old format detected

2. **Tool Discovery (tools/plugin_loader.py):**
   - Update iteration to walk tiers
   - Maintain backward-compatible `get_all_tools()` API

3. **Operator Integration (agents/operator.py):**
   - Update `_get_available_tools()` to work with tiers
   - Add `include_advanced`, `include_plugins` parameters for future tool allocation strategies

4. **Web UI (web/index.html):**
   - Render three collapsible sections: Core / Advanced / Plugins
   - Each tool shows enable/disable toggle + config fields

5. **Tests:**
   - Add config migration tests
   - Test tier iteration and filtering
   - Test runtime override with new format

## Decision: Proceed?

**Recommendation:** Defer to Phase 5. This restructuring is valuable but less critical than routing/models consolidation. Current config works fine; this improves clarity more than functionality.

**Alternative:** Implement only if we're already refactoring config heavily (which we are in Phase 1-3). Could be done alongside Phase 2-3 to keep config edits minimal.
