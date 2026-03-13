"""
WorkspaceFileTool — read and write files inside /workspace/out/.

Gives the operator persistent state across multi-turn runs.
Writes are append-or-overwrite; reads return the current file contents.

Input format (JSON string):
    {"action": "write",  "path": "plan.md", "content": "# Plan\n..."}
    {"action": "append", "path": "plan.md", "content": "## Step 2\n..."}
    {"action": "read",   "path": "plan.md"}
    {"action": "list"}

Paths may be relative ("plan.md") or absolute ("/workspace/out/plan.md").
No path traversal outside /workspace/out/ is allowed.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()

_MAX_READ_BYTES = 32_000   # ~8k tokens — keeps context manageable
_MAX_WRITE_BYTES = 64_000  # hard cap on a single write

# Common absolute-path prefixes the model may use — stripped before resolution
_ABS_PREFIXES = ("/workspace/out/", "/workspace/out")


class WorkspaceFileTool:
    description = (
        'Save and load files in /workspace/out/ for persistent notes between turns. '
        'input MUST be a JSON object (not a plain string). '
        'Required key: "action" — one of: write, append, read, list. '
        'Required for read/write/append: "path" (filename, e.g. "plan.md"). '
        'Required for write/append: "content" (string to write). '
        'CORRECT examples:\n'
        '  {"action":"write","path":"plan.md","content":"# Plan\\nStep 1: ...\\n"}\n'
        '  {"action":"append","path":"plan.md","content":"## Step 2\\n..."}\n'
        '  {"action":"read","path":"plan.md"}\n'
        '  {"action":"list"}'
    )

    def __init__(self, workspace_out: Path | None = None):
        self._root = (workspace_out or _WORKSPACE_OUT).resolve()

    def _safe_path(self, raw: str) -> Path | None:
        """
        Resolve raw to a path inside self._root.
        Accepts relative paths ("plan.md") and absolute paths that
        start with /workspace/out/ — strips the prefix and treats as relative.
        Returns None if the resolved path escapes self._root.
        """
        # Strip well-known absolute prefixes the model tends to use
        rel = raw.strip()
        for prefix in _ABS_PREFIXES:
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                break
        # Also strip a leading slash after prefix-stripping
        rel = rel.lstrip("/")
        if not rel:
            return None
        try:
            p = (self._root / rel).resolve()
            p.relative_to(self._root)  # raises ValueError if outside root
            return p
        except (ValueError, Exception):
            return None

    def run(self, input_text: str) -> str:
        try:
            params = json.loads(input_text)
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, TypeError, ValueError):
            return (
                'Error: input must be a JSON object. '
                'Example: {"action":"read","path":"plan.md"}'
            )

        action = params.get("action", "").lower()
        rel_path = str(params.get("path", "")).strip()

        # Infer missing action from other fields so one-field omissions self-heal
        if not action:
            if "content" in params and rel_path:
                action = "write"
            elif rel_path:
                action = "read"
            else:
                action = "list"

        if action == "list":
            try:
                self._root.mkdir(parents=True, exist_ok=True)
                files = sorted(self._root.rglob("*"))
                names = [str(f.relative_to(self._root)) for f in files if f.is_file()]
                return "Files in /workspace/out/:\n" + ("\n".join(names) if names else "(empty)")
            except Exception as e:
                return f"Error listing workspace: {e}"

        if action not in ("write", "append", "read"):
            return f'Error: unknown action "{action}". Use: write, append, read, or list.'

        if not rel_path:
            return 'Error: "path" is required. Example: {"action":"read","path":"plan.md"}'

        target = self._safe_path(rel_path)
        if target is None:
            return (
                f'Error: path "{rel_path}" is not allowed. '
                'Use a plain filename like "plan.md" or "notes/step1.md".'
            )

        if action == "read":
            if not target.exists():
                return f"File not found: {rel_path} (use action=list to see available files)"
            try:
                data = target.read_bytes()
                text = data[:_MAX_READ_BYTES].decode(errors="replace")
                truncated = len(data) > _MAX_READ_BYTES
                return text + ("\n\n[truncated — file larger than 32 KB]" if truncated else "")
            except Exception as e:
                return f"Error reading {rel_path}: {e}"

        # write or append
        content = params.get("content", "")
        if not isinstance(content, str):
            return 'Error: "content" must be a string.'
        if len(content.encode()) > _MAX_WRITE_BYTES:
            return f"Error: content exceeds {_MAX_WRITE_BYTES // 1024} KB limit — split into smaller writes."
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if action == "write":
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
            else:  # append
                # Ensure there's a newline separator between existing content and new content
                prefix = ""
                if target.exists() and target.stat().st_size > 0:
                    with open(target, "rb") as f:
                        f.seek(-1, 2)
                        last_byte = f.read(1)
                    if last_byte != b"\n":
                        prefix = "\n"
                with open(target, "a", encoding="utf-8") as f:
                    f.write(prefix + content)
            size = target.stat().st_size
            verb = "Written" if action == "write" else "Appended"
            return f"{verb} to /workspace/out/{target.relative_to(self._root)} ({size} bytes total)."
        except Exception as e:
            return f"Error writing {rel_path}: {e}"
