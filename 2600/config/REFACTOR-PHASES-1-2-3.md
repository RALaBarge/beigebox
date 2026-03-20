---
name: Config Refactoring Phases 1-3 Implementation Guide
description: Complete guide to feature flags, agents, models, and routing consolidation
type: architecture
---

# Config Refactoring: Phases 1-3 Implementation

## Overview

This document outlines the comprehensive refactoring of BeigeBox's configuration system across three phases:

- **Phase 1:** Centralize feature flags (20 scattered → 1 section)
- **Phase 2:** Unify agent configuration + create models registry
- **Phase 3:** Consolidate routing logic (5 sections → 1)

**Branch:** `config/phases-1-2-3-refactor`
**Total Files Modified:** ~15
**Estimated Implementation Time:** 40-50 hours

---

## Phase 1: Feature Flags Centralization

### Rationale
Currently, feature activation is scattered across the config:
- Top-level: `backends_enabled`, `voice_enabled`
- Nested: `decision_llm.enabled`, `operator.enabled`, `tools.enabled`
- Custom names: `filter_enabled`, `ralph_enabled`, `rotation_enabled`

This creates:
1. **Discovery problem:** Where are all the toggles?
2. **Typo risk:** 4 different naming patterns
3. **UX problem:** Web UI Config tab doesn't have a unified "Features" section

### New Structure

```yaml
# Central feature registry - single place to toggle features on/off
features:
  # Backend & Routing
  backends: true
  decision_llm: true
  semantic_cache: false
  classifier: false

  # Agents
  operator: true
  harness: true

  # System Features
  cost_tracking: true
  conversation_replay: true
  auto_summarization: false
  system_context: false

  # Observability
  wiretap: true
  payload_log: false

  # Advanced
  wasm: false
  guardrails: false
  amf_mesh: false
  tools: true

  # UI/UX
  voice: false
  web_ui_voice: false

# Subsections keep local enabled for sub-features:
operator:
  enabled: true  # Global on/off (from features.operator)
  shell:
    enabled: true  # Local: shell is a sub-feature of operator
  autonomous:
    enabled: false
  pre_hook:
    enabled: false

tools:
  enabled: true  # Global (from features.tools)
  plugins:
    enabled: true  # Local: plugins are a sub-feature of tools ecosystem
```

### Code Changes

**beigebox/config.py:**
```python
class FeaturesConfig(BaseConfig):
    backends: bool = True
    decision_llm: bool = True
    operator: bool = True
    harness: bool = True
    cost_tracking: bool = True
    conversation_replay: bool = True
    auto_summarization: bool = False
    system_context: bool = False
    wiretap: bool = True
    payload_log: bool = False
    wasm: bool = False
    guardrails: bool = False
    amf_mesh: bool = False
    tools: bool = True
    voice: bool = False
    web_ui_voice: bool = False

class BeigeBoxConfig(BaseConfig):
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    # ... rest of config
```

**beigebox/main.py:**
- Remove individual `_enabled` checks scattered through startup
- Add centralized feature audit at startup
- Log which features are active to help users debug config

**beigebox/web/index.html:**
```javascript
// New "Features" section in Config tab
html += SO('Features (Master Control)', true);
html += cfgToggle('features_backends', 'Multi-backend routing', c.features?.backends ?? true);
html += cfgToggle('features_decision_llm', 'Decision LLM (routing judge)', c.features?.decision_llm ?? true);
// ... one toggle per feature
html += SC;
```

### Files Modified
1. `beigebox/config.py` — Add FeaturesConfig class
2. `beigebox/main.py` — Remove scattered `_enabled` flags, centralize feature audit
3. `beigebox/web/index.html` — Add "Features" section to Config tab
4. `config.yaml` — Add features: section, remove top-level enabled flags
5. Tests — Add feature flag tests

### Migration Strategy
Old config:
```yaml
backends_enabled: true
decision_llm:
  enabled: true
operator:
  enabled: true
```

Auto-migration:
```python
def migrate_features(old_config):
    features = {
        'backends': old_config.get('backends_enabled', True),
        'decision_llm': old_config.get('decision_llm', {}).get('enabled', True),
        'operator': old_config.get('operator', {}).get('enabled', True),
        # ... etc
    }
    return features
```

---

## Phase 2: Agent Config Consolidation + Models Registry

### Rationale
1. **Agents fragmented:** timeout/model naming inconsistent across decision_llm, operator, harness
2. **Models duplicated:** 4 naming patterns (default_model, model, summary_model, allowed_models)
3. **Fallback chain implicit:** No explicit cascade documented

### New Structure

```yaml
# Unified models registry (replaces backend.default_model, decision_llm.model, operator.model, etc.)
models:
  default: "qwen3:4b"          # Global fallback (moves from backend.default_model)

  # Model allocation by agent role
  profiles:
    routing: "llama3.2:3b"     # decision_llm (judge)
    agentic: "qwen3:4b"         # operator (tool use)
    summary: "llama3.2:3b"      # auto_summarization

  # Per-subtask overrides (optional)
  per_task:
    pre_hook: null              # null = inherit from agentic
    post_hook: null
    reflection: null
    context_pruning: null

  # Model availability (moves from local_models)
  whitelist:
    enabled: false
    allowed_models: []          # fnmatch patterns

# Unified agent config (replaces decision_llm, operator separate sections)
agents:
  decision_llm:
    enabled: true               # From features.decision_llm
    temperature: 0.2
    timeout_ms: 5000            # Unified timeout in ms

  operator:
    enabled: true               # From features.operator
    timeout_ms: 300000          # Per-iteration timeout
    run_timeout_s: 600          # Total wall-clock timeout
    max_iterations: 10

    # Sub-features
    shell:
      enabled: true
      allowed_commands: []
      blocked_patterns: []

    autonomous:
      enabled: false
      temperature: 0.7
      timeout_ms: 180000

    pre_hook:
      enabled: false
      timeout_ms: 8000

    post_hook:
      enabled: false
      timeout_ms: 3000

    # Tool profile selection
    tool_profiles:
      qwen3:4b:
        - web_search
        - calculator
        - memory
      llama3.2:3b:
        - web_search
        - calculator

  harness:
    enabled: true               # From features.harness
    ralph_enabled: false

    retry:
      max_retries: 2
      backoff_base: 1.5
      backoff_max: 10

    timeout_ms:                 # Unified (was timeouts.task_seconds)
      task: 120000
      operator: 180000

    shadow_agents:
      enabled: false
      timeout_ms: 30000
      max_tool_calls: 3
      divergence_threshold: 0.3

    stagger:
      operator_ms: 1000         # Unified units
      model_ms: 400

    store_runs: true
    max_stored_runs: 1000
```

### Code Changes

**beigebox/config.py:**
```python
class ModelsConfig(BaseConfig):
    default: str = "qwen3:4b"
    profiles: Dict[str, str] = Field(default_factory=lambda: {
        'routing': 'llama3.2:3b',
        'agentic': 'qwen3:4b',
        'summary': 'llama3.2:3b',
    })
    per_task: Dict[str, Optional[str]] = Field(default_factory=dict)
    whitelist: Dict = Field(default_factory=lambda: {
        'enabled': False,
        'allowed_models': []
    })

class AgentConfig(BaseConfig):
    enabled: bool = True
    timeout_ms: int = 5000

class DecisionLLMConfig(AgentConfig):
    temperature: float = 0.2

class OperatorConfig(AgentConfig):
    timeout_ms: int = 300000
    run_timeout_s: int = 600
    max_iterations: int = 10
    # ... rest of operator-specific config

class AgentsConfig(BaseConfig):
    decision_llm: DecisionLLMConfig
    operator: OperatorConfig
    harness: HarnessConfig
```

**beigebox/agents/operator.py:**
```python
# Old fallback chain
self._model = (
    model_override
    or self.rt.get("operator_model")
    or cfg.get("operator", {}).get("model")
    or cfg.get("backend", {}).get("default_model")
)

# New: unified model lookup
def get_model(self, override=None):
    rt = get_runtime_config()
    # Try runtime override first
    if override:
        return override
    if rt.get("models_agentic"):
        return rt.get("models_agentic")
    # Fall back to static config
    models_cfg = cfg.get("models", {})
    return models_cfg.get("profiles", {}).get("agentic") or models_cfg.get("default")
```

**beigebox/agents/decision.py:**
```python
# Old: hardcoded at startup
model = cfg.get("decision_llm", {}).get("model", "")

# New: support runtime override
def get_model(self):
    rt = get_runtime_config()
    if rt.get("models_routing"):
        return rt.get("models_routing")
    models_cfg = cfg.get("models", {})
    return models_cfg.get("profiles", {}).get("routing") or models_cfg.get("default")
```

### Files Modified
1. `beigebox/config.py` — ModelsConfig, AgentsConfig classes
2. `beigebox/agents/operator.py` — Update model resolution
3. `beigebox/agents/decision.py` — Support runtime model override
4. `beigebox/agents/harness_orchestrator.py` — Update timeout handling
5. `beigebox/main.py` — Update agent initialization, add runtime overrides
6. `beigebox/web/index.html` — New UI sections for models + agents
7. `config.yaml` — New structure
8. Tests — Add model resolution tests

### Migration Strategy
```python
def migrate_models_and_agents(old_config):
    new = {
        'models': {
            'default': old_config.get('backend', {}).get('default_model', 'qwen3:4b'),
            'profiles': {
                'routing': old_config.get('decision_llm', {}).get('model', 'llama3.2:3b'),
                'agentic': old_config.get('operator', {}).get('model', 'qwen3:4b'),
                'summary': old_config.get('auto_summarization', {}).get('summary_model', 'llama3.2:3b'),
            },
            'whitelist': old_config.get('local_models', {}),
        },
        'agents': {
            'decision_llm': old_config.get('decision_llm', {}),
            'operator': old_config.get('operator', {}),
            'harness': old_config.get('harness', {}),
        },
    }
    return new
```

---

## Phase 3: Routing Consolidation

### Rationale
Currently, routing is scattered:
- `backend.url`, `backend.timeout`
- `backends_enabled`, `backends[]`
- `decision_llm` (tier 4)
- `embedding.backend`
- `routing` section (empty)

**Goal:** Single `routing` section with explicit tier ordering and fallback chain.

### New Structure

```yaml
routing:
  # Explicit tier ordering (1=first, 4=last)
  # This documents the routing pipeline clearly
  tiers:
    # Tier 1: Static backend selection (multi-backend routing)
    1_backends:
      enabled: true
      backends:
        - provider: ollama
          name: ollama-local
          url: http://localhost:11434
          priority: 1
          timeout_ms: 120000
          max_retries: 2
          backoff_base: 1.5
        # ... more backends

    # Tier 2: Embedding-based classification (if enabled)
    2_classifier:
      enabled: true
      model: "nomic-embed-text"
      border_threshold: 0.04

    # Tier 3: Semantic cache (optional)
    3_semantic_cache:
      enabled: false
      similarity_threshold: 0.95
      max_entries: 10000
      ttl_seconds: 3600

    # Tier 4: LLM-based routing (decision agent)
    4_decision_llm:
      enabled: true
      # decision_llm config lives here (moves from agents.decision_llm)
      temperature: 0.2
      timeout_ms: 5000
      max_tokens: 256

  # Global routing settings
  session_ttl_seconds: 1800
  fallback_model: "qwen3:4b"  # If all tiers fail

  # Runtime overrides
  force_route: ""             # "simple"|"complex"|"code"|"large"|""
  force_decision: false       # Force tier 4 every request

# Removes:
# - backend.url (goes to routing.tiers.1_backends[0].url)
# - backends_enabled (goes to routing.tiers.1_backends.enabled)
# - backends array (goes to routing.tiers.1_backends.backends)
# - decision_llm section (moves to routing.tiers.4_decision_llm)
# - embedding.backend (goes to routing.tiers.2_classifier.model)
```

### Code Changes

**beigebox/backends/router.py:**
```python
# Current: multiple fallback checks
def select_backend(self, model):
    if self.static_backends:
        return self.static_backends[0]
    return self.openrouter

# New: explicit tier evaluation
def route(self, request):
    # Tier 1: Static backends
    if self.tiers.tier_1_backends.enabled:
        backend = self.select_static_backend(request)
        if backend:
            return backend

    # Tier 2: Classifier
    if self.tiers.tier_2_classifier.enabled:
        route = self.classify(request)
        if route:
            return self.backends_for_route(route)

    # Tier 3: Semantic cache
    if self.tiers.tier_3_cache.enabled:
        cached = self.semantic_cache.lookup(request)
        if cached:
            return cached

    # Tier 4: Decision LLM
    if self.tiers.tier_4_decision.enabled:
        decision = self.decision_agent.route(request)
        return decision

    # Fallback
    return self.default_backend
```

**beigebox/proxy.py:**
```python
# Old: check self.backend_url, self.backend_router separately
# New: single self.router.route(request)
```

### Files Modified
1. `beigebox/backends/router.py` — Restructure to tier-based routing
2. `beigebox/proxy.py` — Use router tiers
3. `beigebox/main.py` — Initialize routing tiers
4. `beigebox/storage/vector_store.py` — Embedding classifier config
5. `beigebox/agents/decision.py` — Decision agent in tier 4
6. `beigebox/web/index.html` — Show routing tier status
7. `config.yaml` — New routing structure
8. Tests — Tier routing tests

### Migration Strategy
```python
def migrate_routing(old_config):
    return {
        'routing': {
            'tiers': {
                '1_backends': {
                    'enabled': old_config.get('backends_enabled', True),
                    'backends': old_config.get('backends', []),
                },
                '2_classifier': {
                    'enabled': old_config.get('classifier', {}).get('enabled', False),
                },
                '3_semantic_cache': {
                    'enabled': old_config.get('semantic_cache', {}).get('enabled', False),
                },
                '4_decision_llm': {
                    'enabled': old_config.get('decision_llm', {}).get('enabled', True),
                    'temperature': old_config.get('decision_llm', {}).get('temperature', 0.2),
                    'timeout_ms': old_config.get('decision_llm', {}).get('timeout', 5) * 1000,
                },
            },
            'fallback_model': old_config.get('backend', {}).get('default_model', 'qwen3:4b'),
            'force_route': old_config.get('runtime', {}).get('force_route', ''),
        }
    }
```

---

## Complete Migration Flow

1. **Load old config.yaml** → Detect version
2. **Run auto-migration** → Convert to new format
3. **Validate new config** → Pydantic validation
4. **Log migration** → "Migrated config from v1 to v2.0"
5. **Save migrated config** → Write to backup file (config.yaml.v1-backup)
6. **Continue startup** → Use new config

---

## Testing Strategy

### Phase 1 Tests
- Feature flag loading
- Feature toggle at runtime
- Web UI feature list rendering

### Phase 2 Tests
- Model resolution (default → profile → per_task)
- Agent config loading
- Agent timeout handling
- Runtime model overrides

### Phase 3 Tests
- Tier ordering (1→2→3→4)
- Tier fallback chain
- Force_route behavior
- Backward compatibility

### Integration Tests
- Full config migration
- Startup with migrated config
- Runtime overrides after migration

---

## Deployment Strategy

1. **Branch:** `config/phases-1-2-3-refactor`
2. **Backward compatibility:** Auto-migration on startup
3. **Warnings:** Log old config format detected, migration performed
4. **Rollback:** Keep old config in `.v1-backup` file
5. **Documentation:** Update README, CONTEXT, config comments
6. **Release notes:** Explain breaking changes, migration path

---

## Open Questions

1. **Web UI migration:** Show old config in Config tab? Or new format only?
   - **Decision:** New format only; migration happens at startup
2. **Runtime override keys:** `models_agentic` or `agents.operator.model`?
   - **Decision:** Flat keys like `models_agentic` (easier HTTP POST)
3. **Backward compatibility duration:** Keep auto-migration forever?
   - **Decision:** 2 releases (v1.2, v1.3), then remove in v2.0
4. **Default timeout units:** Always ms? Or mixed (s, ms)?
   - **Decision:** Always ms internally, suffix `_s` for seconds in YAML (explicit)
