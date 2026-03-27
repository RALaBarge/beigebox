# ✅ COMPLETE — Implemented. VRAM discovery (pynvml), real-time metrics (psutil), 2s top-bar polling, passive SQLite discovery, CLI tools all shipped.

---
title: Model Resource Visibility
subtitle: RAM Footprint Discovery & Real-Time System Metrics in UI
date: 2026-03-18
---

# Model Resource Visibility

## Problem Statement

Currently, BeigeBox operators and admins are **blind to actual resource consumption**:
- What does Claude Opus consume in RAM when loaded? Unknown.
- What about Haiku? Llama 70B? Unknown.
- Is the GPU at capacity? CPU throttled? No visibility.
- When choosing between models, no data on memory trade-offs.

This leads to:
- Silent OOM kills on memory-constrained systems
- Over-provisioning (running models that aren't needed)
- Inability to right-size deployments
- No alerting when system approaches saturation

## Solution: Two-Tier Observability

### Tier 1: Model RAM Footprint Discovery

**What**: Measure actual VRAM/RAM consumed by each model when loaded into inference engine.

**How**:
1. **Discovery Phase** (one-time, per model):
   - Load model via Ollama (or OpenRouter if remote)
   - Poll `/api/ps` endpoint for memory stats
   - Store in `model_specs` table with schema:
     ```sql
     CREATE TABLE model_specs (
       id INTEGER PRIMARY KEY,
       model_name TEXT UNIQUE,
       backend TEXT,  -- "ollama", "openrouter", "vllm"
       vram_mb INTEGER,
       ram_mb INTEGER,
       params_billions REAL,
       discovered_at TIMESTAMP,
       discovery_method TEXT,  -- "ollama_ps", "nvidia_smi", "runtime_inference"
       notes TEXT
     );
     ```

2. **Data Sources**:
   - **Ollama**: Already integrated via `/api/ps` (returns `size_vram`)
   - **NVIDIA GPUs**: `nvidia-smi` parsing (when available)
   - **CPU Models**: `/proc/meminfo` on Linux
   - **Remote Models** (OpenRouter): Estimate from docs or ask API

3. **Accuracy**:
   - Ollama: Exact (from runtime)
   - NVIDIA: Exact (from nvidia-smi)
   - Remote: Best-effort (from model card or docs)

4. **Workflow**:
   ```
   User adds new model to config.yaml
                    ↓
   BeigeBox detects new model
                    ↓
   Attempts to load model (if available locally)
                    ↓
   Polls resource usage (Ollama /api/ps, nvidia-smi, etc.)
                    ↓
   Stores in model_specs table
                    ↓
   UI shows: "Claude Opus: 42 GB VRAM required"
                    ↓
   Deployment tool warns if system capacity < requirement
   ```

### Tier 2: Real-Time System Metrics Dashboard

**What**: Display CPU/RAM/GPU usage in top bar of web UI, updated every 500ms.

**How**:

1. **Server-Side (beigebox/main.py)**:
   ```python
   @app.get("/api/v1/system-metrics")
   async def get_system_metrics():
       """Real-time system resource snapshot."""
       import psutil
       import gputil

       cpu = psutil.cpu_percent(interval=0.1)
       ram = psutil.virtual_memory()

       gpu_info = []
       try:
           gpus = gputil.getGPUs()
           for gpu in gpus:
               gpu_info.append({
                   "id": gpu.id,
                   "name": gpu.name,
                   "load": gpu.load * 100,  # percentage
                   "memory_used": gpu.memoryUsed,
                   "memory_total": gpu.memoryTotal,
               })
       except Exception:
           pass  # GPU detection failed, proceed without

       return {
           "cpu_percent": cpu,
           "ram_percent": ram.percent,
           "ram_used_mb": ram.used // (1024**2),
           "ram_total_mb": ram.total // (1024**2),
           "gpus": gpu_info,
           "timestamp": datetime.now().isoformat(),
       }
   ```

2. **Client-Side (beigebox/web/index.html)**:
   ```javascript
   // Fetch metrics every 500ms
   setInterval(async () => {
       const res = await fetch('/api/v1/system-metrics');
       const metrics = await res.json();

       // Update top bar
       document.getElementById('cpu-usage').textContent =
           `CPU: ${metrics.cpu_percent.toFixed(1)}%`;
       document.getElementById('ram-usage').textContent =
           `RAM: ${metrics.ram_used_mb}/${metrics.ram_total_mb} MB (${metrics.ram_percent.toFixed(1)}%)`;

       // GPU meters
       metrics.gpus.forEach(gpu => {
           const el = document.getElementById(`gpu-${gpu.id}`);
           el.textContent = `GPU${gpu.id}: ${gpu.load.toFixed(1)}% (${gpu.memory_used}/${gpu.memory_total}MB)`;
       });
   }, 500);
   ```

3. **UI Layout** (Top bar, right side):
   ```
   ┌──────────────────────────────────────────────────────────────┐
   │ BeigeBox                                    [≡] [?] [⚙]       │
   │                                    CPU: 45.2% RAM: 8192/16384MB │
   │                                    GPU0: 78% (12456/16384MB)    │
   └──────────────────────────────────────────────────────────────┘
   ```

---

## Implementation Roadmap

### Phase 1: Model Specs Discovery (Week 1)
**Goal**: Measure and store RAM/VRAM for all configured models

**Tasks**:
1. Create `model_specs` SQLite table
2. Add `beigebox discover-models` CLI command
   - Loads each configured model (if available)
   - Polls resource usage
   - Stores results
3. Populate baseline specs for common models (Claude, Opus, Haiku, Llama, Mistral)
4. Update `/api/v1/backends` response to include `model_specs` (optional)

**Acceptance Criteria**:
- [ ] `model_specs` table created with proper schema
- [ ] `beigebox discover-models` discovers RAM footprint for local models
- [ ] For remote models (OpenRouter), docs are parsed or fetched
- [ ] Dashboard endpoint `/api/v1/backends` includes `vram_mb` per model
- [ ] Operator notes show "Claude Opus: 42GB VRAM" when hovering over model

### Phase 2: Real-Time Metrics Endpoint (Week 1)
**Goal**: Expose CPU/RAM/GPU usage via API

**Tasks**:
1. Add `/api/v1/system-metrics` endpoint
   - CPU percent (0-100)
   - RAM percent + absolute values
   - GPU utilization + memory per device (if available)
   - 500ms freshness target
2. Add dependencies: `psutil`, `gputil` (optional, graceful fallback)
3. Add logging: Track metrics changes (alert if >90% sustained)

**Acceptance Criteria**:
- [ ] `/api/v1/system-metrics` endpoint works on Linux + macOS
- [ ] GPU detection works on systems with NVIDIA GPUs
- [ ] Gracefully falls back if GPU unavailable
- [ ] Metrics update every 500ms
- [ ] Latency <100ms (non-blocking)

### Phase 3: UI Top Bar Display (Week 1)
**Goal**: Show real-time metrics in web UI

**Tasks**:
1. Add CSS + HTML for top-bar metrics display (right-aligned)
2. Add JS polling (fetch `/api/v1/system-metrics` every 500ms)
3. Add color coding (green <70%, yellow 70-85%, red >85%)
4. Add tooltips (hover shows details: "GPU0: Tesla A100, 42GB")
5. Optional: Add alert popup if >90% for 10+ seconds

**Acceptance Criteria**:
- [ ] Metrics display in top bar (right-aligned, next to [≡] [?] [⚙])
- [ ] Updates smoothly every 500ms
- [ ] Color coding shows saturation levels
- [ ] Hover tooltips provide details
- [ ] No visual lag (CSS transforms, not layout reflow)

### Phase 4: Deployment Planning Tool (Week 2)
**Goal**: Warn users if deployment config exceeds available resources

**Tasks**:
1. Add deployment health check:
   - Sum model VRAM requirements
   - Compare against available GPU/CPU RAM
   - Warn if close to capacity
2. Generate deployment recommendation:
   - "Best fit for 4 users: Haiku + Opus (24GB VRAM needed, you have 40GB ✓)"
   - "Warning: Running all models simultaneously would require 84GB (you have 40GB ✗)"
3. Export CSV report of model specs for capacity planning

**Acceptance Criteria**:
- [ ] Deployment health check runs at startup
- [ ] Warnings logged if capacity exceeded
- [ ] CSV export of model specs available at `/api/v1/model-specs/export`

---

## Discovery Questions (For Future Optimization)

Once we have resource visibility, we can ask:

1. **Model Selection**: Which model size/speed trade-off is optimal for each task?
   - Haiku (8GB) vs. Opus (42GB): 3x RAM for 20% accuracy gain?
   - When is Haiku "good enough"?

2. **Concurrent Execution**: How many models can run simultaneously without contention?
   - GPU time-sharing (models interleave)?
   - Memory pooling (shared attention buffers)?

3. **Scaling**: What's the cost per inference token across different models?
   - Cost = VRAM * inference_latency / tokens_generated
   - Which model is most cost-effective per task type?

4. **Alerting**: When should we proactively shed load?
   - If GPU >85% sustained, queue new requests instead of blocking
   - If RAM >90%, evict least-used model from cache

---

## Schema

```sql
-- Model specifications (discovered at runtime)
CREATE TABLE model_specs (
    id INTEGER PRIMARY KEY,
    model_name TEXT UNIQUE NOT NULL,  -- "claude-opus", "llama-70b-chat"
    backend TEXT NOT NULL,            -- "ollama", "openrouter", "vllm"

    -- Resource requirements
    vram_mb INTEGER,                  -- Video RAM (GPU memory)
    ram_mb INTEGER,                   -- System RAM
    params_billions REAL,             -- Model size (e.g. 405 for GPT-4)

    -- Discovery metadata
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    discovery_method TEXT,            -- "ollama_ps", "nvidia_smi", "docs_estimate"
    discovery_latency_ms INTEGER,     -- How long discovery took

    -- Caching
    last_loaded_at TIMESTAMP,         -- Last time model was actually loaded
    last_checked_at TIMESTAMP,        -- Last health check

    notes TEXT                        -- Manual notes (e.g. "Requires A100 GPU")
);

-- System metrics snapshots (for historical analysis)
CREATE TABLE system_metrics_snapshots (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cpu_percent REAL,
    ram_used_mb INTEGER,
    ram_total_mb INTEGER,
    gpu_id INTEGER,
    gpu_load REAL,
    gpu_memory_used_mb INTEGER,
    gpu_memory_total_mb INTEGER,
    notes TEXT  -- e.g. "OOM killer triggered"
);
```

---

## Success Metrics

- [ ] Users can see model VRAM requirements before loading
- [ ] Real-time metrics update every 500ms in top bar
- [ ] CPU/RAM/GPU saturation visible at a glance
- [ ] Deployment tool prevents accidental over-provisioning
- [ ] Historical metrics available for capacity planning
- [ ] Zero alerting false positives (only alert on sustained saturation)

---

## Related Issues

- **#TODO-1**: Implement model RAM discovery + storage
- **#TODO-2**: Build `/api/v1/system-metrics` endpoint
- **#TODO-3**: Add top-bar real-time metrics UI
- **#TODO-4**: Deployment capacity planning tool

---

## Open Questions

1. Should we discover models automatically on startup, or only on-demand?
   - Pro (auto): Complete picture at startup
   - Pro (demand): Faster startup for single-model deployments

2. How to handle remote models (OpenRouter, OpenAI)?
   - Parse model cards from docs?
   - Ask user to input specs?
   - Estimate from parameter count?

3. GPU detection: nvidia-smi only, or also support AMD/Intel?
   - MVP: nvidia-smi only
   - Future: Add rocm-smi, Intel GPU Detection

4. Alert thresholds: What's "too high"?
   - CPU >90%? >80%?
   - GPU >85%? >90%?
   - RAM >90%? >95%?
