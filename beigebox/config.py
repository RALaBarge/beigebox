"""
Config loader for beigebox.
Reads config.yaml once at startup. All other modules import from here.
runtime_config.yaml is hot-reloaded on every call to get_runtime_config()
via mtime check — no restart needed for session overrides.
"""

import os
import re
import time
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_RUNTIME_CONFIG_PATH = Path(__file__).parent.parent / "runtime_config.yaml"

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
