#!/bin/bash
# compose-switch.sh — Switch between dev and prod Docker Compose setups
# Usage: ./compose-switch.sh [dev|prod|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_COMPOSE="docker-compose.yaml"
PROD_COMPOSE="docker-compose.prod.yaml"
DEV_LINK="compose-dev.yaml"
PROD_LINK="compose-prod.yaml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() {
    if [ -L "$SCRIPT_DIR/docker-compose.yaml" ]; then
        TARGET=$(readlink "$SCRIPT_DIR/docker-compose.yaml")
        if [[ "$TARGET" == *"$PROD_COMPOSE"* ]]; then
            echo -e "${GREEN}Current mode: PRODUCTION${NC}"
            echo "  Using: $PROD_COMPOSE"
            echo "  Config: .env (required)"
        else
            echo -e "${YELLOW}Current mode: DEVELOPMENT${NC}"
            echo "  Using: $DEV_COMPOSE"
            echo "  Config: env.example"
        fi
    else
        echo -e "${RED}No active compose setup${NC}"
    fi
}

switch_to_dev() {
    cd "$SCRIPT_DIR"

    if [ ! -f "$DEV_COMPOSE" ]; then
        echo -e "${RED}Error: $DEV_COMPOSE not found${NC}"
        exit 1
    fi

    # Remove existing link or file
    if [ -e docker-compose.yaml ] || [ -L docker-compose.yaml ]; then
        rm -f docker-compose.yaml
    fi

    # Create symlink
    ln -s "$DEV_COMPOSE" docker-compose.yaml

    # Remove prod-specific .env if it exists
    [ -f .env.prod ] && mv .env.prod .env.prod.backup

    echo -e "${GREEN}✓ Switched to DEVELOPMENT mode${NC}"
    echo "  Command: docker compose up -d"
    echo "  UI: http://localhost:1337"
}

switch_to_prod() {
    cd "$SCRIPT_DIR"

    if [ ! -f "$PROD_COMPOSE" ]; then
        echo -e "${RED}Error: $PROD_COMPOSE not found${NC}"
        exit 1
    fi

    # Check for .env
    if [ ! -f .env ]; then
        if [ -f .env.prod.example ]; then
            echo -e "${YELLOW}No .env found. Creating from .env.prod.example${NC}"
            cp .env.prod.example .env
            echo -e "${YELLOW}⚠ Edit .env with your paths and API keys before starting${NC}"
        else
            echo -e "${RED}Error: .env.prod.example not found${NC}"
            exit 1
        fi
    fi

    # Remove existing link or file
    if [ -e docker-compose.yaml ] || [ -L docker-compose.yaml ]; then
        rm -f docker-compose.yaml
    fi

    # Create symlink
    ln -s "$PROD_COMPOSE" docker-compose.yaml

    echo -e "${GREEN}✓ Switched to PRODUCTION mode${NC}"
    echo "  Command: docker compose -f docker-compose.prod.yaml up -d"
    echo "  Config: .env (edit before starting)"
    echo ""
    echo "  IMPORTANT:"
    echo "  1. Verify .env paths point to persistent storage"
    echo "  2. Set API keys: GOOGLE_API_KEY, OPENROUTER_API_KEY, BB_MASTER_KEY"
    echo "  3. Run: docker compose up -d"
}

show_help() {
    cat << EOF
compose-switch.sh — Switch between dev and production Docker Compose setups

USAGE:
    ./compose-switch.sh [command]

COMMANDS:
    dev         Switch to development (local build, flexible paths)
    prod        Switch to production (image pull, persistent volumes, auto-restart)
    status      Show current active compose setup
    help        Show this help message

EXAMPLES:
    ./compose-switch.sh dev              # Use development setup
    ./compose-switch.sh prod             # Use production setup
    docker compose up -d                 # Start with current setup

DIFFERENCES:
    Dev:  Local build, host mounts, env.example
    Prod: Image pull, named volumes, .env config, auto-restart, log rotation

EOF
}

case "${1:-status}" in
    dev)
        switch_to_dev
        ;;
    prod)
        switch_to_prod
        ;;
    status)
        print_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo "Run: ./compose-switch.sh help"
        exit 1
        ;;
esac
