"""
Global constants for BeigeBox configuration.

Collects magic numbers and configuration values from across the codebase
into a single place for easy discovery and adjustment.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Timeouts (in seconds, unless suffixed otherwise)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BACKEND_TIMEOUT = 120  # seconds
DEFAULT_EMBEDDING_TIMEOUT = 30  # seconds

# ─────────────────────────────────────────────────────────────────────────────
# Latency & Performance
# ─────────────────────────────────────────────────────────────────────────────

LATENCY_WINDOW_SIZE = 100  # samples for P95 calculation
LATENCY_PERCENTILE = 0.95  # P95 percentile
LATENCY_P95_THRESHOLD_MS = 3000  # deprioritize backends exceeding this

# ─────────────────────────────────────────────────────────────────────────────
# Retry & Backoff
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE = 1.5
DEFAULT_BACKOFF_MAX = 10.0

# ─────────────────────────────────────────────────────────────────────────────
# Embeddings (memory/vector subsystem)
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDING_BORDER_THRESHOLD = 0.04  # classifier threshold for borderline cases
EMBEDDING_BATCH_SIZE = 32

# ─────────────────────────────────────────────────────────────────────────────
# Session & Routing
# ─────────────────────────────────────────────────────────────────────────────

SESSION_CACHE_TTL_SECONDS = 1800  # 30 minutes
ROUTING_SESSION_TTL_SECONDS = 1800

# ─────────────────────────────────────────────────────────────────────────────
# Harness & Orchestration
# ─────────────────────────────────────────────────────────────────────────────

HARNESS_DEFAULT_STAGGER_OPERATOR_MS = 1000
HARNESS_DEFAULT_STAGGER_MODEL_MS = 400
HARNESS_DEFAULT_MAX_STORED_RUNS = 1000
HARNESS_SHADOW_AGENTS_TIMEOUT = 30  # seconds
HARNESS_SHADOW_AGENTS_DIVERGENCE_THRESHOLD = 0.3

# ─────────────────────────────────────────────────────────────────────────────
# Wiretap & Logging
# ─────────────────────────────────────────────────────────────────────────────

WIRETAP_DEFAULT_MAX_LINES = 100000
WIRETAP_LOG_LEVEL_DEFAULT = "INFO"

# ─────────────────────────────────────────────────────────────────────────────
# Auto-Summarization
# ─────────────────────────────────────────────────────────────────────────────

AUTO_SUMMARIZATION_DEFAULT_TOKEN_BUDGET = 24000
AUTO_SUMMARIZATION_DEFAULT_KEEP_LAST = 8

# ─────────────────────────────────────────────────────────────────────────────
# Vector Store & Embeddings
# ─────────────────────────────────────────────────────────────────────────────

VECTOR_STORE_DEFAULT_PATH = "./data/chroma"
VECTOR_BACKEND_DEFAULT = "chromadb"

# ─────────────────────────────────────────────────────────────────────────────
# API & HTTP
# ─────────────────────────────────────────────────────────────────────────────

HTTPX_DEFAULT_TIMEOUT = 120.0
HTTPX_CONNECTION_POOL_LIMITS = {"max_keepalive_connections": 20, "max_connections": 100}

# ─────────────────────────────────────────────────────────────────────────────
# Routing Strategies
# ─────────────────────────────────────────────────────────────────────────────

ROUTES = ["simple", "complex", "code", "large", "fast"]
ROUTE_SIMPLE = "simple"
ROUTE_COMPLEX = "complex"
ROUTE_CODE = "code"
ROUTE_LARGE = "large"
ROUTE_FAST = "fast"

# ─────────────────────────────────────────────────────────────────────────────
# Model Defaults (Single Source of Truth)
# ─────────────────────────────────────────────────────────────────────────────
# Define all model defaults here. Config files and code fallbacks reference these.
# Users can override at runtime via:
#   1. request model parameter: {"model": "qwen3:30b"}
#   2. runtime_config.yaml: models_default: qwen3:30b
#   3. Z-command: "z: model=qwen3:30b"
#
# DO NOT hardcode model names elsewhere. Use these constants.

DEFAULT_MODEL = "llama3.2:3b"                    # General chat (fast, good enough)
DEFAULT_ROUTING_MODEL = "llama3.2:3b"            # Backend picker (fast, lightweight)
DEFAULT_AGENTIC_MODEL = "llama3.2:3b"            # Tool use agent (multi-step reasoning)
DEFAULT_SUMMARY_MODEL = "llama3.2:3b"            # Context compression (lightweight)
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"     # Semantic search & caching (unchanged)

# ─────────────────────────────────────────────────────────────────────────────
# Z-Command Defaults
# ─────────────────────────────────────────────────────────────────────────────

ZCOMMAND_PREFIX = "z:"
ZCOMMAND_SEPARATOR = ","
