"""Binary / firmware / forensic analysis wrappers."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class BinwalkAnalyzeTool(SecurityTool):
    name = "binwalk_analyze"
    binary = "binwalk"
    description = (
        "Firmware / blob analysis (signature scan, optional extraction). JSON input:\n"
        "  {\"file_path\": \"/path/to/firmware.bin\", \"extract\": false, "
        "\"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        path = parsed.get("file_path", "")
        if not self.safe_path(path):
            return {"ok": False, "error": "file_path invalid or does not exist"}
        extract = bool(parsed.get("extract", False))
        timeout = int(parsed.get("timeout", 600))
        argv = ["binwalk"]
        if extract:
            argv.append("-e")
        argv.append(path)
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class ExiftoolExtractTool(SecurityTool):
    name = "exiftool_extract"
    binary = "exiftool"
    description = (
        "Extract metadata from any file (image, PDF, document, audio, …). JSON input:\n"
        "  {\"file_path\": \"/path/to/file\", \"format\": \"json|short|html\", "
        "\"timeout\": 60}"
    )

    def _run(self, parsed: dict) -> dict:
        path = parsed.get("file_path", "")
        if not self.safe_path(path):
            return {"ok": False, "error": "file_path invalid or does not exist"}
        fmt = str(parsed.get("format", "json"))
        if fmt not in ("json", "short", "html"):
            return {"ok": False, "error": "format must be json|short|html"}
        timeout = int(parsed.get("timeout", 60))
        argv = ["exiftool"]
        if fmt == "json":
            argv.append("-json")
        elif fmt == "short":
            argv.append("-S")
        elif fmt == "html":
            argv.append("-h")
        argv.append(path)
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        if fmt == "json" and res.ok:
            try:
                out["metadata"] = json.loads(res.stdout)
                out["stdout"] = "(json parsed)"
            except json.JSONDecodeError:
                pass
        return out


class ChecksecAnalyzeTool(SecurityTool):
    name = "checksec_analyze"
    binary = "checksec"
    description = (
        "Check ELF binary protections (NX, PIE, RELRO, canary, fortify). JSON input:\n"
        "  {\"binary\": \"/path/to/elf\", \"timeout\": 30}"
    )

    def _run(self, parsed: dict) -> dict:
        path = parsed.get("binary", "")
        if not self.safe_path(path):
            return {"ok": False, "error": "binary path invalid or does not exist"}
        timeout = int(parsed.get("timeout", 30))
        argv = ["checksec", "--file", path, "--output", "json"]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        if res.ok:
            try:
                out["protections"] = json.loads(res.stdout)
                out["stdout"] = "(json parsed)"
            except json.JSONDecodeError:
                pass
        return out
