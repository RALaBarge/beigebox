#!/bin/bash
# Sync commits from beigebox-live to shipping folder using git
# Usage: ./docker/sync-to-shipping.sh [--push]

set -e

LIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHIPPING_DIR="$(cd "$LIVE_DIR/../ai-stack/beigebox" && pwd)"
PATCH_DIR="/tmp/beigebox-patches"
PUSH=${1:-}

echo "🔄 Syncing commits from live → shipping..."
echo ""

# Create temp patch directory
rm -rf "$PATCH_DIR"
mkdir -p "$PATCH_DIR"

# Export commits from live that differ from shipping main
cd "$LIVE_DIR"
git fetch origin main 2>/dev/null || true
COMMITS=$(git log origin/main..HEAD --pretty=format:"%H%n" | wc -l)

if [ "$COMMITS" -eq 0 ]; then
  echo "✓ Live is in sync with origin/main, no new commits to sync"
  exit 0
fi

echo "Found $COMMITS new commit(s) to sync"
echo ""

# Export as patches
git format-patch origin/main -o "$PATCH_DIR" > /dev/null
echo "✓ Exported patches to $PATCH_DIR"
echo ""

# Apply to shipping
cd "$SHIPPING_DIR"
echo "Applying patches to shipping..."
for patch in "$PATCH_DIR"/*.patch; do
  if [ -f "$patch" ]; then
    echo "  📎 $(basename "$patch")"
    git am "$patch" || {
      echo "❌ Patch failed. Aborting."
      git am --abort
      exit 1
    }
  fi
done

echo ""
echo "✓ All patches applied"
echo ""

# Push if requested
if [ "$PUSH" = "--push" ]; then
  echo "🚀 Pushing to origin/main..."
  git push origin main
  echo "✓ Pushed"
else
  echo "Ready to push:"
  echo "  cd $SHIPPING_DIR"
  echo "  git push origin main"
fi

echo ""
echo "Done!"
