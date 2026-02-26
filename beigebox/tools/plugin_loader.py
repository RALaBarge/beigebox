"""
Plugin loader — auto-discovers tool plugins from the plugins/ directory.

Drop a .py file into plugins/ and it's automatically registered at startup.
No code changes required anywhere else.

Plugin contract
---------------
A plugin file must contain exactly one class that ends in "Tool" and has a
callable .run(self, input: str) -> str method.

Optionally it may define:
    PLUGIN_NAME   = "my_tool"   # registry key (defaults to snake_case class name)
    PLUGIN_CONFIG = "my_tool"   # key under tools: in config.yaml (defaults to PLUGIN_NAME)

Plugin file example
-------------------
    # plugins/emoji_tool.py
    PLUGIN_NAME = "emoji"

    class EmojiTool:
        def __init__(self):
            pass

        def run(self, query: str) -> str:
            return "😎"

Config example (config.yaml)
----------------------------
    tools:
      plugins:
        enabled: true          # master switch for all plugins
        path: ./plugins        # directory to scan (relative to project root)
        emoji:
          enabled: true        # per-plugin enable flag

The loader respects the per-plugin enabled flag.  If the config key is
absent the plugin defaults to enabled.
"""

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _class_to_name(cls_name: str) -> str:
    """Convert 'MyFancyTool' → 'my_fancy_tool'."""
    import re
    s = re.sub(r"Tool$", "", cls_name)          # strip trailing Tool
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.lower().strip("_") or cls_name.lower()


def _find_tool_class(module) -> type | None:
    """Return the first class ending in 'Tool' that has a .run() method."""
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue  # skip imported classes
        if name.endswith("Tool") and callable(getattr(obj, "run", None)):
            return obj
    return None


def load_plugins(plugins_dir: str | Path, tools_cfg: dict) -> dict[str, object]:
    """
    Scan plugins_dir for .py files and return {name: tool_instance} for
    every plugin that is enabled in tools_cfg.

    Args:
        plugins_dir: Path to the plugins/ directory.
        tools_cfg:   The tools: section from config.yaml.

    Returns:
        Dict of registered plugin tools keyed by their PLUGIN_NAME.
    """
    plugins_cfg = tools_cfg.get("plugins", {})
    if not plugins_cfg.get("enabled", False):
        logger.debug("Plugin loader disabled (tools.plugins.enabled=false)")
        return {}

    base = Path(plugins_dir).resolve()
    if not base.is_dir():
        logger.warning("Plugin directory not found: %s", base)
        return {}

    registered: dict[str, object] = {}

    for py_file in sorted(base.glob("*.py")):
        if py_file.name.startswith("_"):
            continue  # skip __init__.py, _helpers.py, etc.

        module_name = f"bb_plugin_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Plugin: could not load spec for %s", py_file.name)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[attr-defined]

        except Exception as e:
            logger.error("Plugin: failed to import %s: %s", py_file.name, e)
            continue

        # Determine the tool's registry name
        plugin_name: str = getattr(module, "PLUGIN_NAME", "")
        config_key: str  = getattr(module, "PLUGIN_CONFIG", plugin_name)

        cls = _find_tool_class(module)
        if cls is None:
            logger.warning("Plugin: no Tool class found in %s — skipped", py_file.name)
            continue

        if not plugin_name:
            plugin_name = _class_to_name(cls.__name__)
        if not config_key:
            config_key = plugin_name

        # Check per-plugin enabled flag (absent = enabled)
        plugin_tool_cfg = plugins_cfg.get(config_key, {})
        if not plugin_tool_cfg.get("enabled", True):
            logger.debug("Plugin '%s' disabled in config — skipped", plugin_name)
            continue

        try:
            instance = cls()
            registered[plugin_name] = instance
            logger.info("Plugin loaded: '%s' ← %s (%s)", plugin_name, py_file.name, cls.__name__)
        except Exception as e:
            logger.error("Plugin: failed to instantiate %s from %s: %s", cls.__name__, py_file.name, e)

    if registered:
        logger.info("Plugin loader: registered %d plugin(s): %s", len(registered), list(registered.keys()))
    else:
        logger.debug("Plugin loader: no plugins registered")

    return registered
