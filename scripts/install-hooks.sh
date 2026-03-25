#!/bin/sh
# Install git hooks for supply chain security automation.
#
# pre-commit: regenerates requirements.lock when requirements.txt is staged
# pre-push:   runs pip-audit CVE scan before any push
#
# Usage: sh scripts/install-hooks.sh

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

install_hook() {
    NAME="$1"
    SRC="$REPO_ROOT/scripts/hooks/$NAME"
    DEST="$HOOKS_DIR/$NAME"

    if [ ! -f "$SRC" ]; then
        echo "ERROR: $SRC not found" >&2
        exit 1
    fi

    cp "$SRC" "$DEST"
    chmod +x "$DEST"
    echo "Installed $NAME"
}

mkdir -p "$REPO_ROOT/scripts/hooks"

# Write hooks into scripts/hooks/ (committed) then copy to .git/hooks/
cat > "$REPO_ROOT/scripts/hooks/pre-commit" << 'EOF'
#!/bin/sh
if git diff --cached --name-only | grep -q "^requirements\.txt$"; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "pre-commit: requirements.txt changed but 'uv' is not installed." >&2
        echo "Install with: pip install uv" >&2
        exit 1
    fi
    echo "pre-commit: requirements.txt changed — regenerating requirements.lock..."
    uv pip compile requirements.txt --generate-hashes --output-file requirements.lock --quiet
    if [ $? -ne 0 ]; then
        echo "pre-commit: uv pip compile failed." >&2
        exit 1
    fi
    git add requirements.lock
    echo "pre-commit: requirements.lock updated and staged."
fi
EOF

cat > "$REPO_ROOT/scripts/hooks/pre-push" << 'EOF'
#!/bin/sh
if ! command -v pip-audit >/dev/null 2>&1; then
    echo "pre-push: pip-audit not found — skipping CVE scan. Install with: pip install pip-audit" >&2
    exit 0
fi
if [ ! -f requirements.lock ]; then
    echo "pre-push: requirements.lock not found — skipping." >&2
    exit 0
fi
echo "pre-push: scanning requirements.lock for known CVEs..."
if pip-audit -r requirements.lock --progress-spinner off 2>&1; then
    echo "pre-push: no known CVEs found."
    exit 0
fi
echo ""
echo "pre-push: CVEs found. Update affected packages or push with --no-verify to bypass." >&2
exit 1
EOF

chmod +x "$REPO_ROOT/scripts/hooks/pre-commit" "$REPO_ROOT/scripts/hooks/pre-push"

install_hook pre-commit
install_hook pre-push

echo ""
echo "Done. Hooks installed:"
echo "  pre-commit  — auto-regenerates requirements.lock on requirements.txt changes"
echo "  pre-push    — blocks push if known CVEs exist in requirements.lock"
