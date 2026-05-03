"""ToolRegistry + HookManager bootstrap.

The tool registry needs the vector store (for the memory tool) and the
hook manager needs the configured ``hooks`` block. Both are constructed
here so the orchestrator doesn't have to know which lives where.
"""
from __future__ import annotations

from dataclasses import dataclass

from beigebox.hooks import HookManager
from beigebox.tools.registry import ToolRegistry


@dataclass
class ToolsBundle:
    tool_registry: ToolRegistry
    hook_manager: HookManager


def build_tools(cfg: dict, vector_store) -> ToolsBundle:
    """Build tool registry + hook manager. ``vector_store`` comes from
    ``build_storage(...)`` and is required (memory tool depends on it).
    """
    tool_registry = ToolRegistry(vector_store=vector_store)

    hooks_cfg = cfg.get("hooks", {})
    hooks_enabled = hooks_cfg.get("enabled", True) if isinstance(hooks_cfg, dict) else True
    hook_list = hooks_cfg.get("hooks", []) if isinstance(hooks_cfg, dict) else []
    hook_manager = HookManager(
        hooks_dir=hooks_cfg.get("directory", "./hooks") if hooks_enabled else None,
        hook_configs=hook_list if isinstance(hook_list, list) else [],
    )

    return ToolsBundle(
        tool_registry=tool_registry,
        hook_manager=hook_manager,
    )


__all__ = ["ToolsBundle", "build_tools"]
