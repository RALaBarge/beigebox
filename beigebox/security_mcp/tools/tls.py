"""SSL/TLS / SSH configuration audit wrappers."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class TestsslScanTool(SecurityTool):
    name = "testssl_scan"
    binary = "testssl.sh"  # also installed as just 'testssl' on some systems
    description = (
        "Comprehensive TLS / cipher / cert audit (testssl.sh). JSON input:\n"
        "  {\"target\": \"example.com:443\", \"severity\": \"LOW|MEDIUM|HIGH\", "
        "\"timeout\": 1200}\n"
        "severity filters output to issues at or above the threshold."
    )

    SEVERITY = {"LOW", "MEDIUM", "HIGH", "CRITICAL", "WARN", "INFO"}

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target (host[:port])"}
        severity = str(parsed.get("severity", "LOW"))
        if severity not in self.SEVERITY:
            return {"ok": False, "error": f"severity must be one of {sorted(self.SEVERITY)}"}
        timeout = int(parsed.get("timeout", 1200))

        from beigebox.security_mcp._run import which
        binary = "testssl.sh" if which("testssl.sh") else ("testssl" if which("testssl") else None)
        if binary is None:
            return {"ok": False, "error": "neither 'testssl.sh' nor 'testssl' on PATH"}
        argv = [binary, "--quiet", "--color", "0", "--severity", severity,
                "--jsonfile", "/dev/stdout", target]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["findings"] = json.loads(res.stdout)
            out["stdout"] = "(json parsed)"
        except json.JSONDecodeError:
            pass
        return out


class SslscanScanTool(SecurityTool):
    name = "sslscan_scan"
    binary = "sslscan"
    description = (
        "Quick TLS cipher / cert dump. JSON input:\n"
        "  {\"target\": \"example.com:443\", \"timeout\": 120}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        timeout = int(parsed.get("timeout", 120))
        argv = ["sslscan", "--xml=/dev/stdout", "--no-colour", target]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class SshAuditScanTool(SecurityTool):
    name = "ssh_audit_scan"
    binary = "ssh-audit"
    description = (
        "Audit SSH server config: ciphers, kex, MAC, host-key algos, CVEs. JSON input:\n"
        "  {\"target\": \"example.com\", \"port\": 22, \"timeout\": 60}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        port = int(parsed.get("port", 22))
        timeout = int(parsed.get("timeout", 60))
        argv = ["ssh-audit", "-p", str(port), "-j", target]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["audit"] = json.loads(res.stdout)
            out["stdout"] = "(json parsed)"
        except json.JSONDecodeError:
            pass
        return out
