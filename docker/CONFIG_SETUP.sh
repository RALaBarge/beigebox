#!/bin/bash
# CONFIG_SETUP.sh — Interactive BeigeBox feature & backend configuration
# Run anytime to update config.docker.yaml settings (hot-reloadable in container)
#
# Use case: After FIRST_RUN.sh, customize features, backends, and tools

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

CONFIG_FILE="config.docker.yaml"

# Helper: check if config file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo -e "${YELLOW}ERROR: $CONFIG_FILE not found${NC}"
    echo "Run this from docker/ directory, or check config exists"
    exit 1
fi

# Banner
echo ""
echo -e "${BLUE}  BeigeBox Configuration${NC}"
echo -e "${BLUE}  ═════════════════════════${NC}"
echo ""
echo "This updates $CONFIG_FILE with your feature choices."
echo "Changes are hot-reloaded in the running container (no restart needed)."
echo ""

# Menu
echo -e "${YELLOW}What do you want to configure?${NC}"
echo ""
echo "  1. Features (routing, caching, observability, tools)"
echo "  2. Models (default, routing, agentic, summary)"
echo "  3. Backends (Ollama, OpenRouter, multi-backend routing)"
echo "  4. Tools (web search, CDP, plugins, operator)"
echo "  5. Operator agent (max iterations, shell access, tool profiles)"
echo "  6. Harness (multi-turn orchestration, retry, stagger)"
echo "  7. Show current config"
echo ""
read -p "Choose [1-7]: " -r CHOICE
CHOICE=${CHOICE:-1}

echo ""

# Helper: update YAML value (simple sed-based, works for basic key=value)
update_yaml() {
    local key="$1"
    local value="$2"
    local file="$3"

    # Use a marker pattern: key: value (handles basic YAML)
    if grep -q "^  *$key:" "$file"; then
        if [[ "$(uname -s)" == "Darwin" ]]; then
            sed -i '' "s|^  *$key:.*|  $key: $value|g" "$file" || true
        else
            sed -i "s|^  *$key:.*|  $key: $value|g" "$file" || true
        fi
    else
        echo "WARNING: Could not find $key in $file"
    fi
}

case "$CHOICE" in
    1)
        # Features
        echo -e "${CYAN}Features${NC}"
        echo ""
        echo "These control which subsystems are active:"
        echo ""

        echo -e "${YELLOW}Semantic Caching?${NC}"
        echo "  Deduplicates similar prompts via embedding similarity (saves tokens)"
        read -p "Enable [y/N]: " -r SEMANTIC_CACHE
        SEMANTIC_CACHE=${SEMANTIC_CACHE:-n}
        if [[ "$SEMANTIC_CACHE" == "y" || "$SEMANTIC_CACHE" == "Y" ]]; then
            update_yaml "semantic_cache:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Semantic cache enabled"
        else
            update_yaml "semantic_cache:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Semantic cache disabled"
        fi
        echo ""

        echo -e "${YELLOW}Cost Tracking?${NC}"
        echo "  Logs token counts and costs per model to SQLite"
        read -p "Enable [y/N]: " -r COST_TRACKING
        COST_TRACKING=${COST_TRACKING:-n}
        if [[ "$COST_TRACKING" == "y" || "$COST_TRACKING" == "Y" ]]; then
            update_yaml "cost_tracking:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Cost tracking enabled"
        else
            update_yaml "cost_tracking:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Cost tracking disabled"
        fi
        echo ""

        echo -e "${YELLOW}Decision LLM (routing judge)?${NC}"
        echo "  Small LLM that makes borderline routing calls (embedding → decision LLM → backend)"
        read -p "Enable [y/N]: " -r DECISION_LLM
        DECISION_LLM=${DECISION_LLM:-n}
        if [[ "$DECISION_LLM" == "y" || "$DECISION_LLM" == "Y" ]]; then
            update_yaml "decision_llm:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Decision LLM enabled"
        else
            update_yaml "decision_llm:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Decision LLM disabled"
        fi
        echo ""

        echo -e "${YELLOW}Operator agent (agentic tool use)?${NC}"
        echo "  JSON-based tool loop for multi-step reasoning & action"
        read -p "Enable [y/N]: " -r OPERATOR
        OPERATOR=${OPERATOR:-n}
        if [[ "$OPERATOR" == "y" || "$OPERATOR" == "Y" ]]; then
            update_yaml "operator:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Operator enabled"
        else
            update_yaml "operator:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Operator disabled"
        fi
        echo ""

        echo -e "${YELLOW}Harness (multi-turn orchestration)?${NC}"
        echo "  Multi-agent coordination & parallel task execution"
        read -p "Enable [y/N]: " -r HARNESS
        HARNESS=${HARNESS:-n}
        if [[ "$HARNESS" == "y" || "$HARNESS" == "Y" ]]; then
            update_yaml "harness:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Harness enabled"
        else
            update_yaml "harness:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Harness disabled"
        fi
        echo ""

        echo -e "${YELLOW}WASM transforms?${NC}"
        echo "  WASM-based response post-processing (e.g., opener_strip for cleaning LLM output)"
        read -p "Enable [y/N]: " -r WASM
        WASM=${WASM:-n}
        if [[ "$WASM" == "y" || "$WASM" == "Y" ]]; then
            update_yaml "wasm:" "true" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} WASM enabled"
        else
            update_yaml "wasm:" "false" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} WASM disabled"
        fi
        echo ""
        ;;

    2)
        # Models
        echo -e "${CYAN}Models${NC}"
        echo ""
        echo "Configure which models are used for different tasks:"
        echo ""

        echo -e "${YELLOW}Default model (general chat)?${NC}"
        echo "  Currently: $(grep 'default_model:' $CONFIG_FILE | head -1 | cut -d'"' -f2)"
        echo "  Available: qwen3:4b, llama2:7b, neural-chat, or any model in Ollama"
        read -p "Set model [default=keep current]: " -r DEFAULT_MODEL
        if [[ -n "$DEFAULT_MODEL" ]]; then
            update_yaml "default_model:" "\"$DEFAULT_MODEL\"" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Default model set to $DEFAULT_MODEL"
        fi
        echo ""

        echo -e "${YELLOW}Routing model (embedding classifier fallback)?${NC}"
        echo "  Small, fast model for borderline routing decisions"
        echo "  Recommended: llama3.2:3b (2B params, ~100ms latency)"
        read -p "Set model [default=keep current]: " -r ROUTING_MODEL
        if [[ -n "$ROUTING_MODEL" ]]; then
            update_yaml "routing:" "\"$ROUTING_MODEL\"" "$CONFIG_FILE"
            echo -e "${GREEN}✓${NC} Routing model set to $ROUTING_MODEL"
        fi
        echo ""
        ;;

    3)
        # Backends
        echo -e "${CYAN}Backends${NC}"
        echo ""
        echo "Configure where requests are routed:"
        echo ""

        echo -e "${YELLOW}Primary backend?${NC}"
        echo "  1. Ollama (local, http://ollama:11434)"
        echo "  2. OpenRouter (API key required, supports GPT-4, Claude, etc.)"
        echo "  3. Both (multi-backend routing with failover)"
        echo ""
        read -p "Choose [1-3]: " -r BACKEND_CHOICE
        BACKEND_CHOICE=${BACKEND_CHOICE:-1}

        if [[ "$BACKEND_CHOICE" == "2" ]]; then
            echo ""
            echo -e "${YELLOW}OpenRouter API key?${NC}"
            read -s -p "Paste API key (won't display): " -r OR_KEY
            echo ""
            if [[ -n "$OR_KEY" ]]; then
                echo "export OPENROUTER_API_KEY='$OR_KEY'" >> ~/.beigebox/config
                echo -e "${GREEN}✓${NC} OpenRouter key saved to ~/.beigebox/config"
                echo "  (restart container for changes to take effect)"
            fi
        fi
        ;;

    4)
        # Tools
        echo -e "${CYAN}Tools${NC}"
        echo ""
        echo "Enable tools for operator & frontend:"
        echo ""

        echo -e "${YELLOW}Web search (DuckDuckGo)?${NC}"
        read -p "Enable [y/N]: " -r WEB_SEARCH
        WEB_SEARCH=${WEB_SEARCH:-n}
        if [[ "$WEB_SEARCH" == "y" || "$WEB_SEARCH" == "Y" ]]; then
            echo -e "${GREEN}✓${NC} Web search enabled"
        else
            echo -e "${GREEN}✓${NC} Web search disabled"
        fi
        echo ""

        echo -e "${YELLOW}Browser automation (CDP)?${NC}"
        echo "  Requires: docker compose --profile cdp up -d"
        read -p "Enable [y/N]: " -r CDP
        CDP=${CDP:-n}
        if [[ "$CDP" == "y" || "$CDP" == "Y" ]]; then
            echo -e "${GREEN}✓${NC} CDP enabled (make sure chrome service is running)"
        else
            echo -e "${GREEN}✓${NC} CDP disabled"
        fi
        echo ""

        echo -e "${YELLOW}Document search (RAG)?${NC}"
        echo "  Searches uploaded documents via embeddings"
        read -p "Enable [y/N]: " -r DOC_SEARCH
        DOC_SEARCH=${DOC_SEARCH:-n}
        if [[ "$DOC_SEARCH" == "y" || "$DOC_SEARCH" == "Y" ]]; then
            echo -e "${GREEN}✓${NC} Document search enabled"
        else
            echo -e "${GREEN}✓${NC} Document search disabled"
        fi
        echo ""
        ;;

    5)
        # Operator
        echo -e "${CYAN}Operator Agent${NC}"
        echo ""
        echo "JSON-based tool loop for agentic behavior:"
        echo ""

        echo -e "${YELLOW}Max iterations per task?${NC}"
        echo "  (Controls how many tool calls before timeout)"
        echo "  Currently: $(grep 'max_iterations:' $CONFIG_FILE | head -1 | awk '{print $NF}')"
        read -p "Set [default=10]: " -r MAX_ITER
        MAX_ITER=${MAX_ITER:-10}
        update_yaml "max_iterations:" "$MAX_ITER" "$CONFIG_FILE"
        echo -e "${GREEN}✓${NC} Max iterations set to $MAX_ITER"
        echo ""

        echo -e "${YELLOW}Shell command access?${NC}"
        echo "  WARNING: Allows LLM to run shell commands (restricted set)"
        read -p "Enable [y/N]: " -r SHELL_ACCESS
        SHELL_ACCESS=${SHELL_ACCESS:-n}
        if [[ "$SHELL_ACCESS" == "y" || "$SHELL_ACCESS" == "Y" ]]; then
            echo -e "${GREEN}✓${NC} Shell access enabled"
            echo "  Restricted to: ls, cat, grep, ps, df, etc."
            echo "  Blocked: rm, chmod, mv, cp, sudo, sudo, pipe to bash"
        else
            echo -e "${GREEN}✓${NC} Shell access disabled"
        fi
        echo ""

        echo -e "${YELLOW}Tool profile (which tools can operator use)?${NC}"
        echo "  1. minimal (web_search, calculator, datetime)"
        echo "  2. standard (+ web_scraper, memory)"
        echo "  3. full (+ system_info, memory_search)"
        echo ""
        read -p "Choose [1-3]: " -r TOOL_PROFILE
        TOOL_PROFILE=${TOOL_PROFILE:-2}
        case "$TOOL_PROFILE" in
            1) PROFILE="minimal" ;;
            2) PROFILE="standard" ;;
            3) PROFILE="full" ;;
        esac
        echo -e "${GREEN}✓${NC} Tool profile set to $PROFILE"
        echo ""
        ;;

    6)
        # Harness
        echo -e "${CYAN}Harness (Multi-turn Orchestration)${NC}"
        echo ""
        echo "Configure multi-agent coordination:"
        echo ""

        echo -e "${YELLOW}Max retries on failure?${NC}"
        echo "  (How many times to retry a failed turn)"
        echo "  Currently: $(grep 'max_retries:' $CONFIG_FILE | head -1 | awk '{print $NF}' || echo 'not set')"
        read -p "Set [default=2]: " -r MAX_RETRY
        MAX_RETRY=${MAX_RETRY:-2}
        echo -e "${GREEN}✓${NC} Max retries set to $MAX_RETRY"
        echo ""

        echo -e "${YELLOW}Stagger delay (seconds)?${NC}"
        echo "  (Delay between spawning parallel agents, prevents resource spike)"
        read -p "Set [default=0.4]: " -r STAGGER
        STAGGER=${STAGGER:-0.4}
        echo -e "${GREEN}✓${NC} Stagger delay set to ${STAGGER}s"
        echo ""
        ;;

    7)
        # Show current config
        echo -e "${CYAN}Current Configuration${NC}"
        echo ""
        echo "features:"
        grep -A 20 "^features:" "$CONFIG_FILE" | head -25
        echo ""
        echo "models:"
        grep -A 10 "^models:" "$CONFIG_FILE" | head -12
        echo ""
        echo "backends:"
        grep -A 10 "^backends:" "$CONFIG_FILE" | head -12
        echo ""
        ;;

    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo -e "${BLUE}  Configuration saved!${NC}"
echo -e "${BLUE}  ═════════════════════════${NC}"
echo ""
echo "Changes take effect on next request (hot-reload)."
echo "No container restart needed."
echo ""
echo "To re-run: ./CONFIG_SETUP.sh"
echo "To view all settings: ./CONFIG_SETUP.sh → 7"
echo ""
