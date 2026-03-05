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

_vlog = logging.getLogger(__name__)

# ── Known top-level config keys ──────────────────────────────────────────────
_KNOWN_TOP_LEVEL_KEYS = {
    "server", "backend", "backends", "backends_enabled", "embedding",
    "storage", "logging", "auth", "decision_llm", "operator", "tools",
    "routing", "cost_tracking", "harness", "conversation_replay",
    "auto_summarization", "system_context", "generation", "models",
    "wasm", "web_ui", "voice", "wiretap", "semantic_cache", "classifier",
    "model_advertising", "zcommands", "advanced", "runtime", "skills",
}

# ── Pydantic models for key sections ─────────────────────────────────────────
# extra='allow' so unknown sub-keys never break anything.
# Type annotations catch wrong types (e.g. enabled: "yes" instead of true).

class _ServerCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str = "0.0.0.0"
    port: int = 8000

class _BackendCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str = ""
    default_model: str = ""
    timeout: float = 120

class _DecisionLLMCfg(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    model: str = ""
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

class _BeigeBoxConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backends_enabled: bool = False
    server: _ServerCfg = _ServerCfg()
    backend: _BackendCfg = _BackendCfg()
    decision_llm: _DecisionLLMCfg = _DecisionLLMCfg()
    operator: _OperatorCfg = _OperatorCfg()
    generation: _GenerationCfg = _GenerationCfg()
    cost_tracking: _CostTrackingCfg = _CostTrackingCfg()
    auto_summarization: _AutoSumCfg = _AutoSumCfg()


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
# data/ is mounted as a Docker volume so runtime settings survive image rebuilds
_RUNTIME_CONFIG_PATH = Path(__file__).parent.parent / "data" / "runtime_config.yaml"

_config: dict | None = None

# Runtime config hot-reload state
_runtime_config: dict = {}
_runtime_mtime: float = 0.0


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} patterns with actual environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    return re.sub(r"\$\{(\w+)\}", replacer, value)


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
    global _runtime_config, _runtime_mtime

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
    except Exception:
        pass  # Keep last good config on parse error

    return _runtime_config


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


def update_runtime_config(key: str, value) -> bool:
    """
    Write a single key into the runtime: block of runtime_config.yaml.
    Thread-safe via file read-modify-write. Returns True on success.
    """
    try:
        if _RUNTIME_CONFIG_PATH.exists():
            with open(_RUNTIME_CONFIG_PATH) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        if "runtime" not in data or not isinstance(data["runtime"], dict):
            data["runtime"] = {}

        data["runtime"][key] = value

        with open(_RUNTIME_CONFIG_PATH, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        # Bust the mtime cache so next get_runtime_config() picks it up
        global _runtime_mtime
        _runtime_mtime = 0.0
        return True
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error(
            "update_runtime_config(%s) failed: %s (path=%s, writable=%s)",
            key, e, _RUNTIME_CONFIG_PATH,
            os.access(_RUNTIME_CONFIG_PATH.parent, os.W_OK),
        )
        return False
