"""
RepoTool — generic code repository exploration for agentic workflows.

Exposes read, search, list, git log, and git diff as a single MCP tool.
Works on any path on the filesystem — no fixed repo root. Useful for
garlicpress agentic map agents, operator code tasks, or Claude Desktop.

Input (JSON string):

  Read a file:
    {"action": "read_file", "path": "/abs/path/to/file.py"}
    {"action": "read_file", "path": "relative/to/root", "root": "/repo"}

  Search (ripgrep):
    {"action": "search", "pattern": "class Proxy", "root": "/repo"}
    {"action": "search", "pattern": "TODO", "root": "/repo", "glob": "*.py", "context": 2}

  List source files:
    {"action": "list_files", "root": "/repo"}
    {"action": "list_files", "root": "/repo", "extensions": [".py", ".ts"], "max": 200}

  Git log:
    {"action": "git_log", "root": "/repo"}
    {"action": "git_log", "root": "/repo", "path": "beigebox/proxy.py", "n": 20}

  Git diff:
    {"action": "git_diff", "root": "/repo"}
    {"action": "git_diff", "root": "/repo", "ref": "HEAD~1"}
    {"action": "git_diff", "root": "/repo", "ref": "HEAD~1", "path": "beigebox/proxy.py"}
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "repo"
PLUGIN_Z_ALIASES = {}   # no z-command alias — purely tool/MCP surface

_MAX_READ_BYTES = 32_000   # ~8k tokens
_MAX_OUTPUT     = 16_000   # search / log / diff cap

_DEFAULT_EXTENSIONS = {".py", ".ts", ".js", ".go", ".rs", ".java", ".md", ".yaml", ".toml"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build",
              ".mypy_cache", ".claude", "findings"}


class RepoTool:
    description = (
        "Explore any code repository on the filesystem. "
        "Input MUST be a JSON object with 'action' and 'root' (absolute path to repo). "
        "Actions:\n"
        "  read_file  — read a file: {\"action\":\"read_file\",\"path\":\"/abs/path\"}\n"
        "  search     — ripgrep search: {\"action\":\"search\",\"pattern\":\"class Foo\",\"root\":\"/repo\"}\n"
        "               optional: glob (e.g. '*.py'), context (lines), max_results\n"
        "  list_files — list source files: {\"action\":\"list_files\",\"root\":\"/repo\"}\n"
        "               optional: extensions (['.py','.ts']), max (default 300)\n"
        "  git_log    — git history: {\"action\":\"git_log\",\"root\":\"/repo\"}\n"
        "               optional: path (file), n (count, default 20)\n"
        "  git_diff   — git diff: {\"action\":\"git_diff\",\"root\":\"/repo\"}\n"
        "               optional: ref (e.g. 'HEAD~1'), path (file)"
    )

    def run(self, input_text: str) -> str:
        try:
            params = json.loads(input_text) if isinstance(input_text, str) else input_text
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, ValueError):
            return 'Error: input must be a JSON object. Example: {"action":"list_files","root":"/repo"}'

        action = params.get("action", "").strip()
        dispatch = {
            "read_file":  self._read_file,
            "search":     self._search,
            "list_files": self._list_files,
            "git_log":    self._git_log,
            "git_diff":   self._git_diff,
        }
        fn = dispatch.get(action)
        if fn is None:
            return (
                f'Error: unknown action "{action}". '
                f'Available: {", ".join(dispatch)}'
            )
        try:
            return fn(params)
        except Exception as e:
            logger.error("RepoTool.%s error: %s", action, e)
            return f"Error in {action}: {e}"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _read_file(self, p: dict) -> str:
        path_str = p.get("path", "").strip()
        if not path_str:
            return 'Error: "path" required for read_file'

        root_str = p.get("root", "").strip()
        if root_str and not Path(path_str).is_absolute():
            target = (Path(root_str) / path_str).resolve()
        else:
            target = Path(path_str).resolve()

        if not target.exists():
            return f"File not found: {target}"
        if not target.is_file():
            return f"Not a file: {target}"

        try:
            data = target.read_bytes()
            text = data[:_MAX_READ_BYTES].decode(errors="replace")
            truncated = len(data) > _MAX_READ_BYTES
            suffix = f"\n\n[truncated — showing first {_MAX_READ_BYTES // 1000}KB of {len(data) // 1000}KB]" if truncated else ""
            return f"# {target}\n\n{text}{suffix}"
        except Exception as e:
            return f"Error reading {target}: {e}"

    def _search(self, p: dict) -> str:
        pattern = p.get("pattern", "").strip()
        if not pattern:
            return 'Error: "pattern" required for search'

        root = p.get("root", ".").strip() or "."
        glob_pat = p.get("glob", "")
        context = int(p.get("context", 0))
        max_results = int(p.get("max_results", 50))

        # Prefer ripgrep, fall back to grep -r
        import shutil
        if shutil.which("rg"):
            cmd = ["rg", "--color=never", "-n", f"-m{max_results}"]
            if glob_pat:
                cmd += ["--glob", glob_pat]
            if context > 0:
                cmd += [f"-C{context}"]
            cmd += [pattern, root]
        else:
            cmd = ["grep", "-r", "--color=never", "-n"]
            if context > 0:
                cmd += [f"-C{context}"]
            if glob_pat:
                # grep --include takes shell glob, not rg glob
                cmd += [f"--include={glob_pat}"]
            cmd += [pattern, root]

        return self._run_cmd(cmd, cwd=root)

    def _list_files(self, p: dict) -> str:
        root_str = p.get("root", ".").strip() or "."
        root = Path(root_str).resolve()
        if not root.is_dir():
            return f"Not a directory: {root}"

        exts_raw = p.get("extensions", [])
        exts = set(exts_raw) if exts_raw else _DEFAULT_EXTENSIONS
        max_files = int(p.get("max", 300))

        files: list[str] = []
        for path in sorted(root.rglob("*")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix in exts:
                files.append(str(path.relative_to(root)))
            if len(files) >= max_files:
                files.append(f"... (truncated at {max_files} files)")
                break

        return f"# Files in {root} ({len(files)} shown)\n\n" + "\n".join(files)

    def _git_log(self, p: dict) -> str:
        root = p.get("root", ".").strip() or "."
        file_path = p.get("path", "").strip()
        n = int(p.get("n", 20))

        cmd = ["git", "log", f"-{n}", "--oneline", "--decorate"]
        if file_path:
            cmd += ["--", file_path]

        return self._run_cmd(cmd, cwd=root)

    def _git_diff(self, p: dict) -> str:
        root = p.get("root", ".").strip() or "."
        ref = p.get("ref", "HEAD").strip() or "HEAD"
        file_path = p.get("path", "").strip()

        cmd = ["git", "diff", ref]
        if file_path:
            cmd += ["--", file_path]

        return self._run_cmd(cmd, cwd=root)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_cmd(self, cmd: list[str], cwd: str = ".") -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                cwd=cwd,
            )
            out = result.stdout or result.stderr or "(no output)"
            if len(out) > _MAX_OUTPUT:
                out = out[:_MAX_OUTPUT] + f"\n\n[truncated at {_MAX_OUTPUT // 1000}KB]"
            return out
        except FileNotFoundError:
            return f"Error: command not found — {cmd[0]}"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after 15s — {' '.join(cmd[:3])}"
        except Exception as e:
            return f"Error: {e}"
