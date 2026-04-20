#!/bin/bash
# Sync code from beigebox-live to shipping folder
# Uses git to copy only tracked files, respects .gitignore (skips configs, data, personal stuff)

set -e

LIVE_DIR="/home/jinx/beigebox-live"
SHIPPING_DIR="../ai-stack/beigebox"

echo "📦 Syncing code from live → shipping..."
echo ""

cd "$LIVE_DIR"

# Get tracked files (respects .gitignore — sensitive data is skipped automatically)
git ls-files | while read file; do
  # Skip personal/testing directories
  if [[ "$file" == 2600/* ]] || [[ "$file" == amf/* ]] || [[ "$file" == data/* ]]; then
    continue
  fi

  src="$LIVE_DIR/$file"
  dst="$SHIPPING_DIR/$file"

  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
done

echo "✓ Files synced (sensitive data automatically excluded via .gitignore)"
echo ""
echo "Next: review & commit in shipping"
echo "  cd $SHIPPING_DIR"
echo "  git status"
echo "  git add -A && git commit -m '<msg>' && git push"
