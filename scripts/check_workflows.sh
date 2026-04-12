#!/bin/bash
#
# Check GitHub Actions workflow status across all BeigeBox ecosystem repos
# Usage: ./scripts/check_workflows.sh
# Shows: repo status and summary

# Configuration
REPOS=("beigebox" "bluTruth" "embeddings-guardian" "beigebox-security" "agentauth" "browserbox" "garlicpress" "pdf-oxide-wasi")
OWNER="RALaBarge"

# Print header
echo "=== BeigeBox CI/CD Workflow Status ==="
echo "Generated: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Track counters
total=0
passing=0
failing=0
in_progress=0
queued=0

# Check each repo
for repo in "${REPOS[@]}"; do
  # Fetch latest workflow run status
  result=$(gh run list --repo "$OWNER/$repo" --limit=1 --json status,conclusion --jq '.[] | .status + ":" + (.conclusion // "")' 2>/dev/null || echo "error")

  if [ "$result" = "error" ]; then
    echo "[?] $repo - Could not fetch status"
    ((total++))
    continue
  fi

  status="${result%%:*}"
  conclusion="${result##*:}"

  # Update counters and determine icon
  ((total++))
  if [ "$status" = "completed" ] && [ "$conclusion" = "success" ]; then
    ((passing++))
    icon="✓"
  elif [ "$status" = "completed" ] && [ "$conclusion" = "failure" ]; then
    ((failing++))
    icon="✗"
  elif [ "$status" = "in_progress" ]; then
    ((in_progress++))
    icon="→"
  elif [ "$status" = "queued" ]; then
    ((queued++))
    icon="⋯"
  else
    icon="?"
  fi

  # Print repo status
  printf "%s %s: %s\n" "$icon" "$repo" "$status:${conclusion:-pending}"
done

echo ""
echo "=== Summary ==="
echo "PASS:        $passing"
echo "FAIL:        $failing"
echo "IN_PROGRESS: $in_progress"
echo "QUEUED:      $queued"
echo "TOTAL:       $total"
echo ""

# Exit code based on results
if [ $failing -eq 0 ] && [ $in_progress -eq 0 ] && [ $queued -eq 0 ]; then
  echo "Status: All workflows passing!"
  exit 0
elif [ $failing -eq 0 ]; then
  echo "Status: Waiting for remaining workflows..."
  exit 1
else
  echo "Status: Some workflows failing. Run: gh run view <ID> --repo RALaBarge/<REPO> --log"
  exit 2
fi
