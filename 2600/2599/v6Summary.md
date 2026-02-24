# V0.6.0 Implementation Ready ✓

## All Files Ready in Outputs

```
/mnt/user-data/outputs/
├── config_v0.6.yaml              (All v0.6 features, disabled by default)
├── README_UPDATED.md             (Updated with v0.6 roadmap + features)
├── sqlite_store.py               (Schema with cost_usd + custom fields)
├── cleanup-redundant.sh          (Script to clean up old files)
├── 2600/                         (Design docs)
│   ├── orchestrator-design.md
│   ├── multi-backend-design.md
│   ├── flight-recorder-design.md
│   ├── conversation-replay-design.md
│   ├── semantic-map-design.md
│   └── cost-tracking-design.md
└── [other docs from previous work]
```

---

## ONE-LINER Bash Command to Remove Redundant Files

Copy & paste this into your terminal to remove old analysis/planning docs from your local beigebox folder:

```bash
cd /path/to/beigebox && rm -f BEIGEBOX_ANALYSIS.md CLARIFYING_QUESTIONS.md DETAILED_TECHNICAL_ANSWERS.md EXECUTION_PLAN.md IMPLEMENTATION_SPEC_LOCKED.md MODEL_ADVERTISING.md MODEL_ADVERTISING_IMPLEMENTATION.md OPERATOR_GUIDE.md TODO_ANALYSIS.md WEB_UI_AND_API.md README_OLD.md && rm -rf docs/2600 && echo "✓ Cleanup complete"
```

**What it removes**:
- All old analysis documents (*.md files from planning phase)
- Old `docs/2600` directory
- Keeps `README.md` (current), keeps `2600/` (new)

**Dry run first** (see what would be deleted):
```bash
cd /path/to/beigebox && ls -la BEIGEBOX_ANALYSIS.md CLARIFYING_QUESTIONS.md DETAILED_TECHNICAL_ANSWERS.md EXECUTION_PLAN.md IMPLEMENTATION_SPEC_LOCKED.md MODEL_ADVERTISING.md MODEL_ADVERTISING_IMPLEMENTATION.md OPERATOR_GUIDE.md TODO_ANALYSIS.md WEB_UI_AND_API.md README_OLD.md 2>/dev/null; ls -la docs/2600 2>/dev/null && echo "Files above would be deleted"
```

---

## Integration Steps for You

### Step 1: Update Your Local Files

```bash
# Copy config template
cp config_v0.6.yaml /path/to/beigebox/config.yaml

# Copy updated schema
cp sqlite_store.py /path/to/beigebox/beigebox/storage/

# Copy design docs
mkdir -p /path/to/beigebox/2600
cp -r 2600/* /path/to/beigebox/2600/

# Copy updated README
cp README_UPDATED.md /path/to/beigebox/README.md
```

### Step 2: Clean Up Old Files

```bash
cd /path/to/beigebox && rm -f BEIGEBOX_ANALYSIS.md CLARIFYING_QUESTIONS.md DETAILED_TECHNICAL_ANSWERS.md EXECUTION_PLAN.md IMPLEMENTATION_SPEC_LOCKED.md MODEL_ADVERTISING.md MODEL_ADVERTISING_IMPLEMENTATION.md OPERATOR_GUIDE.md TODO_ANALYSIS.md WEB_UI_AND_API.md README_OLD.md && rm -rf docs/2600 && echo "✓ Cleanup complete"
```

### Step 3: Ready to Implement

- Config template ready (all features disabled by default)
- Design docs ready (one per feature, detailed specs)
- Schema updated (cost_usd + custom fields)
- README updated (v0.6 roadmap + architecture)

---

## What's Locked In

### v0.6.0 Features (Disabled by Default)

1. **Orchestrator** — Parallel LLM spawning for operator agent
   - Max 5 parallel tasks
   - Per-task timeout: 120s
   - Total timeout: 300s

2. **Multi-Backend Router** — Local Ollama + OpenRouter fallback
   - Priority-based cascading
   - Per-backend timeout
   - Cost tracking for API calls

3. **Cost Tracking** — OpenRouter costs only (local = $0)
   - Stored in messages table (`cost_usd` column)
   - Query by model, day, conversation
   - Stats endpoint: `/api/v1/costs?days=30`

4. **Flight Recorder** — Request lifecycle timelines
   - In-memory cache (max 1000 records, 24hr retention)
   - Detailed milestones + elapsed times
   - Endpoint: `/api/v1/flight-recorder/{request_id}`

5. **Conversation Replay** — Full reconstruction with decisions
   - Shows routing method, confidence, tools
   - Query from SQLite + wiretap logs
   - Endpoint: `/api/v1/conversation/{conv_id}/replay`

6. **Semantic Map** — Topic clustering & visualization
   - Graph of topics + similarity edges
   - Community detection for clusters
   - Endpoint: `/api/v1/conversation/{conv_id}/semantic-map`

### SQL Schema Updates

```sql
ALTER TABLE messages ADD COLUMN cost_usd REAL DEFAULT NULL;
ALTER TABLE messages ADD COLUMN custom_field_1 TEXT DEFAULT NULL;
ALTER TABLE messages ADD COLUMN custom_field_2 TEXT DEFAULT NULL;
```

- **cost_usd**: NULL for local, numeric for OpenRouter
- **custom_field_1**: Temp usage (text, flexible)
- **custom_field_2**: Temp usage (text, flexible)

All existing data unaffected (NULL values). Zero downtime.

### Configuration Structure

```yaml
backends_enabled: false
backends:
  - name: "local"          # Ollama
  - name: "openrouter"     # API

cost_tracking:
  enabled: false
  track_openrouter: true
  track_local: false

orchestrator:
  enabled: false
  max_parallel_tasks: 5
  task_timeout_seconds: 120
  total_timeout_seconds: 300

flight_recorder:
  enabled: false
  retention_hours: 24
  max_records: 1000

conversation_replay:
  enabled: false

semantic_map:
  enabled: false
  similarity_threshold: 0.5
  max_topics: 50
```

---

## Next Steps (You)

1. ✓ Download all files from outputs/
2. Copy to your local beigebox directory
3. Run cleanup one-liner
4. Implement the 6 features (in any order)
5. Run tests at the end of sprint
6. Review todo.md for blockers

---

## Design Doc Quick Reference

Each design doc in `2600/` includes:

- **Problem Statement** — why feature needed
- **Design Decisions** — how it works
- **Implementation** — code structure + integration points
- **API Endpoints** — what endpoints expose feature
- **Configuration** — yaml options
- **Testing Checklist** — what to test
- **Future Enhancements** — ideas for v0.7+

---

## Important Notes

### Testing
- Tests added at **end of sprint** (after all features working)
- Test scaffolding provided in each design doc
- Full suite run before v0.6 release

### Deployment
- All features **disabled by default**
- Users opt-in via config
- Zero impact on existing installations
- Graceful degradation if any feature fails

### Database
- **No migration needed**
- New columns default to NULL
- Existing data unaffected
- Can add columns anytime (future-proof)

### Code Organization
- Each feature in separate module (orchestrator/, backends/, recorder/, etc.)
- Proxy.py will be split into proxy/ subdirectory
- CLI and Operator remain standalone
- Minimal coupling, maximum extensibility

---

## SQL Custom Fields - Your Notes

**custom_field_1** and **custom_field_2** are reserved for temporary usage. Leave blank for now, we can discuss what they're for after v0.6 implementation.

When you know what you want to store:
```sql
-- Update in future
-- custom_field_1: e.g., user_tag, routing_note, custom_metadata
-- custom_field_2: e.g., cost_breakdown, tool_name, custom_flag
```

---

**All ready for implementation. Good luck! 🚀**

