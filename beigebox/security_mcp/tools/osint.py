"""OSINT, exploit lookup, and miscellaneous wrappers."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class WhatwebScanTool(SecurityTool):
    name = "whatweb_scan"
    binary = "whatweb"
    description = (
        "Web technology fingerprinting. JSON input:\n"
        "  {\"target\": \"https://example.com\", \"aggression\": 1, \"timeout\": 120}\n"
        "aggression: 1=stealthy, 3=aggressive, 4=heavy."
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target, allow_url=True):
            return {"ok": False, "error": "invalid target"}
        aggression = int(parsed.get("aggression", 1))
        if aggression not in (1, 3, 4):
            return {"ok": False, "error": "aggression must be 1|3|4"}
        timeout = int(parsed.get("timeout", 120))
        argv = ["whatweb", "-a", str(aggression), "--no-errors",
                "--log-json=/dev/stdout", target]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # whatweb emits one JSON doc per line.
        rows = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("["):
                # Sometimes a JSON array
                try:
                    rows.extend(json.loads(line))
                except json.JSONDecodeError:
                    pass
            elif line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if rows:
            out["fingerprints"] = rows
        return out


class SearchsploitLookupTool(SecurityTool):
    name = "searchsploit_lookup"
    binary = "searchsploit"
    description = (
        "Search the local Exploit-DB mirror by keyword / CVE. JSON input:\n"
        "  {\"query\": \"apache 2.4\", \"json\": true, \"timeout\": 30}"
    )

    def _run(self, parsed: dict) -> dict:
        query = str(parsed.get("query", "")).strip()
        if not query:
            return {"ok": False, "error": "query required"}
        # query can contain spaces; pass each whitespace-token as its own argv element.
        terms = query.split()
        if not all(self.safe_arg(t) for t in terms):
            return {"ok": False, "error": "unsafe term in query"}
        timeout = int(parsed.get("timeout", 30))
        argv = ["searchsploit"]
        if parsed.get("json", True):
            argv.append("--json")
        argv += terms
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["results"] = json.loads(res.stdout)
            out["stdout"] = "(json parsed)"
        except json.JSONDecodeError:
            pass
        return out


class TheharvesterScanTool(SecurityTool):
    name = "theharvester_scan"
    binary = "theHarvester"  # also installed as 'theharvester' on some systems
    description = (
        "OSINT email / subdomain / employee gathering from search engines. JSON input:\n"
        "  {\"domain\": \"example.com\", \"sources\": \"crtsh,duckduckgo,bing\", "
        "\"limit\": 500, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        sources = str(parsed.get("sources", "crtsh,duckduckgo,bing"))
        if not self.safe_arg(sources):
            return {"ok": False, "error": "unsafe sources"}
        limit = int(parsed.get("limit", 500))
        timeout = int(parsed.get("timeout", 600))

        from beigebox.security_mcp._run import which_any
        _, binary = which_any("theHarvester", "theharvester")
        if binary is None:
            return {"ok": False, "error": "neither 'theHarvester' nor 'theharvester' on PATH"}
        argv = [binary, "-d", domain, "-b", sources, "-l", str(limit), "-f", "/dev/stdout"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class CewlWordlistGenTool(SecurityTool):
    name = "cewl_wordlist_gen"
    binary = "cewl"
    description = (
        "Generate a wordlist by spidering a site. JSON input:\n"
        "  {\"url\": \"https://example.com\", \"depth\": 2, \"min_length\": 5, "
        "\"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        depth = int(parsed.get("depth", 2))
        minlen = int(parsed.get("min_length", 5))
        timeout = int(parsed.get("timeout", 600))
        argv = ["cewl", "-d", str(depth), "-m", str(minlen), url]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        words = [w.strip() for w in (res.stdout or "").splitlines() if w.strip()]
        if words:
            out["words"] = words
            out["word_count"] = len(words)
        return out


class MsfvenomGenerateTool(SecurityTool):
    name = "msfvenom_generate"
    binary = "msfvenom"
    requires_auth = True
    description = (
        "Generate a Metasploit payload (one-shot, non-interactive). JSON input:\n"
        "  {\"payload\": \"linux/x64/meterpreter/reverse_tcp\", "
        "\"lhost\": \"10.0.0.5\", \"lport\": 4444, \"format\": \"elf\", "
        "\"out_path\": \"/tmp/payload.bin\", \"options\": {}, \"timeout\": 120, "
        "\"authorization\": true}\n"
        "options is a flat dict of additional KEY=VALUE pairs."
    )

    def _run(self, parsed: dict) -> dict:
        payload = str(parsed.get("payload", ""))
        if not payload or not self.safe_arg(payload):
            return {"ok": False, "error": "payload required and must be safe"}
        lhost = parsed.get("lhost", "")
        if lhost and not self.safe_target(lhost):
            return {"ok": False, "error": "invalid lhost"}
        lport = int(parsed.get("lport", 4444))
        fmt = str(parsed.get("format", "elf"))
        if not self.safe_arg(fmt):
            return {"ok": False, "error": "unsafe format"}
        out_path = parsed.get("out_path", "")
        if out_path and not self.safe_path(out_path, must_exist=False, forbid_traversal=True):
            return {"ok": False, "error": "unsafe out_path (no shell metachars, no '..' segments)"}
        options = parsed.get("options") or {}
        if not isinstance(options, dict):
            return {"ok": False, "error": "options must be a dict"}
        timeout = int(parsed.get("timeout", 120))

        argv = ["msfvenom", "-p", payload, "-f", fmt]
        if lhost:
            argv.append(f"LHOST={lhost}")
        if lport:
            argv.append(f"LPORT={lport}")
        for k, v in options.items():
            if not (self.safe_arg(str(k)) and self.safe_arg(str(v))):
                return {"ok": False, "error": f"unsafe option {k}={v}"}
            argv.append(f"{k}={v}")
        if out_path:
            argv += ["-o", out_path]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
