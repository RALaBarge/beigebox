# Configuration Drift Audit Report

**Date:** 2026-04-21  
**Scope:** Hardcoded defaults, duplicated config, inconsistent fallbacks  
**Severity:** CRITICAL — Any model/threshold change requires updating 20+ files

---

## Executive Summary

BeigeBox has **3 major configuration drift categories** that prevent immutability:

1. **Model Defaults** — Hardcoded in 16+ places across 5 files
2. **Embedding Model** — Hardcoded in 3 places, used in 7 files
3. **Semantic Cache Threshold** — Inconsistent values (0.92 vs 0.95) across 3 files
4. **Configuration Duplication** — Same settings defined in 4 different files
5. **Stale Values** — Outdated model names like "gemma3:4b" in some configs

---

## Issue 1: Model Defaults Fragmentation

### The Problem

Default model is hardcoded in **16 fallback statements** across **5 files**:

| File | Count | Fallback Pattern |
|------|-------|------------------|
| `beigebox/main.py` | 4 | `cfg.get("models", {}).get("default", "qwen3:4b")` |
| `beigebox/config_migration.py` | 4 | Migration fallbacks to "qwen3:4b" |
| `beigebox/discovery/runner.py` | 2 | Judge/candidate model fallback |
| `beigebox/dgm/loop.py` | 1 | Routing model hardcoded |
| `beigebox/config.py` | 1 | Pydantic schema: `default: str = "qwen3:4b"` |

### Proof of Concept

Commit `072a4271` had to manually change **28 files** just to swap `llama3.2:3b` → `qwen3:4b`. That's because defaults are scattered.

### Impact

To change the default model globally:
1. Edit `config.yaml` ❌
2. Edit `config.example.yaml` ❌
3. Edit `docker/config.docker.yaml` ❌
4. Edit `docker/config.yaml` ❌
5. Edit `beigebox/config.py` ❌
6. Edit `beigebox/main.py` (4 places) ❌
7. Edit `beigebox/config_migration.py` (4 places) ❌
8. Edit `beigebox/discovery/runner.py` (2 places) ❌
9. Edit `beigebox/dgm/loop.py` ❌
10. Update 50+ test files ❌

**Result:** High risk of inconsistency, impossible to guarantee all usages updated.

---

## Issue 2: Embedding Model Fragmentation

### The Problem

Embedding model `nomic-embed-text` is hardcoded in **3 places**:

| File | Pattern |
|------|---------|
| `beigebox/cache.py:176` | `embed_cfg.get("model", "nomic-embed-text")` |
| `beigebox/agents/embedding_classifier.py:203` | `cfg.get("embedding", {}).get("model", "nomic-embed-text")` |
| `beigebox/config_migration.py:81` | Migration fallback to "nomic-embed-text" |

And used in **7 files total** (config files, security scanner, RAG, etc.)

### Impact

If you need to swap the embedding model (e.g., due to performance), you'd have to:
- Update 3 fallback statements
- Update config files  
- Update all test fixtures
- Re-run embeddings on all cached documents

---

## Issue 3: Semantic Cache Threshold Inconsistency

### The Problem

The similarity threshold for semantic cache has **conflicting defaults**:

| Source | Value | Context |
|--------|-------|---------|
| `beigebox/cache.py:171` | `0.92` | Fallback default in code |
| `beigebox/config_migration.py:133` | `0.95` | Migration fallback |
| `docker/config.docker.yaml:95` | `0.92` | Docker config |
| `config.yaml:576` | ??? | Documentation only (no value) |
| Documentation | `0.92` | Mentioned in discovery docs |

### Which is Correct?

When a request comes in, which value is used?

1. Config value (from `config.yaml`) → if set
2. Runtime override → if set  
3. **FALLBACK** → either 0.92 or 0.95 depending on which code path

Result: **Unpredictable behavior** if config missing.

---

## Issue 4: Configuration Duplication

Four files define nearly identical model configurations:

| File | qwen3:4b refs | nomic-embed | Lines |
|------|------|---------|-------|
| `config.example.yaml` | 7 | 1 | Full example |
| `docker/config.docker.yaml` | 10 | 1 | Docker defaults |
| `docker/config.yaml` | 4 | 1 | Docker copy |
| `config.yaml` | 1 | 1 | Production |

**No single source of truth.** If you update one, the others drift.

---

## Issue 5: Stale Model Names

At least one config file still references outdated models:

```yaml
# docker/config.docker.linux.old.yaml — likely unused but could cause confusion
routing: "gemma3:4b"  # ← Wrong model! Should be qwen3:4b or llama3.2:3b
```

---

## Root Cause Analysis

### Why This Happened

1. **Config grew organically** — Started simple (1 file), became complex (4 files)
2. **No centralized constant** — Models treated as strings, not configuration
3. **Fallbacks everywhere** — Each module added its own default "just in case"
4. **Tests hardcoded** — 50+ test files have model names inline
5. **Old configs not cleaned up** — `.old` and `.example` files still present

### Historical Evidence

- **Commit 072a4271:** "chore: replace llama3.2:3b with qwen3:4b as default model throughout" → Had to touch 28 files
- **Commit 37805815:** "refactor: update code references — backend.default_model → models.default" → Another refactor needed
- Multiple "fix" commits related to model mismatches

---

## Risk Assessment

| Scenario | Current State | Risk |
|----------|---------------|------|
| Change default model | Must edit 12+ files | **CRITICAL** — Easy to miss one |
| Change embedding model | Must edit 3+ files + tests | **CRITICAL** — Embeddings cached |
| Change cache threshold | Inconsistent defaults | **HIGH** — Unpredictable behavior |
| Add new model profile | Config duplication | **HIGH** — Have to update 4 files |
| Deploy to new environment | Copy wrong config file | **MEDIUM** → **CRITICAL** |

---

## Recommended Fixes

### Priority 1: Consolidate Model Defaults (CRITICAL)

- Create `beigebox/constants.py:ModelDefaults` class with centralized model names
- Remove all hardcoded "qwen3:4b" fallbacks
- Use single source of truth for all model references

### Priority 2: Fix Semantic Cache Threshold (HIGH)

- Decide: 0.92 or 0.95?
- Make consistent across all files
- Add validation at startup

### Priority 3: Consolidate Config Files (HIGH)

- Single source of truth for model config
- Generate example/docker configs from template
- Remove duplicates, validate consistency at startup

### Priority 4: Clean Up Test Fixtures (MEDIUM)

- Use a shared test constant for model names
- Replace 50+ hardcoded model strings with constant reference

### Priority 5: Remove Stale Files (LOW)

- Delete `*.old.yaml` files
- Archive historical configs to `d0cs/history/`

---

## Files Affected

### Immediate Changes Needed

- `beigebox/config.py` — Remove hardcoded default from Pydantic schema
- `beigebox/main.py` — 4 fallback statements → use constant
- `beigebox/cache.py` — Embedding model fallback → use constant
- `beigebox/agents/embedding_classifier.py` — Embedding model fallback
- `beigebox/config_migration.py` — Multiple model fallbacks
- `beigebox/discovery/runner.py` — 2 model fallbacks
- `beigebox/dgm/loop.py` — Routing model hardcoded
- `config.yaml`, `config.example.yaml`, `docker/config.docker.yaml`, `docker/config.yaml` — Consolidate

### Indirect Impact

- 50+ test files that hardcode model names
- Documentation that mentions specific models
- Deployment scripts that reference model names

---

## Git History

Key commits showing the problem:

1. `072a4271` — Had to change 28 files to swap one model name
2. `37805815` — Refactored config structure, proving fragility
3. `e8c6c87d` — "config cleanup — remove redundant blocks"
4. `b210325d` — "implement Phase 2 config refactoring"

Each of these commits suggests configuration was getting out of sync.

---

## Next Steps

1. ✅ Review this audit with user
2. ⏳ Implement Priority 1 fix (centralized model defaults)
3. ⏳ Implement Priority 2 fix (semantic cache threshold)
4. ⏳ Implement Priority 3 fix (config consolidation)
5. ⏳ Validate no regressions in tests
6. ⏳ Document the new immutable configuration system
