# ✅ COMPLETE — Tested and removed. BitNet b1.58 2B-4T was evaluated and not integrated into BeigeBox.

#!/bin/bash
################################################################################
# BitNet b1.58 2B-4T Quick Start Script
# 
# This script will:
# 1. Create a local bitnet-playground directory
# 2. Install llama-cpp-python with server support
# 3. Download the official Microsoft BitNet GGUF model
# 4. Launch an OpenAI-compatible API server
# 5. Provide test commands to verify everything works
#
# Requirements: Python 3.9+, pip, git, ~2GB disk space
# Tested on: Linux (Ubuntu/Debian), macOS
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_DIR="bitnet-playground"
MODEL_DIR="$PROJECT_DIR/models"
VENV_DIR="$PROJECT_DIR/venv"
MODEL_NAME="BitNet-b1.58-2B-4T-gguf"
MODEL_FILE="ggml-model-i2_s.gguf"
HF_REPO="microsoft/BitNet-b1.58-2B-4T-gguf"
SERVER_PORT=8080
CONTEXT_SIZE=4096
THREADS=16  # Match your 5800X3D's 16 threads

################################################################################
# Helper Functions
################################################################################

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
    if ! command -v $1 &> /dev/null; then
        log_error "$1 is required but not installed. Please install it first."
        exit 1
    fi
}

################################################################################
# Pre-flight Checks
################################################################################

echo ""
echo "========================================"
echo "  BitNet b1.58 2B-4T Quick Start"
echo "========================================"
echo ""

log_info "Running pre-flight checks..."

check_command python3
check_command pip3
check_command git

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ $(python3 -c 'import sys; print(sys.version_info < (3, 9))') == "True" ]]; then
    log_error "Python 3.9+ required. You have Python $PYTHON_VERSION"
    exit 1
fi

log_success "Pre-flight checks passed!"
echo ""

################################################################################
# Step 1: Create Project Directory
################################################################################

log_info "Creating project directory: $PROJECT_DIR"

if [ -d "$PROJECT_DIR" ]; then
    log_warning "Directory $PROJECT_DIR already exists."
    read -p "Do you want to continue? (This may overwrite files) [y/N]: " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        log_info "Aborted by user."
        exit 0
    fi
fi

mkdir -p "$PROJECT_DIR"
mkdir -p "$MODEL_DIR"

log_success "Project directory created."
echo ""

################################################################################
# Step 2: Create Virtual Environment
################################################################################

log_info "Creating Python virtual environment..."

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

log_success "Virtual environment created and activated."
echo ""

################################################################################
# Step 3: Install llama-cpp-python with Server Support
################################################################################

log_info "Installing llama-cpp-python with server support (this may take 5-10 minutes)..."

# Set compilation flags for better CPU performance on AMD
export CMAKE_ARGS="-DLLAMA_AVX2=on -DLLAMA_FMA=on -DLLAMA_NATIVE=off"
export FORCE_CMAKE=1

# Install with server extras
pip install --upgrade pip
pip install "llama-cpp-python[server]"

log_success "llama-cpp-python installed."
echo ""

################################################################################
# Step 4: Download the BitNet Model
################################################################################

log_info "Downloading BitNet model from Hugging Face (~0.5GB)..."

# Install huggingface-cli if not present
pip install huggingface_hub

# Download the model
huggingface-cli download "$HF_REPO" \
    "$MODEL_FILE" \
    --local-dir "$MODEL_DIR" \
    --local-dir-use-symlinks False

# Verify download
if [ -f "$MODEL_DIR/$MODEL_FILE" ]; then
    MODEL_SIZE=$(du -h "$MODEL_DIR/$MODEL_FILE" | cut -f1)
    log_success "Model downloaded successfully! ($MODEL_SIZE)"
else
    log_error "Model download failed. File not found."
    exit 1
fi
echo ""

################################################################################
# Step 5: Create Startup Script
################################################################################

log_info "Creating server startup script..."

cat > "$PROJECT_DIR/start-server.sh" << 'EOF'
#!/bin/bash
################################################################################
# BitNet OpenAI-Compatible Server Launcher
################################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source venv/bin/activate

# Configuration
MODEL_PATH="models/ggml-model-i2_s.gguf"
PORT=8080
CONTEXT_SIZE=4096
THREADS=16

# Check model exists
if [ ! -f "$MODEL_PATH" ]; then
    echo "ERROR: Model not found at $MODEL_PATH"
    echo "Run setup.sh first to download the model."
    exit 1
fi

echo "========================================"
echo "  Starting BitNet OpenAI-Compatible Server"
echo "========================================"
echo ""
echo "Model: $MODEL_PATH"
echo "Port: http://localhost:$PORT"
echo "Context: $CONTEXT_SIZE tokens"
echo "Threads: $THREADS"
echo ""
echo "Endpoints:"
echo "  - GET  http://localhost:$PORT/health"
echo "  - GET  http://localhost:$PORT/v1/models"
echo "  - POST http://localhost:$PORT/v1/chat/completions"
echo ""
echo "Press Ctrl+C to stop the server"
echo "========================================"
echo ""

# Launch the server
python -m llama_cpp.server \
    --model "$MODEL_PATH" \
    --port $PORT \
    --n_ctx $CONTEXT_SIZE \
    --n_threads $THREADS \
    --chat_format chatml \
    --n_batch 512 \
    --n_gpu_layers 0 \
    --verbose
EOF

chmod +x "$PROJECT_DIR/start-server.sh"

log_success "Server startup script created."
echo ""

################################################################################
# Step 6: Create Test Script
################################################################################

log_info "Creating test script..."

cat > "$PROJECT_DIR/test-api.sh" << 'EOF'
#!/bin/bash
################################################################################
# BitNet API Test Script
################################################################################

PORT=8080
BASE_URL="http://localhost:$PORT"

echo "========================================"
echo "  Testing BitNet API"
echo "========================================"
echo ""

# Test 1: Health check
echo "Test 1: Health Check"
echo "--------------------"
curl -s "$BASE_URL/health" | python3 -m json.tool
echo ""

# Test 2: List models
echo "Test 2: List Models"
echo "-------------------"
curl -s "$BASE_URL/v1/models" | python3 -m json.tool
echo ""

# Test 3: Chat completion
echo "Test 3: Chat Completion"
echo "-----------------------"
curl -s "$BASE_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "bitnet-b1.58-2b",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France? Answer in one sentence."}
        ],
        "max_tokens": 100,
        "temperature": 0.3
    }' | python3 -m json.tool
echo ""

# Test 4: Streaming completion
echo "Test 4: Streaming Completion"
echo "----------------------------"
curl -s "$BASE_URL/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "bitnet-b1.58-2b",
        "messages": [
            {"role": "user", "content": "Count from 1 to 5."}
        ],
        "max_tokens": 50,
        "stream": true
    }'
echo ""
echo ""

echo "========================================"
echo "  All tests completed!"
echo "========================================"
EOF

chmod +x "$PROJECT_DIR/test-api.sh"

log_success "Test script created."
echo ""

################################################################################
# Step 7: Create LangChain Example
################################################################################

log_info "Creating LangChain integration example..."

cat > "$PROJECT_DIR/langchain_example.py" << 'EOF'
#!/usr/bin/env python3
"""
BitNet + LangChain Integration Example

This script demonstrates how to use your local BitNet model with LangChain,
exactly like you would use GPT-4 via API.
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# Configuration
BASE_URL = "http://localhost:8080/v1"
API_KEY = "not-needed"  # Any string works for local server
MODEL_NAME = "bitnet-b1.58-2b"

def main():
    print("========================================")
    print("  BitNet + LangChain Example")
    print("========================================")
    print()
    
    # Initialize the LLM (pointing to LOCAL server, not OpenAI)
    llm = ChatOpenAI(
        openai_api_base=BASE_URL,
        openai_api_key=API_KEY,
        model_name=MODEL_NAME,
        temperature=0.3,
        max_tokens=512,
        verbose=True
    )
    
    # Test 1: Simple Q&A
    print("Test 1: Simple Q&A")
    print("------------------")
    response = llm.invoke([HumanMessage(content="What is multi-agent systems in 2 sentences?")])
    print(f"Response: {response.content}")
    print()
    
    # Test 2: With system message
    print("Test 2: With System Message")
    print("---------------------------")
    response = llm.invoke([
        SystemMessage(content="You are a coding assistant. Answer concisely."),
        HumanMessage(content="Write a Python function to calculate fibonacci(n).")
    ])
    print(f"Response: {response.content}")
    print()
    
    print("========================================")
    print("  LangChain integration complete!")
    print("========================================")

if __name__ == "__main__":
    # Check if server is running
    import requests
    try:
        requests.get("http://localhost:8080/health", timeout=2)
    except requests.exceptions.ConnectionError:
        print("ERROR: Server not running. Start it first with:")
        print("  ./start-server.sh")
        exit(1)
    
    # Install langchain if needed
    try:
        import langchain_openai
    except ImportError:
        print("Installing langchain-openai...")
        import subprocess
        subprocess.check_call(["pip", "install", "langchain-openai"])
        import langchain_openai
    
    main()
EOF

chmod +x "$PROJECT_DIR/langchain_example.py"

log_success "LangChain example created."
echo ""

################################################################################
# Step 8: Create README
################################################################################

log_info "Creating README with instructions..."

cat > "$PROJECT_DIR/README.md" << EOF
# BitNet b1.58 2B-4T Playground

Local 1-bit LLM with OpenAI-compatible API.

## Quick Start

### 1. Start the Server
\`\`\`bash
./start-server.sh
\`\`\`

### 2. Test the API (in another terminal)
\`\`\`bash
./test-api.sh
\`\`\`

### 3. Try LangChain Integration
\`\`\`bash
source venv/bin/activate
python langchain_example.py
\`\`\`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| \`/health\` | GET | Health check |
| \`/v1/models\` | GET | List available models |
| \`/v1/chat/completions\` | POST | Chat completions (OpenAI format) |

## Example curl Request

\`\`\`bash
curl http://localhost:8080/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "bitnet-b1.58-2b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
\`\`\`

## Model Info

- **Name**: BitNet b1.58 2B-4T
- **Parameters**: 2B (ternary weights)
- **Memory**: ~0.4 GB RAM
- **Context**: 4096 tokens
- **Speed**: ~15-30 tokens/sec on Ryzen 5800X3D

## Stopping the Server

Press \`Ctrl+C\` in the terminal running \`start-server.sh\`

## Troubleshooting

### Server won't start
- Check if port 8080 is already in use: \`lsof -i :8080\`
- Try a different port: edit \`start-server.sh\` and change \`PORT=8080\`

### Slow inference
- Ensure you're using CPU threads: \`--n_gpu_layers 0\`
- Reduce context size if needed: \`--n_ctx 2048\`

### Model not found
- Re-run setup: \`bash setup.sh\`
- Check model exists: \`ls -lh models/\`

## Resources

- **Original Repo**: https://github.com/microsoft/BitNet
- **Model Card**: https://huggingface.co/microsoft/BitNet-b1.58-2B-4T-gguf
- **Technical Report**: https://arxiv.org/html/2504.12285v1
EOF

log_success "README created."
echo ""

################################################################################
# Step 9: Create Requirements File
################################################################################

log_info "Creating requirements.txt..."

cat > "$PROJECT_DIR/requirements.txt" << 'EOF'
llama-cpp-python[server]>=0.2.90
huggingface_hub>=0.20.0
langchain-openai>=0.1.0
langchain-core>=0.1.0
requests>=2.31.0
EOF

log_success "requirements.txt created."
echo ""

################################################################################
# Complete!
################################################################################

echo ""
echo "========================================"
echo "  🎉 Setup Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "  1. Activate the virtual environment:"
echo "     source $VENV_DIR/bin/activate"
echo ""
echo "  2. Start the server:"
echo "     cd $PROJECT_DIR && ./start-server.sh"
echo ""
echo "  3. In another terminal, test the API:"
echo "     cd $PROJECT_DIR && ./test-api.sh"
echo ""
echo "  4. Try LangChain integration:"
echo "     cd $PROJECT_DIR && python langchain_example.py"
echo ""
echo "Server will be available at: http://localhost:$SERVER_PORT"
echo ""
echo "========================================"
echo ""

# Deactivate virtual environment (user will reactivate when needed)
deactivate 2>/dev/null || true

log_success "All done! See $PROJECT_DIR/README.md for detailed instructions."
echo ""
