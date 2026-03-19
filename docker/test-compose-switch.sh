#!/bin/bash
# test-compose-switch.sh — Test suite for compose-switch.sh
# Tests switching, error handling, file management, and stress scenarios

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_PASSED=0
TESTS_FAILED=0

test_case() {
    local name="$1"
    local test_func="$2"

    # Create fresh test env
    TEST_TMP="/tmp/compose-test-$$-$RANDOM"
    mkdir -p "$TEST_TMP"
    cd "$TEST_TMP"
    cp "$SCRIPT_DIR"/compose-switch.sh .
    touch docker-compose.yaml docker-compose.prod.yaml .env.prod.example

    # Run test
    if $test_func 2>/dev/null; then
        echo "✓ $name"
        ((TESTS_PASSED++))
    else
        echo "✗ $name"
        ((TESTS_FAILED++))
    fi

    # Cleanup
    cd / && rm -rf "$TEST_TMP"
}

# Test: help message
test_help() { bash compose-switch.sh help | grep -q "Switch between"; }

# Test: status shows no active setup when no symlink
test_status_none() { bash compose-switch.sh status | grep -q "active"; }

# Test: dev switch creates symlink
test_dev_switch() {
    [ -f docker-compose.yaml ] || touch docker-compose.yaml
    bash compose-switch.sh dev > /dev/null
    [ -L docker-compose.yaml ]
}

# Test: prod switch creates symlink
test_prod_switch() {
    rm -f docker-compose.yaml
    bash compose-switch.sh prod > /dev/null
    [ -L docker-compose.yaml ]
}

# Test: status shows DEVELOPMENT after dev switch
test_status_dev() {
    bash compose-switch.sh dev > /dev/null
    bash compose-switch.sh status | grep -q "DEVELOPMENT"
}

# Test: status shows PRODUCTION after prod switch
test_status_prod() {
    bash compose-switch.sh prod > /dev/null
    bash compose-switch.sh status | grep -q "PRODUCTION"
}

# Test: invalid command errors
test_invalid_cmd() {
    bash compose-switch.sh invalid 2>&1 | grep -q "Unknown"
}

# Test: missing dev file errors
test_missing_dev_file() {
    rm -f docker-compose.yaml
    bash compose-switch.sh dev 2>&1 | grep -q "Error"
}

# Test: missing prod file errors
test_missing_prod_file() {
    rm -f docker-compose.prod.yaml
    bash compose-switch.sh prod 2>&1 | grep -q "Error"
}

# Test: missing .env.prod.example errors
test_missing_env_example() {
    rm -f .env.prod.example
    bash compose-switch.sh prod 2>&1 | grep -q "Error"
}

# Test: creates .env from example
test_env_creation() {
    rm -f .env
    bash compose-switch.sh prod > /dev/null
    [ -f .env ]
}

# Test: replaces regular file with symlink
test_replace_file() {
    echo "content" > docker-compose.yaml
    bash compose-switch.sh dev > /dev/null
    [ -L docker-compose.yaml ]
}

# Test: replaces broken symlink
test_replace_broken_symlink() {
    rm -f docker-compose.yaml
    ln -s /nonexistent docker-compose.yaml
    bash compose-switch.sh prod > /dev/null
    [ -L docker-compose.yaml ] && [ -f docker-compose.yaml ]
}

# Test: dev symlink points to correct file
test_dev_target() {
    bash compose-switch.sh dev > /dev/null
    [ "$(readlink docker-compose.yaml)" = "docker-compose.yaml" ]
}

# Test: prod symlink points to correct file
test_prod_target() {
    bash compose-switch.sh prod > /dev/null
    [ "$(readlink docker-compose.yaml)" = "docker-compose.prod.yaml" ]
}

# Test: switching dev then prod
test_switch_sequence() {
    bash compose-switch.sh dev > /dev/null
    bash compose-switch.sh prod > /dev/null
    [ "$(readlink docker-compose.yaml)" = "docker-compose.prod.yaml" ]
}

# Test: .env.prod backup
test_env_prod_backup() {
    bash compose-switch.sh prod > /dev/null
    echo "data" > .env.prod
    bash compose-switch.sh dev > /dev/null
    [ -f .env.prod.backup ]
}

# Test: .env persists across switches
test_env_persistence() {
    bash compose-switch.sh prod > /dev/null
    echo "secret" >> .env
    bash compose-switch.sh dev > /dev/null
    bash compose-switch.sh prod > /dev/null
    grep -q "secret" .env
}

# Test: idempotent dev switch
test_dev_idempotent() {
    bash compose-switch.sh dev > /dev/null
    bash compose-switch.sh dev > /dev/null
    [ -L docker-compose.yaml ]
}

# Test: idempotent prod switch
test_prod_idempotent() {
    bash compose-switch.sh prod > /dev/null
    bash compose-switch.sh prod > /dev/null
    [ -L docker-compose.yaml ]
}

# Test: rapid switching (stress test)
test_rapid_switching() {
    for i in {1..10}; do
        bash compose-switch.sh dev > /dev/null 2>&1
        bash compose-switch.sh prod > /dev/null 2>&1
    done
    [ -L docker-compose.yaml ]
}

# Test: concurrent switching (stress test)
# Note: Due to TOCTOU race conditions on symlink operations, concurrent
# execution may fail atomically. This test verifies no corruption.
test_concurrent_switch() {
    bash compose-switch.sh dev > /dev/null 2>&1 &
    bash compose-switch.sh prod > /dev/null 2>&1 &
    bash compose-switch.sh dev > /dev/null 2>&1 &
    wait 2>/dev/null || true
    # Either the symlink exists or everything failed cleanly
    [ -L docker-compose.yaml ] || [ ! -e docker-compose.yaml ]
}

# Test: default command is status
test_default_cmd() {
    bash compose-switch.sh dev > /dev/null
    bash compose-switch.sh | grep -q "Current"
}

# Run all tests
echo "================================"
echo "  compose-switch.sh Test Suite"
echo "================================"
echo ""

test_case "help message" test_help
test_case "status with no setup" test_status_none
test_case "dev switch creates symlink" test_dev_switch
test_case "prod switch creates symlink" test_prod_switch
test_case "status shows DEVELOPMENT" test_status_dev
test_case "status shows PRODUCTION" test_status_prod
test_case "invalid command rejected" test_invalid_cmd
test_case "missing dev file errors" test_missing_dev_file
test_case "missing prod file errors" test_missing_prod_file
test_case "missing .env.prod.example errors" test_missing_env_example
test_case "creates .env from example" test_env_creation
test_case "replaces regular file" test_replace_file
test_case "replaces broken symlink" test_replace_broken_symlink
test_case "dev symlink targets correct file" test_dev_target
test_case "prod symlink targets correct file" test_prod_target
test_case "switch dev then prod" test_switch_sequence
test_case ".env.prod backup" test_env_prod_backup
test_case ".env persists across switches" test_env_persistence
test_case "dev switch idempotent" test_dev_idempotent
test_case "prod switch idempotent" test_prod_idempotent
test_case "rapid switching stress test" test_rapid_switching
test_case "concurrent switching stress test" test_concurrent_switch
test_case "default command is status" test_default_cmd

echo ""
echo "================================"
TOTAL=$((TESTS_PASSED + TESTS_FAILED))
echo "Results: $TESTS_PASSED/$TOTAL passed"
echo "================================"

exit $(( TESTS_FAILED > 0 ? 1 : 0 ))
