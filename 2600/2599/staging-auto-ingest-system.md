# ✅ COMPLETE — Implemented. Drop markdown in 2600/2600-staging/, server auto-chunks (1200 chars, 150 overlap), embeds via nomic-embed-text, archives originals, updates .upload-manifest.json on startup. Integrated with operator document_search tool.

# Staging & Auto-Ingest System

Automatic document indexing workflow for BeigeBox design docs.

## Overview

When you start BeigeBox (`beigebox dial`), it automatically:
1. Scans `2600/2600-staging/` for markdown files
2. Chunks each document (1200 chars, 150 char overlap)
3. Embeds into ChromaDB vector store
4. Moves successfully indexed files to `2600/2599/` (archive)
5. Updates `.upload-manifest.json` with upload metadata

**Fire-and-forget**: Server starts immediately while indexing happens in background.

---

## Workflow

### Add a new design doc

```bash
# 1. Write your doc
cat > my-feature-design.md << 'EOF'
# Feature Design: Awesome Thing

## Overview
Description of the feature...

## Architecture
How it works...

## Implementation
Key components...
EOF

# 2. Move to staging
mv my-feature-design.md beigebox/2600/2600-staging/

# 3. Start server (indexing happens automatically)
cd beigebox && beigebox dial
```

### What happens behind the scenes

```
2600/2600-staging/my-feature-design.md
  ↓
[Read file, validate UTF-8]
  ↓
[Chunk: 1200 chars, 150 char overlap]
  ↓
[Embed chunks via nomic-embed-text]
  ↓
[Store in ChromaDB with metadata]
  ↓
[Move to 2600/2599/my-feature-design.md]
  ↓
[Update .upload-manifest.json]
  ↓
[Log: "Indexed and archived: my-feature-design.md"]
```

---

## Manifest Structure

**File**: `2600/.upload-manifest.json`

**Format**:
```json
{
  "version": "1.0",
  "description": "Upload manifest for 2600/ document indexing",
  "last_sync": "2026-03-21T12:30:45.123456Z",
  "files": {
    "my-feature-design.md": {
      "uploaded_at": "2026-03-21T12:30:45.123456Z",
      "md5": "a1b2c3d4e5f6...",
      "status": "uploaded"
    },
    "broken-doc.md": {
      "uploaded_at": "2026-03-21T12:25:30.000000Z",
      "md5": "",
      "status": "failed",
      "error": "Encoding error: invalid UTF-8 in line 42"
    }
  }
}
```

**Fields**:

| Field | Meaning |
|-------|---------|
| `version` | Schema version (for compatibility) |
| `last_sync` | ISO 8601 timestamp of last ingest run |
| `files[name].uploaded_at` | When file was successfully indexed |
| `files[name].md5` | MD5 hash of file content (detect changes) |
| `files[name].status` | `uploaded` or `failed` |
| `files[name].error` | Error message if status=failed |

---

## Directories

| Directory | Purpose |
|-----------|---------|
| `2600/` | Working copy — active design docs |
| `2600/2600-staging/` | **Staging folder** — drop new docs here |
| `2600/2599/` | Archive — indexed docs moved here |
| `2600/.upload-manifest.json` | Tracking file (committed to Git) |

### Why three folders?

- **`2600/`**: Quick reference for writers; documents you're actively working on
- **`2600-staging/`**: Intent to publish; docs ready to ingest and archive
- **`2599/`**: Cold storage; completed/old docs (keeps `2600/` focused)

---

## Usage Examples

### Example 1: Add a new design doc

```bash
# Write doc locally
cat > feature-x-design.md << 'EOF'
# Feature X Design

## Motivation
We need to handle concurrent requests better.

## Solution
Implement request queuing with priority levels.

## Implementation
1. Create RequestQueue class
2. Add priority scoring
3. Update proxy to use queue
EOF

# Move to staging
mv feature-x-design.md beigebox/2600/2600-staging/

# Start server — it auto-indexes
cd beigebox && beigebox dial

# Check manifest
cat 2600/.upload-manifest.json | jq '.files."feature-x-design.md"'
# Output:
# {
#   "uploaded_at": "2026-03-21T12:30:45.123456Z",
#   "md5": "a1b2c3d4...",
#   "status": "uploaded"
# }
```

### Example 2: Batch ingest multiple docs

```bash
# Add several docs to staging
cp architectural-decision-*.md beigebox/2600/2600-staging/

# Start server once
beigebox dial

# All docs ingest automatically, manifest updates with each
cat 2600/.upload-manifest.json | jq '.files | keys'
# ["architectural-decision-001.md", "architectural-decision-002.md", ...]
```

### Example 3: Fix and retry a failed doc

```bash
# Check what failed
cat 2600/.upload-manifest.json | jq '.files[] | select(.status == "failed")'

# Output:
# {
#   "uploaded_at": "2026-03-21T12:25:30.000000Z",
#   "md5": "",
#   "status": "failed",
#   "error": "Encoding error: invalid UTF-8 in line 42"
# }

# Fix the doc (e.g., save as UTF-8 without BOM)
vim broken-doc.md

# Move back to staging
mv 2600/2600-staging/broken-doc.md .
mv broken-doc.md beigebox/2600/2600-staging/

# Restart server — retries the doc
beigebox dial

# Verify success
cat 2600/.upload-manifest.json | jq '.files."broken-doc.md"'
# Should now show status: "uploaded"
```

---

## Integration with Operator

Once indexed, documents are available to the operator via the `document_search` tool:

```
Operator: "What's the design for request queuing?"

[Uses document_search to find relevant chunks]

Found in: feature-x-design.md
  - Chunks with keywords: queue, priority, concurrent
  - Returns top 3 most relevant snippets

Operator: "Based on the design, here's my implementation plan..."
```

---

## Chunking Details

**Parameters** (from `beigebox/storage/chunker.py`):

| Param | Value | Why |
|-------|-------|-----|
| Chunk size | 1200 chars | ~200-300 tokens; balances context with specificity |
| Overlap | 150 chars | Ensures sentences spanning chunk boundaries stay retrievable |
| Strategy | Paragraph-aware | Splits at `\n\n` when possible (natural boundaries) |
| Fallback | Hard split | If a paragraph > 1200 chars, split at char boundary |

**Example**:

```
Input (400 chars):
"# Section 1

This is paragraph A (200 chars)...

This is paragraph B (150 chars)...

# Section 2

This is paragraph C (250 chars)..."

Chunks:
1. "# Section 1\n\nThis is paragraph A (200 chars)..."
2. "[150 char overlap]...This is paragraph B (150 chars)...\n\n# Section 2"
3. "[150 char overlap]...\nThis is paragraph C (250 chars)..."
```

---

## Logging & Debugging

Server logs auto-ingest activity:

```
[INFO] Found 3 staged document(s) in 2600-staging — indexing…
[INFO] Indexed and archived: feature-x-design.md
[INFO] Indexed and archived: api-contract.md
[ERROR] Failed to ingest: broken-doc.md: Encoding error: invalid UTF-8 in line 42
[INFO] Staging ingest complete — manifest updated
```

To see detailed logs:

```bash
# Live tail
tail -f logs/beigebox.log | grep -i staging

# Or check config
cat logs/beigebox.log | jq 'select(.msg | contains("staging"))'
```

---

## Manifest Audit Trail

Because `.upload-manifest.json` is committed to Git, you have a complete history:

```bash
# See all uploads
git log --follow -p 2600/.upload-manifest.json

# See when a specific doc was added
git log -S '"my-doc.md"' -- 2600/.upload-manifest.json

# Diff between two commits
git show COMMIT1:2600/.upload-manifest.json > /tmp/before.json
git show COMMIT2:2600/.upload-manifest.json > /tmp/after.json
diff /tmp/before.json /tmp/after.json
```

---

## Limitations & Edge Cases

### Encoding

- Only UTF-8 files supported
- Files with BOM (Byte Order Mark) will fail
- Fix: Save as UTF-8 without BOM (most editors have this option)

### File size

- Very large files (>10MB) are chunked but may take longer
- Manifest stores one entry per file (not per chunk)
- Chunks are stored in ChromaDB with metadata

### Conflicts

- If a file exists in both `2600/` and `2600/2600-staging/`, the staging version wins
- Manifest tracks only files that passed indexing

### Retries

- Failed files stay in staging (not moved to archive)
- Fix the file and restart server to retry
- Manifest updates with new timestamp on success

---

## Future Enhancements

Possible additions (not yet implemented):

1. **Scheduled ingest**: Check staging folder every N minutes (not just at startup)
2. **Bulk CLI command**: `beigebox upload-docs 2600/2600-staging/` (manual trigger)
3. **Change detection**: Skip re-indexing if md5 unchanged
4. **Compression**: Archive old manifests (currently grows with every ingest)
5. **Webhook notifications**: Post to Slack/Discord when docs indexed
6. **Versioning**: Keep old versions of docs with timestamps

---

## See Also

- [Request Pipeline Complete](request-pipeline-complete.md) — Full proxy request flow
- [Output Normalizer WASM](output-normalizer-wasm.md) — Response formatting (different system)
- [ChromaDB](https://docs.trychroma.dev/) — Vector store used for chunking/embedding
