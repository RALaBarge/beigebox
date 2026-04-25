"""Subdomain enumeration, asset probing, WAF fingerprinting."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class AmassScanTool(SecurityTool):
    name = "amass_scan"
    binary = "amass"
    description = (
        "Passive (and optional active) subdomain enumeration. JSON input:\n"
        "  {\"domain\": \"example.com\", \"active\": false, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        active = bool(parsed.get("active", False))
        timeout = int(parsed.get("timeout", 600))
        argv = ["amass", "enum", "-d", domain, "-json", "/dev/stdout"]
        if active:
            argv.append("-active")
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # amass emits JSONL.
        subs = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                subs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if subs:
            out["subdomains"] = subs
        return out


class SubfinderScanTool(SecurityTool):
    name = "subfinder_scan"
    binary = "subfinder"
    description = (
        "Fast passive subdomain enumeration (ProjectDiscovery). JSON input:\n"
        "  {\"domain\": \"example.com\", \"sources\": [\"crtsh\",\"hackertarget\"], \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        sources = parsed.get("sources") or []
        if sources and not all(self.safe_arg(s) for s in sources):
            return {"ok": False, "error": "unsafe source name"}
        timeout = int(parsed.get("timeout", 300))
        argv = ["subfinder", "-d", domain, "-silent", "-oJ"]
        if sources:
            argv += ["-sources", ",".join(sources)]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        subs = [
            json.loads(l) for l in res.stdout.splitlines()
            if l.strip().startswith("{")
        ]
        if subs:
            out["subdomains"] = subs
        return out


class HttpxProbeTool(SecurityTool):
    name = "httpx_probe"
    binary = "httpx"
    description = (
        "HTTP probing + tech detection (ProjectDiscovery httpx, NOT Python httpx).\n"
        "JSON input: {\"targets\": [\"https://example.com\", ...], "
        "\"tech_detect\": true, \"status_code\": true, \"timeout\": 120}\n"
        "Single target also accepted as {\"target\": \"...\"}."
    )

    def _run(self, parsed: dict) -> dict:
        targets = parsed.get("targets")
        if not targets and parsed.get("target"):
            targets = [parsed["target"]]
        if not isinstance(targets, list) or not targets:
            return {"ok": False, "error": "targets must be a non-empty list (or pass single 'target')"}
        for t in targets:
            if not self.safe_target(t, allow_url=True):
                return {"ok": False, "error": f"invalid target: {t}"}
        tech = bool(parsed.get("tech_detect", True))
        status = bool(parsed.get("status_code", True))
        timeout = int(parsed.get("timeout", 120))

        argv = ["httpx", "-silent", "-json", "-no-color"]
        if tech:
            argv.append("-tech-detect")
        if status:
            argv.append("-status-code")

        # feed targets via stdin to keep them out of argv length limits + safe
        import subprocess, time
        from beigebox.security_mcp._run import which, RunResult
        resolved = which("httpx")
        if resolved is None:
            return {"ok": False, "error": "binary 'httpx' not on PATH (this is ProjectDiscovery's httpx, not the Python package)"}
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [resolved, *argv[1:]],
                input="\n".join(targets),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s"}
        elapsed = time.monotonic() - start
        rows = [json.loads(l) for l in (proc.stdout or "").splitlines() if l.strip().startswith("{")]
        return {
            "ok": proc.returncode == 0,
            "binary": "httpx",
            "duration_s": round(elapsed, 2),
            "returncode": proc.returncode,
            "results": rows,
            "stderr": (proc.stderr or "")[:8000],
        }


class WafW00fScanTool(SecurityTool):
    name = "wafw00f_scan"
    binary = "wafw00f"
    description = (
        "Identify WAF in front of a URL. JSON input:\n"
        "  {\"target\": \"https://example.com\", \"timeout\": 60}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target, allow_url=True):
            return {"ok": False, "error": "invalid target"}
        timeout = int(parsed.get("timeout", 60))
        argv = ["wafw00f", "-a", "-o", "-", target]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
