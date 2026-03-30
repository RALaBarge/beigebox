#!/usr/bin/env bash
# BeigeBox security scan — runs all scanners and reports results.
# Usage: ./scripts/security-scan.sh [--fix] [--quick]
#   --fix    auto-fix what bandit/semgrep can
#   --quick  skip trivy (requires Docker) and gitleaks (requires install)

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

FIX=0
QUICK=0
FAILURES=0

for arg in "$@"; do
    case "$arg" in
        --fix)   FIX=1 ;;
        --quick) QUICK=1 ;;
    esac
done

banner() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}\n"; }

# ── 1. pip-audit: dependency CVEs ─────────────────────────────────────────
banner "pip-audit — dependency vulnerabilities"
if command -v pip-audit &>/dev/null; then
    if pip-audit --strict --desc 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        echo -e "${RED}FAIL${NC} — vulnerabilities found in dependencies"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}SKIP${NC} — pip-audit not installed (pip install pip-audit)"
fi

# ── 2. bandit: static security analysis ──────────────────────────────────
banner "bandit — static security analysis"
if command -v bandit &>/dev/null; then
    BANDIT_ARGS=(-r beigebox -c .bandit -ll --format txt)
    if bandit "${BANDIT_ARGS[@]}" 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        echo -e "${RED}FAIL${NC} — security issues found in source code"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}SKIP${NC} — bandit not installed (pip install bandit)"
fi

# ── 3. semgrep: advanced static analysis ─────────────────────────────────
banner "semgrep — advanced pattern matching"
if command -v semgrep &>/dev/null; then
    SEMGREP_ARGS=(--config auto --exclude='.venv' --exclude='2600' --exclude='.claude' --exclude='tests' beigebox/)
    if [ "$FIX" -eq 1 ]; then
        SEMGREP_ARGS+=(--autofix)
    fi
    if semgrep "${SEMGREP_ARGS[@]}" 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        echo -e "${RED}FAIL${NC} — semgrep findings"
        ((FAILURES++))
    fi
else
    echo -e "${YELLOW}SKIP${NC} — semgrep not installed (pip install semgrep)"
fi

# ── 4. gitleaks: secrets in git history ──────────────────────────────────
if [ "$QUICK" -eq 0 ]; then
    banner "gitleaks — secrets detection"
    if command -v gitleaks &>/dev/null; then
        if gitleaks detect --config .gitleaks.toml --no-banner 2>&1; then
            echo -e "${GREEN}PASS${NC}"
        else
            echo -e "${RED}FAIL${NC} — potential secrets found in git history"
            ((FAILURES++))
        fi
    else
        echo -e "${YELLOW}SKIP${NC} — gitleaks not installed (https://github.com/gitleaks/gitleaks#installing)"
    fi
fi

# ── 5. trivy: container image scan ───────────────────────────────────────
if [ "$QUICK" -eq 0 ]; then
    banner "trivy — container image vulnerabilities"
    if command -v trivy &>/dev/null; then
        IMAGE="beigebox:latest"
        if docker image inspect "$IMAGE" &>/dev/null; then
            if trivy image --config trivy.yaml "$IMAGE" 2>&1; then
                echo -e "${GREEN}PASS${NC}"
            else
                echo -e "${RED}FAIL${NC} — container vulnerabilities found"
                ((FAILURES++))
            fi
        else
            echo -e "${YELLOW}SKIP${NC} — image '$IMAGE' not found (build with: docker compose -f docker/docker-compose.yml build)"
        fi
    else
        echo -e "${YELLOW}SKIP${NC} — trivy not installed (https://aquasecurity.github.io/trivy/latest/getting-started/installation/)"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────
banner "Summary"
if [ "$FAILURES" -eq 0 ]; then
    echo -e "${GREEN}All scans passed (or were skipped).${NC}"
    exit 0
else
    echo -e "${RED}${FAILURES} scanner(s) reported findings.${NC}"
    exit 1
fi
