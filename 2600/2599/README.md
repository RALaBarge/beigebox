# 2600-Staging Directory

This is the intermediate staging area for documents pending bulk upload to production.

## Workflow

1. **New documents** are added to the parent `2600/` folder
2. **Ready for upload?** Move docs to `2600-staging/`
3. **Bulk upload:** Run `beigebox upload-docs 2600-staging/`
4. **Upload tracked** in `../.upload-manifest.json` (parent directory)
5. **Archive:** Move uploaded docs to `../2599/` if desired

## Example

```bash
# Add a new doc
cp my-design-doc.md ../

# Index it locally
beigebox index-docs ../ 

# Ready to upload to production?
cp ../my-design-doc.md .

# Bulk sync to production
beigebox upload-docs .

# Check manifest
cat ../.upload-manifest.json | jq '.files."my-design-doc.md"'

# Archive if done
mv ../my-design-doc.md ../2599/
```

## Status Tracking

The manifest (`../.upload-manifest.json`) tracks:
- `uploaded_at` — ISO 8601 timestamp
- `md5` — file hash to detect changes
- `status` — "pending", "uploaded", or "failed"

Failed uploads can be retried: `grep '"failed"' ../.upload-manifest.json`

## Notes

- Staging is **not** in Git (see `.gitignore`)
- Manifest **is** in Git for audit trail
- Allows incremental uploads without reprocessing entire 2600/
- Failed uploads remain in staging with `"status": "failed"` for retry
