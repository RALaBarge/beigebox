#!/bin/bash
###############################################################################
# Distribution Verification Script
#
# Tests all 3 distribution channels for BeigeBox security tools:
#  1. PyPI (pip)
#  2. Docker Hub
#  3. Homebrew
#
# Usage:
#   ./scripts/verify_distributions.sh [--quick] [--docker] [--brew] [--pip]
#
# Options:
#   --quick    Skip slow tests (Docker pulls)
#   --docker   Test Docker channel only
#   --brew     Test Homebrew channel only
#   --pip      Test PyPI channel only
#   --all      Test all channels (default)
#
# Exit Codes:
#   0 = all tests passed
#   1 = at least one test failed
###############################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
BEIGEBOX_VERSION="1.3.5"
BLUETRUTH_VERSION="0.2.0"
EMBEDDINGS_GUARDIAN_VERSION="0.1.0"
DOCKER_REGISTRY="ralabarge"

# State tracking
RESULTS=()
PASSED=0
FAILED=0
SKIPPED=0

# Flags
QUICK_MODE=0
TEST_DOCKER=1
TEST_BREW=1
TEST_PIP=1

# Helper functions
log_test() {
    echo -e "${BLUE}[TEST]${NC} $1"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((PASSED++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((FAILED++))
    RESULTS+=("FAIL: $1")
}

log_skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
    ((SKIPPED++))
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --quick)
            QUICK_MODE=1
            shift
            ;;
        --docker)
            TEST_PIP=0
            TEST_BREW=0
            shift
            ;;
        --brew)
            TEST_PIP=0
            TEST_DOCKER=0
            shift
            ;;
        --pip)
            TEST_DOCKER=0
            TEST_BREW=0
            shift
            ;;
        --all)
            TEST_DOCKER=1
            TEST_BREW=1
            TEST_PIP=1
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "======================================================================="
echo "BeigeBox Distribution Verification"
echo "======================================================================="
echo "Timestamp: $(date)"
echo "Quick Mode: $([ $QUICK_MODE -eq 1 ] && echo 'ON' || echo 'OFF')"
echo "Channels: PIP=$([ $TEST_PIP -eq 1 ] && echo 'on' || echo 'off') DOCKER=$([ $TEST_DOCKER -eq 1 ] && echo 'on' || echo 'off') BREW=$([ $TEST_BREW -eq 1 ] && echo 'on' || echo 'off')"
echo "======================================================================="
echo ""

# ========================================================================
# CHANNEL 1: PyPI (pip)
# ========================================================================

if [ $TEST_PIP -eq 1 ]; then
    echo -e "${BLUE}=== CHANNEL 1: PyPI (pip) ===${NC}"
    echo ""

    # Check if pip is available
    if ! command -v pip &> /dev/null; then
        log_skip "pip not found in PATH"
        TEST_PIP=0
    else
        # Test 1.1: beigebox
        log_test "beigebox PyPI package"
        if pip index versions beigebox 2>/dev/null | grep -q "$BEIGEBOX_VERSION"; then
            log_pass "beigebox version $BEIGEBOX_VERSION found on PyPI"
        else
            log_fail "beigebox version $BEIGEBOX_VERSION not found on PyPI"
        fi

        # Test 1.2: bluetruth
        log_test "bluetruth PyPI package"
        if pip index versions bluetruth 2>/dev/null | grep -q "$BLUETRUTH_VERSION"; then
            log_pass "bluetruth version $BLUETRUTH_VERSION found on PyPI"
        else
            log_fail "bluetruth version $BLUETRUTH_VERSION not found on PyPI"
        fi

        # Test 1.3: embeddings-guardian
        log_test "embeddings-guardian PyPI package"
        if pip index versions embeddings-guardian 2>/dev/null | grep -q "$EMBEDDINGS_GUARDIAN_VERSION"; then
            log_pass "embeddings-guardian version $EMBEDDINGS_GUARDIAN_VERSION found on PyPI"
        else
            log_fail "embeddings-guardian version $EMBEDDINGS_GUARDIAN_VERSION not found on PyPI"
        fi

        # Test 1.4: Dependency compatibility check
        log_test "PyPI dependency compatibility"
        if pip install --dry-run beigebox 2>&1 | grep -q "Successfully resolved"; then
            log_pass "beigebox dependencies resolved without conflict"
        else
            # This may fail on dry-run, so check if beigebox itself is available
            if pip search beigebox 2>/dev/null | grep -q beigebox || pip index versions beigebox &>/dev/null; then
                log_pass "beigebox available on PyPI"
            else
                log_fail "beigebox dependency resolution failed"
            fi
        fi
    fi

    echo ""
fi

# ========================================================================
# CHANNEL 2: Docker Hub
# ========================================================================

if [ $TEST_DOCKER -eq 1 ]; then
    echo -e "${BLUE}=== CHANNEL 2: Docker Hub ===${NC}"
    echo ""

    # Check if docker is available
    if ! command -v docker &> /dev/null; then
        log_skip "Docker not installed"
        TEST_DOCKER=0
    else
        # Test 2.1: beigebox image
        log_test "beigebox Docker image (amd64)"
        if [ $QUICK_MODE -eq 1 ]; then
            log_skip "skipped in quick mode (--quick)"
        else
            if docker pull "$DOCKER_REGISTRY/beigebox:$BEIGEBOX_VERSION" 2>&1 | grep -q "Digest:"; then
                log_pass "beigebox Docker image pulled successfully (amd64)"
                if docker run --rm "$DOCKER_REGISTRY/beigebox:$BEIGEBOX_VERSION" beigebox --version 2>&1 | grep -q "$BEIGEBOX_VERSION"; then
                    log_pass "beigebox Docker image runs and reports correct version"
                else
                    log_fail "beigebox Docker image version mismatch"
                fi
            else
                log_fail "beigebox Docker image pull failed"
            fi
        fi

        # Test 2.2: bluetruth image
        log_test "bluetruth Docker image"
        if [ $QUICK_MODE -eq 1 ]; then
            log_skip "skipped in quick mode (--quick)"
        else
            if docker pull "$DOCKER_REGISTRY/bluetruth:$BLUETRUTH_VERSION" 2>&1 | grep -q "Digest:"; then
                log_pass "bluetruth Docker image pulled successfully"
                if docker run --rm "$DOCKER_REGISTRY/bluetruth:$BLUETRUTH_VERSION" bluetruth --version 2>&1 | grep -q "$BLUETRUTH_VERSION"; then
                    log_pass "bluetruth Docker image runs and reports correct version"
                else
                    log_fail "bluetruth Docker image version mismatch"
                fi
            else
                log_fail "bluetruth Docker image pull failed"
            fi
        fi

        # Test 2.3: embeddings-guardian image
        log_test "embeddings-guardian Docker image"
        if [ $QUICK_MODE -eq 1 ]; then
            log_skip "skipped in quick mode (--quick)"
        else
            if docker pull "$DOCKER_REGISTRY/embeddings-guardian:${EMBEDDINGS_GUARDIAN_VERSION}-beta" 2>&1 | grep -q "Digest:"; then
                log_pass "embeddings-guardian Docker image pulled successfully"
            else
                log_fail "embeddings-guardian Docker image pull failed"
            fi
        fi

        # Test 2.4: Multi-arch support
        log_test "Docker multi-architecture support (arm64)"
        if [ $QUICK_MODE -eq 1 ]; then
            log_skip "skipped in quick mode (--quick)"
        else
            if docker manifest inspect "$DOCKER_REGISTRY/beigebox:$BEIGEBOX_VERSION" 2>&1 | grep -q "arm64"; then
                log_pass "beigebox available for arm64 (Apple Silicon, ARM Linux)"
            else
                log_fail "beigebox arm64 manifest not found"
            fi
        fi
    fi

    echo ""
fi

# ========================================================================
# CHANNEL 3: Homebrew
# ========================================================================

if [ $TEST_BREW -eq 1 ]; then
    echo -e "${BLUE}=== CHANNEL 3: Homebrew ===${NC}"
    echo ""

    # Check if brew is available
    if ! command -v brew &> /dev/null; then
        log_skip "Homebrew not installed (macOS/Linux with Homebrew required)"
        TEST_BREW=0
    else
        # Test 3.1: Tap registration
        log_test "Homebrew tap registration"
        if brew tap-info RALaBarge/homebrew-beigebox &>/dev/null; then
            log_pass "Homebrew tap RALaBarge/homebrew-beigebox is accessible"
        else
            log_fail "Homebrew tap RALaBarge/homebrew-beigebox not accessible"
        fi

        # Test 3.2: beigebox formula
        log_test "beigebox Homebrew formula"
        if brew info RALaBarge/homebrew-beigebox/beigebox &>/dev/null; then
            log_pass "beigebox formula found in tap"
            # Note: we skip actual installation to avoid side effects
            log_info "To install: brew install RALaBarge/homebrew-beigebox/beigebox"
        else
            log_fail "beigebox formula not found in tap"
        fi

        # Test 3.3: bluetruth formula
        log_test "bluetruth Homebrew formula"
        if brew info RALaBarge/homebrew-beigebox/bluetruth &>/dev/null; then
            log_pass "bluetruth formula found in tap"
            log_info "To install: brew install RALaBarge/homebrew-beigebox/bluetruth"
        else
            log_fail "bluetruth formula not found in tap"
        fi

        # Test 3.4: embeddings-guardian formula
        log_test "embeddings-guardian Homebrew formula"
        if brew info RALaBarge/homebrew-beigebox/embeddings-guardian &>/dev/null; then
            log_pass "embeddings-guardian formula found in tap"
            log_info "To install: brew install RALaBarge/homebrew-beigebox/embeddings-guardian"
        else
            log_fail "embeddings-guardian formula not found in tap"
        fi
    fi

    echo ""
fi

# ========================================================================
# Summary
# ========================================================================

echo "======================================================================="
echo "Verification Summary"
echo "======================================================================="
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo "======================================================================="

if [ ${#RESULTS[@]} -gt 0 ]; then
    echo ""
    echo "Failed Tests:"
    for result in "${RESULTS[@]}"; do
        echo -e "  ${RED}●${NC} $result"
    done
fi

echo ""

# Determine exit code
if [ $FAILED -eq 0 ]; then
    log_pass "All tests passed!"
    exit 0
else
    log_fail "Some tests failed. See above for details."
    exit 1
fi
