"""
PlanManagerTool — first-class plan.md management for multi-turn orchestration.

Creates, reads, updates, and appends to workspace/out/plan.md.
Tracks modification timestamps so the Operator always knows the plan state.

Input format (JSON string):
    {"action": "create", "content": "# Research Plan\n..."}
    {"action": "read"}
    {"action": "update", "content": "# Updated Plan\n..."}
    {"action": "append", "content": "## New Section\n..."}
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()

_PLAN_FILENAME = "plan.md"


class PlanManagerTool:
    description = (
        'Manage the research plan (workspace/out/plan.md). '
        'input MUST be a JSON object. '
        'Required key: "action" — one of: create, read, update, append. '
        'Required for create/update/append: "content" (markdown string). '
        'Examples:\n'
        '  {"action":"create","content":"# Plan\\n## Phase 1\\n- Step 1\\n"}\n'
        '  {"action":"read"}\n'
        '  {"action":"update","content":"# Updated Plan\\n..."}\n'
        '  {"action":"append","content":"\\n## Phase 2\\n- Step 3\\n"}'
    )

    def __init__(self, workspace_out: Path | None = None):
        self._root = (workspace_out or _WORKSPACE_OUT).resolve()
        self._plan_path = self._root / _PLAN_FILENAME

    def _ensure_dir(self):
        self._root.mkdir(parents=True, exist_ok=True)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read_plan(self) -> str:
        if self._plan_path.exists():
            return self._plan_path.read_text(encoding="utf-8")
        return ""

    def _write_plan(self, content: str):
        self._ensure_dir()
        self._plan_path.write_text(content, encoding="utf-8")

    def run(self, input_text: str) -> str:
        try:
            params = json.loads(input_text)
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, TypeError, ValueError):
            return (
                'Error: input must be a JSON object. '
                'Example: {"action":"read"}'
            )

        action = params.get("action", "").lower().strip()
        content = params.get("content", "")

        if not action:
            # Infer from fields
            if content:
                action = "create" if not self._plan_path.exists() else "update"
            else:
                action = "read"

        if action == "read":
            plan_content = self._read_plan()
            if not plan_content:
                return json.dumps({
                    "action": "read",
                    "path": str(self._plan_path),
                    "content": "",
                    "modified_at": None,
                    "status": "success",
                    "message": "No plan exists yet. Use action=create to start one."
                })
            mtime = datetime.fromtimestamp(
                self._plan_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            return json.dumps({
                "action": "read",
                "path": str(self._plan_path),
                "content": plan_content,
                "modified_at": mtime,
                "status": "success"
            })

        if action == "create":
            if not content:
                return 'Error: "content" is required for action=create.'
            self._write_plan(content)
            return json.dumps({
                "action": "create",
                "path": str(self._plan_path),
                "content": content,
                "modified_at": self._now_iso(),
                "status": "success"
            })

        if action == "update":
            if not content:
                return 'Error: "content" is required for action=update.'
            self._write_plan(content)
            return json.dumps({
                "action": "update",
                "path": str(self._plan_path),
                "content": content,
                "modified_at": self._now_iso(),
                "status": "success"
            })

        if action == "append":
            if not content:
                return 'Error: "content" is required for action=append.'
            existing = self._read_plan()
            # Ensure newline separator
            separator = ""
            if existing and not existing.endswith("\n"):
                separator = "\n"
            new_content = existing + separator + content
            self._write_plan(new_content)
            return json.dumps({
                "action": "append",
                "path": str(self._plan_path),
                "content": new_content,
                "modified_at": self._now_iso(),
                "status": "success"
            })

        return f'Error: unknown action "{action}". Use: create, read, update, or append.'
