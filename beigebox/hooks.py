"""
Hooks — extensible pre/post processing pipeline.

Drop a Python file in the hooks/ directory, implement the interface,
and BeigeBox will call it on every request/response.

Two hook types:
  - pre_request: modify the request before it hits the backend
  - post_response: modify the response before it goes back to the frontend

Each hook is a Python file with one or both functions:
    def pre_request(body: dict, context: dict) -> dict
    def post_response(body: dict, response: dict, context: dict) -> dict

The context dict contains:
    - conversation_id: str
    - model: str
    - user_message: str (latest user message)
    - decision: Decision | None (if decision LLM is enabled)
    - config: dict (the full config)

Hooks are loaded from config and executed in order. If a hook raises
an exception, it's logged and skipped — never blocks the pipeline.
"""

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Hook:
    """A loaded hook with its callable functions."""
    name: str
    path: str
    pre_request: Callable | None = None
    post_response: Callable | None = None
    enabled: bool = True


class HookManager:
    """Loads and executes hooks from the hooks directory."""

    def __init__(self, hooks_dir: str | None = None, hook_configs: list[dict] | None = None):
        self.hooks: list[Hook] = []

        if hooks_dir:
            self._load_directory(hooks_dir)

        if hook_configs:
            self._load_from_config(hook_configs)

    def _load_module(self, path: str, name: str) -> Hook:
        """Load a Python file as a hook module."""
        try:
            spec = importlib.util.spec_from_file_location(f"beigebox_hook_{name}", path)
            if spec is None or spec.loader is None:
                logger.error("Could not load hook '%s' from %s", name, path)
                return Hook(name=name, path=path, enabled=False)

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            hook = Hook(
                name=name,
                path=path,
                pre_request=getattr(module, "pre_request", None),
                post_response=getattr(module, "post_response", None),
            )

            funcs = []
            if hook.pre_request:
                funcs.append("pre_request")
            if hook.post_response:
                funcs.append("post_response")

            if funcs:
                logger.info("Loaded hook '%s': %s", name, ", ".join(funcs))
            else:
                logger.warning("Hook '%s' has no pre_request or post_response function", name)
                hook.enabled = False

            return hook

        except Exception as e:
            logger.error("Failed to load hook '%s' from %s: %s", name, path, e)
            return Hook(name=name, path=path, enabled=False)

    def _load_directory(self, hooks_dir: str):
        """Load all .py files from a directory as hooks."""
        hooks_path = Path(hooks_dir)
        if not hooks_path.exists():
            logger.debug("Hooks directory %s does not exist, skipping", hooks_dir)
            return

        for py_file in sorted(hooks_path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = py_file.stem
            hook = self._load_module(str(py_file), name)
            if hook.enabled:
                self.hooks.append(hook)

    def _load_from_config(self, hook_configs: list[dict]):
        """Load hooks specified in config.yaml."""
        for hc in hook_configs:
            path = hc.get("path", "")
            name = hc.get("name", Path(path).stem if path else "unknown")
            enabled = hc.get("enabled", True)

            if not enabled:
                logger.debug("Hook '%s' disabled in config", name)
                continue

            if not path or not Path(path).exists():
                logger.warning("Hook '%s' path not found: %s", name, path)
                continue

            hook = self._load_module(path, name)
            if hook.enabled:
                self.hooks.append(hook)

    def run_pre_request(self, body: dict, context: dict) -> dict:
        """
        Run all pre_request hooks in order.
        Each hook receives and returns the (possibly modified) body.
        """
        for hook in self.hooks:
            if not hook.enabled or not hook.pre_request:
                continue
            try:
                result = hook.pre_request(body, context)
                if result is not None and isinstance(result, dict):
                    body = result
                    logger.debug("Hook '%s' pre_request applied", hook.name)
            except Exception as e:
                logger.error("Hook '%s' pre_request failed: %s", hook.name, e)
        return body

    def run_post_response(self, body: dict, response: dict, context: dict) -> dict:
        """
        Run all post_response hooks in order.
        Each hook receives and returns the (possibly modified) response.
        """
        for hook in self.hooks:
            if not hook.enabled or not hook.post_response:
                continue
            try:
                result = hook.post_response(body, response, context)
                if result is not None and isinstance(result, dict):
                    response = result
                    logger.debug("Hook '%s' post_response applied", hook.name)
            except Exception as e:
                logger.error("Hook '%s' post_response failed: %s", hook.name, e)
        return response

    def list_hooks(self) -> list[str]:
        """Return names of all loaded hooks."""
        return [h.name for h in self.hooks if h.enabled]
