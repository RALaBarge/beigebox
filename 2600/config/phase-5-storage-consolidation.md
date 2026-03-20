---
name: Phase 5 Storage/Persistence Consolidation Strategy
description: Unify storage, wiretap, payload_log into coherent persistence section; tradeoffs analysis
type: config
---

# Phase 5: Storage/Persistence Consolidation

## Problem Statement

Persistence configuration is scattered across three disconnected sections:

```yaml
# Primary data store
storage:
  type: sqlite
  path: ./data/beigebox.db
  vector_store_path: ./data/chroma
  vector_backend: chromadb

# Request/response logging
wiretap:
  enabled: true
  path: ./data/wire.jsonl
  max_lines: 100000
  rotation_enabled: true

# Full LLM context dump (special case)
payload_log:
  path: ./data/payload.jsonl
  # enabled: via runtime_config only! (hidden)

# Plus scattered in advanced section:
advanced:
  wiretap_compress: false  # Duplication!
```

**Issues:**
1. **Semantic confusion:** All three store data, but treated as unrelated sections
2. **Redundant paths:** `./data/` repeated 3 times; single parent directory implied but not enforced
3. **Disabled state fragmented:** `payload_log.enabled` lives in runtime_config, not in static config
4. **No rotation strategy:** `wiretap.rotation_enabled` but no TTL/cleanup policy
5. **Scattered compression:** `advanced.wiretap_compress` is isolated from wiretap config
6. **Future-hostile:** Adding metrics_log, error_log, trace_log means adding 3+ more sections

## Proposed Structure

```yaml
# Unified persistence configuration
persistence:
  # Data directory (all paths below are relative to this)
  data_dir: ./data

  # Primary storage backends
  backends:
    sqlite:
      enabled: true
      path: beigebox.db          # Relative to data_dir

    vector:
      enabled: true
      path: chroma
      backend: chromadb

  # Diagnostic/observability logging
  diagnostics:
    enabled: true                # Master switch for all diagnostics

    # Request/response streams (high volume)
    wiretap:
      enabled: true
      path: wire.jsonl
      max_lines: 100000

    # Full LLM context payloads (diagnostic, optional)
    payload_log:
      enabled: false             # NOW configurable statically!
      path: payload.jsonl

    # Future: metrics, error traces, etc.
    # metrics_log:
    #   enabled: false
    #   path: metrics.jsonl
    # error_log:
    #   enabled: false
    #   path: errors.jsonl

  # Rotation, compression, retention policy
  archival:
    enabled: true
    compression:
      enabled: false             # Moves from advanced.wiretap_compress
      format: "gzip"             # Future: zstd, brotli
    retention:
      enabled: true
      days: 30                   # Auto-delete logs older than 30 days
      keep_latest: 10            # Always keep at least 10 recent archives

# Removes these old sections:
# - storage (merged to persistence.backends)
# - wiretap (merged to persistence.diagnostics.wiretap)
# - payload_log (merged to persistence.diagnostics.payload_log)
# - advanced.wiretap_compress (merged to persistence.archival.compression)
```

## Benefits

### 1. Clarity & Coherence
- **Single section:** All persistence in one place; easier to audit what's being stored
- **Clear hierarchy:** data_dir → backends vs diagnostics vs archival (three concerns)
- **Explicit paths:** No more `./data/` scattered everywhere
- **Semantic grouping:** "Diagnostics" vs "Production data" is explicit

### 2. Operational Simplicity
- **Single data_dir:** Backup policy is clear (back up everything in `./data/`)
- **Single rotation policy:** All logs share same compression + retention rules
- **Explicit enabled flags:** No more hunting runtime_config for payload_log_enabled
- **Future-proof naming:** Adding new diagnostic logs is just adding to `diagnostics:` block

### 3. Configuration Clarity
- **No duplication:** Compression setting is once (not scattered to advanced section)
- **Retention policy explicit:** TTL for logs defined in one place
- **Master switches:** Can disable all diagnostics without touching individual log configs

### 4. Code Simplicity
```python
# Current: check three places
data_store = config['storage']['path']
wiretap_enabled = config['wiretap']['enabled']
payload_log_enabled = get_runtime_config().get('payload_log_enabled')  # WTF!

# New: unified location
persistence = config['persistence']
data_store = os.path.join(persistence['data_dir'], persistence['backends']['sqlite']['path'])
diagnostics_enabled = persistence['diagnostics']['enabled']
payload_log_enabled = persistence['diagnostics']['payload_log']['enabled']
```

## Downsides & Tradeoffs

### 1. Migration Complexity
**Cost:** Moderate (4-5 files affected)
- Config loading in `config.py` needs new schema
- Storage initialization in `storage/sqlite_store.py` needs path handling
- Wiretap in `wiretap.py` needs rotation/compression setup
- Main.py needs to initialize archival policy
- Web UI Config section needs restructure

**Per-file work:**
- `config.py`: ~50 lines Pydantic schema
- `wiretap.py`: ~30 lines path + rotation logic
- `sqlite_store.py`: ~20 lines path resolution
- `main.py`: ~10 lines archival scheduler setup
- `web/index.html`: ~40 lines config section UI

**Mitigation:** Write migration script to auto-convert old → new format

### 2. Feature Expansion Required
**New feature:** Retention/TTL policy
- Adds archival scheduler at startup
- Needs background task to clean old logs (every 6h? 24h?)
- Could cause I/O spike if massive logs accumulated

**Mitigation:**
- Make archival.enabled = false by default (opt-in)
- Run cleanup in low-traffic window (configurable)
- Add metrics to track cleanup performance

### 3. Breaking Change
```yaml
# Old config
storage:
  path: ./data/beigebox.db

wiretap:
  path: ./data/wire.jsonl

# New config
persistence:
  data_dir: ./data
  backends:
    sqlite:
      path: beigebox.db
```

**Impact:** Existing deployments need config migration

**Mitigation:**
- Auto-migration on startup with warnings
- Fallback to old format if available
- Document in CHANGELOG with upgrade guide

### 4. Path Resolution Complexity
**Current:** Absolute paths, simple
```python
storage_path = config['storage']['path']  # "./data/beigebox.db"
```

**New:** Relative path joining required
```python
data_dir = config['persistence']['data_dir']
storage_path = os.path.join(data_dir, config['persistence']['backends']['sqlite']['path'])
```

**Problem:** What if paths are absolute? Mixed absolute/relative?

**Mitigation:** Define rules in schema:
- `data_dir` can be absolute or relative
- Sub-paths (backend paths) must be relative
- Absolute sub-paths not allowed (raises validation error)

### 5. Archival Scheduler Overhead
**New feature:** Cleanup old logs based on retention policy

**Cost:**
- Adds background asyncio task
- Runs every N hours (configurable)
- Scans filesystem, deletes old files

**Risk:**
- Accidental deletion if retention_days set too low
- I/O spike on large log files

**Mitigation:**
- Default retention_days = 90 (generous)
- Log all deletions to stderr
- Add dry-run mode (archival.dry_run: true) for testing
- Manual cleanup command (`beigebox cleanup`)

## Implementation Plan

### Phase 5.1: Schema & Validation (config.py)
1. Define `PersistenceConfig` Pydantic model
2. Define `BackendConfig`, `DiagnosticsConfig`, `ArchivalConfig` sub-models
3. Add migration logic for old → new format
4. Add validators (relative paths, directory existence)

### Phase 5.2: Storage Initialization (storage/sqlite_store.py, wiretap.py)
1. Update path resolution to use `persistence.data_dir`
2. Update wiretap to support compression + rotation
3. Update sqlite_store to support relative paths
4. Test path resolution (absolute, relative, ~)

### Phase 5.3: Archival Scheduler (main.py)
1. On startup, initialize archival task if enabled
2. Async task that runs every 6h (configurable)
3. Scan diagnostics logs, delete if older than retention_days
4. Log what was deleted

### Phase 5.4: Web UI (web/index.html)
1. Render "Persistence" section with hierarchy
2. Show data_dir, backends toggles, diagnostics toggles
3. Show archival policy (retention days, compression)

### Phase 5.5: Tests & Migration
1. Config migration tests
2. Path resolution tests (absolute, relative, env vars)
3. Archival scheduler tests (dry-run, cleanup logic)
4. Manual test: upgrade old config, verify paths work

## Decision: Proceed?

**Recommendation:** Defer to Phase 5 (as initially planned). This is valuable **long-term** but not urgent:

**Proceed if:**
- We're already refactoring config (Phases 1-3)
- Archival/retention becomes a user requirement
- Scaling to multiple data stores (S3, PostgreSQL)

**Defer if:**
- Current config.yaml works fine for users
- Retention/archival not needed yet
- Want to focus on Phases 1-3 core improvements first

**Incremental approach:**
1. Do Phase 5.1 (schema) with Phase 1-3
2. Keep old format working (migration on startup)
3. Implement Phase 5.2-5.5 later when archival is needed

## Alternative: Minimal Consolidation

If full Phase 5 feels too heavy, do lightweight version:
```yaml
persistence:
  data_dir: ./data
  sqlite_path: beigebox.db       # Just de-duplicate paths
  vector_path: chroma
  wiretap_path: wire.jsonl
  payload_log_path: payload.jsonl
  wiretap_enabled: true
  payload_log_enabled: false
  # No archival, no backends subsection, just paths
```

**Benefit:** 5 lines of code instead of 50
**Drawback:** Doesn't scale to multi-backend future, less semantic
