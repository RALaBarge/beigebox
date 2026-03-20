# BeigeBox Configuration System Refactoring

## Overview

This document describes the multi-phase refactoring of BeigeBox's configuration system to improve clarity, consistency, and maintainability.

**Status:** Planning phase - Branch `config/phases-1-2-3-refactor` contains design and documentation
**Target Release:** v2.0

---

## Problem Statement

The current config.yaml has grown organically, resulting in:

1. **Scattered feature flags** — 20+ `enabled` flags in 10+ sections with mixed naming patterns
2. **Model duplication** — 4 different naming patterns (default_model, model, summary_model, allowed_models)
3. **Agent config fragmentation** — Timeout/retry settings inconsistently named across decision_llm, operator, harness
4. **Routing logic spread** — Backend selection logic in 5 different sections (backend, backends, decision_llm, embedding, routing)
5. **Missing hierarchy** — No clear distinction between core features, advanced features, and sub-features

---

## Proposed Solution: Three-Phase Refactoring

### Phase 1: Feature Flags Centralization

**Goal:** One place to see all toggles

**Current:** Scattered across config
```yaml
backends_enabled: true           # Top level
decision_llm.enabled: true       # Nested
voice_enabled: false             # Suffix pattern
local_models.filter_enabled: false  # Different name
```

**Target:** Unified section
```yaml
features:
  backends: true
  decision_llm: true
  voice: false
  local_models: false
  # ... 20+ others
```

**Impact:** 2-3 files, 2-4 hours implementation

---

### Phase 2: Agent Config Consolidation + Models Registry

**Goal:** Single source of truth for model allocation and unified agent configuration

**Current Issues:**
- Model names scattered: `backend.default_model`, `decision_llm.model`, `operator.model`, `auto_summarization.summary_model`
- Timeout naming inconsistent: `timeout`, `timeout_ms`, `run_timeout`, `timeouts.task_seconds`
- Pre/post hooks duplicate `model` field
- No explicit model fallback chain

**Target:**
```yaml
models:
  default: "qwen3:4b"
  profiles:
    routing: "llama3.2:3b"      # decision_llm uses this
    agentic: "qwen3:4b"         # operator uses this
    summary: "llama3.2:3b"      # auto_summarization uses this

agents:
  decision_llm:
    enabled: true
    timeout_ms: 5000            # Unified unit

  operator:
    enabled: true
    timeout_ms: 300000          # Per-iteration
    run_timeout_s: 600          # Total wall-clock
    # ... rest of config

  harness:
    enabled: true
    timeout_ms:
      task: 120000              # Unified units
      operator: 180000
    # ... rest of config
```

**Impact:** 5-6 files, 20-25 hours implementation

---

### Phase 3: Routing Consolidation

**Goal:** Explicit routing tier pipeline in one place

**Current Issues:**
- Tier 1 backends: `backend.url`, `backends[]`, `backends_enabled`
- Tier 2 classifier: `classifier` section
- Tier 3 cache: `semantic_cache` section
- Tier 4 decision LLM: `decision_llm` section
- No clear ordering or fallback chain

**Target:**
```yaml
routing:
  tiers:
    1_backends:
      enabled: true
      backends: [...]  # Moved from top-level backends

    2_classifier:
      enabled: false

    3_semantic_cache:
      enabled: false

    4_decision_llm:
      enabled: true
      temperature: 0.2
      timeout_ms: 5000

  fallback_model: "qwen3:4b"
  force_route: ""
```

**Impact:** 8-10 files, 25-30 hours implementation

---

## Implementation Strategy

### Backward Compatibility
- Auto-migration on startup (v1 → v2)
- Old config format works with warnings
- Fallback to original format if new format unavailable

### Test Coverage
- Config loading tests for both v1 and v2
- Migration tests (v1 → v2 roundtrip)
- Runtime override tests
- Tier routing tests (Phase 3)

### Documentation
- `config.yaml.v2-template` — Reference for new format
- `REFACTORING.md` (this file) — Implementation guide
- Inline code comments explaining schema changes
- `2600/config/REFACTOR-PHASES-1-2-3.md` — Detailed technical plan
- `2600/config/phase-4-tools-restructuring.md` — Future improvements
- `2600/config/phase-5-storage-consolidation.md` — Future improvements

---

## Files Affected by Each Phase

### Phase 1 (Feature Flags)
- `beigebox/config.py` — Add FeaturesConfig Pydantic model
- `beigebox/main.py` — Use features.* instead of scattered flags
- `beigebox/web/index.html` — Centralized "Features" section in Config tab
- `config.yaml` — New features: section

### Phase 2 (Agents + Models)
- `beigebox/config.py` — Add ModelsConfig, AgentsConfig Pydantic models
- `beigebox/agents/operator.py` — Use unified model resolution
- `beigebox/agents/decision.py` — Support runtime model overrides
- `beigebox/agents/harness_orchestrator.py` — Use new timeout structure
- `beigebox/main.py` — Initialize agents from new config
- `beigebox/web/index.html` — New Models and Agents sections

### Phase 3 (Routing)
- `beigebox/backends/router.py` — Tier-based routing
- `beigebox/proxy.py` — Use router.route() instead of individual backends
- `beigebox/main.py` — Initialize routing tiers
- `beigebox/agents/decision.py` — Tier 4 routing
- `beigebox/storage/vector_store.py` — Tier 2 classifier
- `beigebox/web/index.html` — Routing status in Config tab

### Support Across All Phases
- `beigebox/config_migration.py` — Auto-migration v1 → v2 (NEW)
- Tests — Expanded test coverage for new structures
- Documentation — See 2600/ and this file

---

## Migration Path for Users

1. **Current (v1.0):** Existing config.yaml works as-is
2. **Upgrade Warning:** "Config format v1.0 detected. Auto-migrating to v2.0..."
3. **Auto-Migration:** Conversion happens at startup, logged to stdout
4. **Backup:** Original config saved to `config.yaml.v1-backup`
5. **Optional Update:** User can manually update config.yaml to v2 format (see `config.yaml.v2-template`)

### Explicit Migration (for v2 early adopters)
```bash
beigebox --migrate-config
# Converts config.yaml in-place, backs up original
```

---

## Roadmap

### Short Term (v2.0)
- Implement Phases 1-3
- Auto-migration v1 → v2
- Update documentation

### Medium Term (v2.1+)
- Phase 4: Tool configuration restructuring (core/advanced/plugins tiers)
- Phase 5: Storage/persistence consolidation (single data_dir, unified archival)

### Long Term (v3.0)
- Remove v1 config support
- Remove migration code
- Full v2 structure only

---

## Testing Checklist

- [ ] Load old config.yaml with v1 format
- [ ] Auto-migrate to v2 at startup (logged)
- [ ] Load new config.yaml v2 format directly
- [ ] Runtime overrides work with new keys
- [ ] Web UI Config tab shows unified feature list
- [ ] Web UI shows new agents structure
- [ ] Web UI shows new models registry
- [ ] Web UI shows routing tiers
- [ ] Agent initialization uses new config paths
- [ ] Routing tiers evaluate in correct order
- [ ] Model resolution (default → profile → per_task)
- [ ] Timeout handling across all agents

---

## References

- **Design Document:** `2600/config/REFACTOR-PHASES-1-2-3.md`
- **Phase 4 Strategy:** `2600/config/phase-4-tools-restructuring.md`
- **Phase 5 Strategy:** `2600/config/phase-5-storage-consolidation.md`
- **v2 Config Template:** `config.yaml.v2-template`
- **Migration Code:** `beigebox/config_migration.py`

---

## Decision Log

### Phase 1: Feature Flags Centralization
- **Decision:** Centralize to `features:` section
- **Rationale:** Single source of truth for feature state
- **Alternative:** Keep scattered (rejected — too hard to discover)

### Phase 2: Models & Agents
- **Decision:** Unified `models:` with profiles + `agents:` with unified timeout naming
- **Rationale:** Removes duplication, makes cascading clear
- **Alternative:** Keep separate (rejected — users asked for single model registry)

### Phase 3: Routing
- **Decision:** Explicit `routing.tiers.*` numbered 1-4
- **Rationale:** Clear tier ordering, easy to understand pipeline
- **Alternative:** Keep scattered (rejected — routing logic is hard to follow currently)

### Backward Compatibility
- **Decision:** Auto-migrate v1 → v2 at startup
- **Rationale:** Users don't need manual steps
- **Deprecation Plan:** v2 releases with v1 support, remove in v3

---

## FAQ

**Q: Do I need to update my config.yaml immediately?**
A: No. Auto-migration works fine. But reading `config.yaml.v2-template` helps understand the new structure.

**Q: Can I revert to v1 format?**
A: Your backup is in `config.yaml.v1-backup`. Copy it back to use old format.

**Q: What if I'm using runtime overrides with old keys?**
A: Auto-migration handles common patterns. Rare cases may need manual adjustment.

**Q: When will v1 support be dropped?**
A: v3.0 (not soon). v2.x releases will support both.

**Q: Can I use v2 config structure with a development build?**
A: Yes, this branch already supports v2 format.
