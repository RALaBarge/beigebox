"""Additional ProjectDiscovery tools (naabu, dnsx)."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class NaabuScanTool(SecurityTool):
    name = "naabu_scan"
    binary = "naabu"
    description = (
        "Fast SYN port scanner (ProjectDiscovery). JSON input:\n"
        "  {\"target\": \"example.com\", \"ports\": \"top-1000\", "
        "\"rate\": 1000, \"timeout\": 600}\n"
        "ports accepts the same syntax as nmap, plus 'top-100', 'top-1000', '-' for all."
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        ports = str(parsed.get("ports", "top-1000"))
        if not self.safe_arg(ports):
            return {"ok": False, "error": "unsafe ports"}
        rate = int(parsed.get("rate", 1000))
        timeout = int(parsed.get("timeout", 600))
        argv = ["naabu", "-host", target, "-rate", str(rate),
                "-silent", "-json", "-no-color"]
        if ports.startswith("top-"):
            argv += ["-top-ports", ports.split("-", 1)[1]]
        else:
            argv += ["-p", ports]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        rows = [json.loads(l) for l in res.stdout.splitlines() if l.strip().startswith("{")]
        if rows:
            out["open_ports"] = rows
            out["port_count"] = len(rows)
        return out


class DnsxResolveTool(SecurityTool):
    name = "dnsx_resolve"
    binary = "dnsx"
    description = (
        "Bulk DNS resolver / probe (ProjectDiscovery). JSON input:\n"
        "  {\"domains\": [\"a.example.com\",\"b.example.com\"], "
        "\"record_types\": \"A,AAAA,CNAME\", \"timeout\": 120}\n"
        "Single host also accepted as {\"domain\": \"...\"}."
    )

    def _run(self, parsed: dict) -> dict:
        domains = parsed.get("domains")
        if not domains and parsed.get("domain"):
            domains = [parsed["domain"]]
        if not isinstance(domains, list) or not domains:
            return {"ok": False, "error": "domains must be a non-empty list"}
        for d in domains:
            if not self.safe_target(d):
                return {"ok": False, "error": f"invalid domain: {d}"}
        rtypes = str(parsed.get("record_types", "A,AAAA,CNAME"))
        if not self.safe_arg(rtypes):
            return {"ok": False, "error": "unsafe record_types"}
        timeout = int(parsed.get("timeout", 120))

        argv = ["dnsx", "-silent", "-json", "-no-color", "-resp", "-t", rtypes]
        res = run_argv(argv, timeout=timeout, stdin="\n".join(domains))
        out = json.loads(res.to_json_str())
        rows = [json.loads(l) for l in res.stdout.splitlines() if l.strip().startswith("{")]
        if rows:
            out["results"] = rows
            out["result_count"] = len(rows)
        return out
