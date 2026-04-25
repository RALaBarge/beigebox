"""SMB / AD enumeration wrappers (Linux-runnable bits of the lateral surface)."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class Enum4linuxScanTool(SecurityTool):
    name = "enum4linux_scan"
    binary = "enum4linux"
    description = (
        "SMB / RPC enumeration (legacy enum4linux). JSON input:\n"
        "  {\"target\": \"10.0.0.5\", \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        timeout = int(parsed.get("timeout", 600))
        argv = ["enum4linux", "-a", target]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class Enum4linuxNgScanTool(SecurityTool):
    name = "enum4linux_ng_scan"
    binary = "enum4linux-ng"
    description = (
        "Modern Python rewrite of enum4linux with structured output. JSON input:\n"
        "  {\"target\": \"10.0.0.5\", \"username\": \"\", \"password\": \"\", "
        "\"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        user = parsed.get("username", "")
        pwd = parsed.get("password", "")
        if user and not self.safe_arg(user):
            return {"ok": False, "error": "unsafe username"}
        if pwd and not self.safe_arg(pwd):
            return {"ok": False, "error": "unsafe password"}
        timeout = int(parsed.get("timeout", 600))
        argv = ["enum4linux-ng", "-A", "-oJ", "/dev/stdout", target]
        if user:
            argv += ["-u", user]
        if pwd:
            argv += ["-p", pwd]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # enum4linux-ng dumps the JSON inline; capture if parseable
        try:
            out["results"] = json.loads(res.stdout) if res.stdout.strip().startswith("{") else None
        except json.JSONDecodeError:
            pass
        return out


class SmbmapScanTool(SecurityTool):
    name = "smbmap_scan"
    binary = "smbmap"
    description = (
        "SMB share enumeration with permission mapping. JSON input:\n"
        "  {\"target\": \"10.0.0.5\", \"username\": \"\", \"password\": \"\", "
        "\"domain\": \"\", \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        user = parsed.get("username", "")
        pwd = parsed.get("password", "")
        domain = parsed.get("domain", "")
        for label, val in (("username", user), ("password", pwd), ("domain", domain)):
            if val and not self.safe_arg(val):
                return {"ok": False, "error": f"unsafe {label}"}
        timeout = int(parsed.get("timeout", 300))
        argv = ["smbmap", "-H", target]
        if user:
            argv += ["-u", user]
        if pwd:
            argv += ["-p", pwd]
        if domain:
            argv += ["-d", domain]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class NetexecScanTool(SecurityTool):
    name = "netexec_scan"
    binary = "netexec"  # also installed as 'nxc' on some systems
    description = (
        "Successor to crackmapexec. SMB / WinRM / MSSQL / SSH / LDAP enumeration "
        "and credential testing. JSON input:\n"
        "  {\"protocol\": \"smb|winrm|mssql|ssh|ldap\", \"target\": \"10.0.0.5\", "
        "\"username\": \"\", \"password\": \"\", \"hash\": \"\", \"shares\": false, "
        "\"timeout\": 600}\n"
        "REQUIRES authorization=true to confirm you have written permission to test the target."
    )

    PROTOCOLS = {"smb", "winrm", "mssql", "ssh", "ldap", "ftp", "rdp", "vnc"}

    def _run(self, parsed: dict) -> dict:
        if not parsed.get("authorization"):
            return {"ok": False, "error": "set 'authorization': true to confirm you have permission to test the target"}
        proto = str(parsed.get("protocol", "smb"))
        if proto not in self.PROTOCOLS:
            return {"ok": False, "error": f"protocol must be one of {sorted(self.PROTOCOLS)}"}
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        user = parsed.get("username", "")
        pwd = parsed.get("password", "")
        nthash = parsed.get("hash", "")
        shares = bool(parsed.get("shares", False))
        for label, val in (("username", user), ("password", pwd), ("hash", nthash)):
            if val and not self.safe_arg(val):
                return {"ok": False, "error": f"unsafe {label}"}
        timeout = int(parsed.get("timeout", 600))

        # Resolve binary — prefer 'netexec' but fall back to 'nxc'.
        from beigebox.security_mcp._run import which
        binary = "netexec" if which("netexec") else ("nxc" if which("nxc") else None)
        if binary is None:
            return {"ok": False, "error": "neither 'netexec' nor 'nxc' on PATH"}

        argv = [binary, proto, target]
        if user:
            argv += ["-u", user]
        if pwd:
            argv += ["-p", pwd]
        if nthash:
            argv += ["-H", nthash]
        if shares:
            argv += ["--shares"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
