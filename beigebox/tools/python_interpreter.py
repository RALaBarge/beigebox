"""
Python interpreter tool — runs Python code in a bwrap sandbox (TIR pattern).

Tool-Integrated Reasoning: the model generates Python code, it runs here,
stdout/stderr come back as the next observation. Useful for calculations,
data analysis, file parsing, and anything that needs actual computation.

Security model:
  - bwrap sandbox: no network, no /app or /home, read-only /workspace/in,
    writable /workspace/out, tmpfs /tmp, dies with parent process.
  - Code injected via stdin (no tempfile bind needed).
  - 10-second execution limit, 8 KB output cap.
  - Falls back to unsandboxed direct execution only when bwrap is unavailable
    AND the config flag python_interpreter.allow_unsandboxed is True.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from beigebox.config import get_config

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10
_DEFAULT_MAX_OUTPUT = 8 * 1024  # 8 KB


def _extract_code(text: str) -> str:
    """Strip markdown fences and return raw Python code."""
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"<code>(.*?)</code>", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _bwrap_argv() -> list[str]:
    bwrap = shutil.which("bwrap") or "bwrap"
    python = shutil.which("python3") or "/usr/bin/python3"
    return [
        bwrap,
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        # ── read-only runtime ────────────────────────────────────────────
        "--ro-bind",     "/usr",          "/usr",
        "--ro-bind-try", "/bin",          "/bin",
        "--ro-bind-try", "/lib",          "/lib",
        "--ro-bind-try", "/lib64",        "/lib64",
        "--ro-bind-try", "/lib32",        "/lib32",
        "--ro-bind-try", "/usr/local",    "/usr/local",
        # ── kernel virtual filesystems ───────────────────────────────────
        "--proc", "/proc",
        "--dev",  "/dev",
        # ── writable scratch ─────────────────────────────────────────────
        "--tmpfs", "/tmp",
        "--chdir", "/tmp",
        # ── agent workspace ──────────────────────────────────────────────
        "--ro-bind-try", "/app/workspace/in",  "/workspace/in",
        "--bind-try",    "/app/workspace/out", "/workspace/out",
        # ── explicitly NOT mounting: /app /home /root ────────────────────
        python, "-",
    ]


class PythonInterpreterTool:
    """
    Execute Python code in a bwrap sandbox and return stdout/stderr.

    capture_tool_io is enabled so execution results are indexed for later
    retrieval — useful for debugging multi-step data analysis sessions.

    Implements the TIR (Tool-Integrated Reasoning) pattern: the operator
    model can generate Python to crunch numbers, parse files, or produce
    structured output, and gets the result back as an observation.

    Input: Python code (with or without markdown fences).
    Output: stdout + stderr, truncated to max_output bytes.

    The sandbox sees:
      /workspace/in/  — files the user dropped in (read-only)
      /workspace/out/ — write results here; tell the user the filename
    """

    capture_tool_io: bool = True

    description = (
        "Execute Python code and return the output. "
        "Input is Python code (markdown fences optional). "
        "Use print() to see results. "
        "Read files from /workspace/in/, write output to /workspace/out/. "
        "No network access. 10-second time limit."
    )

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT, max_output: int = _DEFAULT_MAX_OUTPUT):
        cfg = get_config()
        pi_cfg = cfg.get("tools", {}).get("python_interpreter", {})
        self._timeout = pi_cfg.get("timeout", timeout)
        self._max_output = pi_cfg.get("max_output_bytes", max_output)
        self._allow_unsandboxed = pi_cfg.get("allow_unsandboxed", False)

        self._bwrap_ok = bool(shutil.which("bwrap"))
        self._python = shutil.which("python3") or "/usr/bin/python3"

        mode = "bwrap sandbox" if self._bwrap_ok else (
            "unsandboxed (bwrap unavailable)" if self._allow_unsandboxed else "disabled (no bwrap)"
        )
        logger.info("PythonInterpreterTool initialised (%s)", mode)

    def run(self, code: str) -> str:
        code = _extract_code(code).strip()
        if not code:
            return "Error: no code provided."

        if self._bwrap_ok:
            argv = _bwrap_argv()
        elif self._allow_unsandboxed:
            logger.warning("PythonInterpreterTool: running unsandboxed — bwrap unavailable")
            argv = [self._python, "-"]
        else:
            return (
                "Python interpreter unavailable: bwrap sandbox not installed. "
                "Install bubblewrap or set tools.python_interpreter.allow_unsandboxed: true."
            )

        try:
            result = subprocess.run(
                argv,
                input=code,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            output = result.stdout
            if result.stderr:
                output = (output + "\n[stderr]\n" + result.stderr).strip() if output else result.stderr

            if not output.strip():
                return f"(no output, exit code {result.returncode})"

            if len(output) > self._max_output:
                output = output[:self._max_output] + f"\n... [truncated at {self._max_output} bytes]"

            return output

        except subprocess.TimeoutExpired:
            return f"Error: execution timed out after {self._timeout}s."
        except Exception as e:
            return f"Error: {e}"
