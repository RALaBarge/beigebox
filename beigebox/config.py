"""
Config loader for beigebox.
Reads config.yaml once at startup. All other modules import from here.
runtime_config.yaml is hot-reloaded on every call to get_runtime_config()
via mtime check — no restart needed for session overrides.

On load, config is validated against a Pydantic schema. Unknown top-level
keys (likely typos) and type mismatches on known fields are logged as
warnings. Validation never blocks startup — warnings only.
"""

import logging
import os
import re
import time
import yaml
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from pydantic import BaseModel, ConfigDict, ValidationError
from beigebox.constants import DEFAULT_MODEL, DEFAULT_EMBEDDING_MODEL

_vlog = logging.getLogger(__name__)

# ── Known top-level config keys ──────────────────────────────────────────────
_KNOWN_TOP_LEVEL_KEYS = {
    "server", "backend", "backends", "backends_enabled", "embedding",
    "storage", "logging", "auth", "decision_llm", "operator", "tools",
    "routing", "cost_tracking", "harness", "conversation_replay",
    "auto_summarization", "aggressive_summarization", "system_context", "generation", "models",
    "wasm", "web_ui", "voice", "wiretap", "semantic_cache", "classifier",
    "model_advertising", "zcommands", "advanced", "runtime", "skills",
    "workspace", "hooks", "connections", "amf_mesh", "security",
}

# ── Pydantic models for key sections ─────────────────────────────────────────
# extra='allow' so unknown sub-keys never break anything.
# Type annotations catch wrong types (e.g. enabled: "yes" instead of true).

class _FeaturesCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    backends: bool = True
    decision_llm: bool = True
    classifier: bool = True
    semantic_cache: bool = False
    operator: bool = True
    harness: bool = True
    tools: bool = True
    cost_tracking: bool = True
    conversation_replay: bool = True
    auto_summarization: bool = False
    aggressive_summarization: bool = False
    system_context: bool = False
    wiretap: bool = True
    payload_log: bool = False
    wasm: bool = False
    guardrails: bool = False
    amf_mesh: bool = False
    voice: bool = False
    hooks: bool = False

class _ServerCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str = "0.0.0.0"
    port: int = 8000

class _BackendCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str = ""
    default_model: str = ""
    timeout: float = 120

class _ModelsCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    default: str = DEFAULT_MODEL
    profiles: dict = {}
    per_task: dict = {}
    whitelist: dict = {}

class _DecisionLLMCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    temperature: float = 0.2
    timeout: float = 5
    max_tokens: int = 256

class _OperatorCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    max_iterations: int = 8
    timeout: float = 60

class _GenerationCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    force: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    num_ctx: Optional[int] = None
    repeat_penalty: Optional[float] = None
    max_tokens: Optional[int] = None
    seed: Optional[int] = None

class _CostTrackingCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    track_openrouter: bool = True
    track_local: bool = False

class _AutoSumCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    token_budget: int = 3000
    keep_last: int = 4

class _AggSumCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    keep_last: int = 2
    model: str = ""

class _RAGPoisoningCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    detection_mode: str = "warn"  # warn, quarantine, strict
    sensitivity: float = 0.95
    baseline_window: int = 1000
    min_norm: float = 0.1
    max_norm: float = 100.0

class _MemoryIntegrityCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    mode: str = "log_only"  # log_only, quarantine, or strict
    key_source: str = "env"  # env, file, or keyring
    key_path: str = "~/.beigebox/memory.key"
    dev_mode: bool = False  # graceful degradation if key missing (dev only)

class _APIAnomalyCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    detection_mode: str = "warn"  # warn, rate_limit, block
    baseline_window_seconds: int = 300  # 5-min rolling window
    request_rate_threshold: int = 5  # max requests per minute
    error_rate_threshold: float = 0.30  # max 30% errors
    model_switch_threshold: int = 8  # max distinct models in window
    latency_z_threshold: float = 3.0  # z-score for latency anomalies
    payload_min_chars: int = 50  # minimum request size
    payload_max_bytes: int = 100000  # maximum request size
    ip_instability_threshold: int = 5  # max IPs per conversation
    rules: dict = {}  # per-rule overrides

class _MCPValidatorCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    allow_unsafe: bool = False
    log_violations: bool = True
    allow_localhost_cdp: bool = False
    max_code_length: int = 10_000
    max_query_length: int = 4_000
    max_network_cidr: int = 24
    max_ports: int = 100
    max_network_timeout: float = 30.0

class _SecurityCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    rag_poisoning: _RAGPoisoningCfg = _RAGPoisoningCfg()
    memory_integrity: _MemoryIntegrityCfg = _MemoryIntegrityCfg()
    api_anomaly: _APIAnomalyCfg = _APIAnomalyCfg()
    mcp_validator: _MCPValidatorCfg = _MCPValidatorCfg()

class _BeigeBoxConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backends_enabled: bool = False
    features: _FeaturesCfg = _FeaturesCfg()
    server: _ServerCfg = _ServerCfg()
    backend: _BackendCfg = _BackendCfg()
    models: _ModelsCfg = _ModelsCfg()
    decision_llm: _DecisionLLMCfg = _DecisionLLMCfg()
    operator: _OperatorCfg = _OperatorCfg()
    generation: _GenerationCfg = _GenerationCfg()
    cost_tracking: _CostTrackingCfg = _CostTrackingCfg()
    auto_summarization: _AutoSumCfg = _AutoSumCfg()
    aggressive_summarization: _AggSumCfg = _AggSumCfg()
    security: _SecurityCfg = _SecurityCfg()


def _validate_config(cfg: dict) -> None:
    """Warn on unknown top-level keys and type mismatches in known sections."""
    unknown = set(cfg.keys()) - _KNOWN_TOP_LEVEL_KEYS
    if unknown:
        _vlog.warning(
            "config.yaml: unrecognised top-level key(s) — possible typo: %s",
            sorted(unknown),
        )
    try:
        _BeigeBoxConfig.model_validate(cfg)
    except ValidationError as e:
        for err in e.errors():
            loc = " → ".join(str(x) for x in err["loc"])
            _vlog.warning("config.yaml: %s: %s", loc, err["msg"])

load_dotenv()

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
# data/ is a Docker volume mount — settings written here survive container
# rebuilds/restarts. In bare-metal dev, data/ sits next to config.yaml.
_RUNTIME_CONFIG_PATH = Path(__file__).parent.parent / "data" / "runtime_config.yaml"

_config: dict | None = None

# Runtime config hot-reload state
_runtime_config: dict = {}
_runtime_mtime: float = 0.0
_runtime_mtime_last_checked: float = 0.0
_RUNTIME_MTIME_CHECK_INTERVAL: float = 1.0  # stat() syscall at most once per second


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} and ${ENV_VAR:-default} patterns with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        # group(2) is None when the ":-default" suffix is absent; distinguish
        # between "no default provided" (leave empty) and explicit empty default.
        default  = match.group(2)
        val = os.environ.get(var_name)
        if val is not None:
            return val
        return default if default is not None else ""
    return re.sub(r"\$\{(\w+)(?::-(.*?))?\}", replacer, value)


def _walk_and_resolve(obj):
    """Recursively resolve env vars in all string values."""
    if isinstance(obj, dict):
        return {k: _walk_and_resolve(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_walk_and_resolve(v) for v in obj]
    elif isinstance(obj, str):
        return _resolve_env_vars(obj)
    return obj


def load_config(path: Path | None = None) -> dict:
    """Load and cache config from YAML file."""
    global _config
    if _config is not None:
        return _config

    config_path = path or _CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    _config = _walk_and_resolve(raw)
    _validate_config(_config)

    # Backward compatibility: add virtual 'backend' key for old config access patterns
    # Maps cfg["backend"]["url"] to the new backends[0].url format
    if "backend" not in _config:
        backends = _config.get("backends", [])
        _config["backend"] = {
            "url": backends[0].get("url", "") if backends else "",
            "default_model": _config.get("models", {}).get("default", ""),
            "timeout": backends[0].get("timeout", 120) if backends else 120,
        }

    return _config


def get_config() -> dict:
    """Return cached base config, loading if necessary."""
    if _config is None:
        return load_config()
    return _config


def get_runtime_config() -> dict:
    """
    Return runtime_config.yaml overrides, hot-reloading if the file changed.
    Returns the contents of the `runtime` key, or {} if file is missing/empty.
    """
    global _runtime_config, _runtime_mtime, _runtime_mtime_last_checked

    import time as _time
    now = _time.monotonic()
    if now - _runtime_mtime_last_checked < _RUNTIME_MTIME_CHECK_INTERVAL:
        return _runtime_config
    _runtime_mtime_last_checked = now

    if not _RUNTIME_CONFIG_PATH.exists():
        return {}

    try:
        mtime = _RUNTIME_CONFIG_PATH.stat().st_mtime
    except OSError:
        return _runtime_config

    if mtime == _runtime_mtime:
        return _runtime_config

    # File changed — reload
    try:
        with open(_RUNTIME_CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}
        _runtime_config = data.get("runtime", {})
        _runtime_mtime = mtime
    except yaml.YAMLError as e:
        _vlog.warning("runtime_config.yaml parse error (keeping last good config): %s", e)
    except Exception as e:
        _vlog.warning("runtime_config.yaml load failed: %s", e)

    return _runtime_config


def get_storage_paths(cfg: dict | None = None) -> tuple[str, str]:
    """
    Return normalized storage paths as (sqlite_path, vector_store_path).

    Preferred keys:
      - storage.path
      - storage.vector_store_path

    Legacy compatibility keys still accepted:
      - storage.sqlite_path
      - storage.chroma_path
    """
    cfg = cfg or get_config()
    storage_cfg = cfg.get("storage", {})

    sqlite_path = (
        storage_cfg.get("path")
        or storage_cfg.get("sqlite_path")
        or "./data/beigebox.db"
    )
    vector_store_path = (
        storage_cfg.get("vector_store_path")
        or storage_cfg.get("chroma_path")
        or "./data/chroma"
    )

    return sqlite_path, vector_store_path

def get_effective_backends_config() -> tuple[bool, list[dict]]:
    """
    Return (backends_enabled, backends_list) merging runtime config onto static config.
    Runtime config takes precedence — lets you configure backends via the hot-reloaded
    runtime_config.yaml without touching config.yaml or restarting.

    Always ensures an Ollama backend is present so local models are never left
    without a route when only API backends (e.g. OpenRouter) are configured at runtime.
    """
    cfg = get_config()
    rt = get_runtime_config()
    enabled = rt.get("backends_enabled", cfg.get("backends_enabled", False))
    # Runtime backends list (if present) fully replaces static config list
    backends = list(rt.get("backends") if rt.get("backends") is not None else cfg.get("backends", []))

    # If runtime backends exist but none is an Ollama backend, inject the primary one
    # from static config so local model requests always have a valid route.
    if backends and not any(b.get("provider") == "ollama" for b in backends):
        static_ollama = next(
            (b for b in cfg.get("backends", []) if b.get("provider") == "ollama"),
            None,
        )
        if static_ollama is None:
            # Fall back to constructing one from the primary backend URL
            primary_url = cfg.get("backend", {}).get("url", "").rstrip("/")
            if primary_url:
                static_ollama = {
                    "provider": "ollama",
                    "name": "ollama-local",
                    "url": primary_url,
                    "priority": 1,
                }
        if static_ollama:
            backends = [static_ollama] + backends

    return bool(enabled), backends


def get_primary_backend_url(cfg: dict | None = None) -> str:
    """
    Return the primary backend URL from the new config structure.
    Falls back to old config format for compatibility.
    """
    cfg = cfg or get_config()

    # Try new format first: backends list
    backends = cfg.get("backends", [])
    if backends and isinstance(backends, list) and len(backends) > 0:
        return backends[0].get("url", "").rstrip("/")

    # Fall back to old format for compatibility
    return cfg.get("backend", {}).get("url", "").rstrip("/")


def update_runtime_config(key: str, value) -> bool:
    """
    Write a single key into the runtime: block of runtime_config.yaml.
    Returns True on success.

    Passing value=None removes the key from the runtime block.
    """
    try:
        if _RUNTIME_CONFIG_PATH.exists():
            with open(_RUNTIME_CONFIG_PATH) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        if "runtime" not in data or not isinstance(data["runtime"], dict):
            data["runtime"] = {}

        if value is None:
            data["runtime"].pop(key, None)
        else:
            data["runtime"][key] = value

        if not _RUNTIME_CONFIG_PATH.parent.exists():
            return False
        # Atomic write — write to a temp file then rename so a crash mid-write
        # never leaves a truncated/corrupt runtime_config.yaml.
        import tempfile as _tempfile
        tmp_fd, tmp_path = _tempfile.mkstemp(
            dir=_RUNTIME_CONFIG_PATH.parent, suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            os.replace(tmp_path, _RUNTIME_CONFIG_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

        # Reset both mtime and the last-checked timestamp so the next
        # get_runtime_config() call bypasses the 1s interval guard and
        # reloads immediately — important for the config POST → GET test.
        global _runtime_mtime, _runtime_mtime_last_checked
        _runtime_mtime = 0.0
        _runtime_mtime_last_checked = 0.0
        return True
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error(
            "update_runtime_config(%s) failed: %s (path=%s, writable=%s)",
            key, e, _RUNTIME_CONFIG_PATH,
            os.access(_RUNTIME_CONFIG_PATH.parent, os.W_OK),
        )
        return False


def write_runtime_config(data: dict) -> bool:
    """
    Atomically replace the entire runtime_config.yaml with the given data dict.

    The data should be a dict whose contents will be wrapped in a ``runtime:`` key,
    matching the expected file structure. Used by the DGM revert endpoint to restore
    a pre-run snapshot.

    Returns True on success.
    """
    import tempfile as _tempfile
    try:
        if not _RUNTIME_CONFIG_PATH.parent.exists():
            return False
        full = {"runtime": data}
        tmp_fd, tmp_path = _tempfile.mkstemp(
            dir=_RUNTIME_CONFIG_PATH.parent, suffix=".tmp"
        )
        try:
            import yaml as _yaml
            with os.fdopen(tmp_fd, "w") as f:
                _yaml.dump(full, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            os.replace(tmp_path, _RUNTIME_CONFIG_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception as e:
        _log.getLogger(__name__).error("write_runtime_config failed: %s", e)
        return False
