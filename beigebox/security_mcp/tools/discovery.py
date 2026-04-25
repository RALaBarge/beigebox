"""URL / parameter discovery wrappers (passive intel from Wayback, OTX, etc.)."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class GauDiscoveryTool(SecurityTool):
    name = "gau_discovery"
    binary = "gau"
    description = (
        "Get-all-URLs from Wayback Machine, CommonCrawl, OTX, URLScan. JSON input:\n"
        "  {\"domain\": \"example.com\", \"providers\": \"wayback,commoncrawl,otx,urlscan\", "
        "\"include_subs\": true, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        providers = str(parsed.get("providers", "wayback,commoncrawl,otx,urlscan"))
        if not self.safe_arg(providers):
            return {"ok": False, "error": "unsafe providers"}
        include_subs = bool(parsed.get("include_subs", True))
        timeout = int(parsed.get("timeout", 600))

        # gau reads domains from stdin; safer than positional arg.
        import subprocess, time
        from beigebox.security_mcp._run import which
        resolved = which("gau")
        if resolved is None:
            return {"ok": False, "error": "binary 'gau' not on PATH"}
        argv = [resolved, "--providers", providers, "--threads", "5"]
        if include_subs:
            argv.append("--subs")
        start = time.monotonic()
        try:
            proc = subprocess.run(argv, input=domain, capture_output=True,
                                  text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s"}
        urls = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
        return {
            "ok": proc.returncode == 0,
            "binary": "gau",
            "duration_s": round(time.monotonic() - start, 2),
            "url_count": len(urls),
            "urls": urls[:10000],  # cap response size
            "stderr": (proc.stderr or "")[:4000],
        }


class WaybackurlsDiscoveryTool(SecurityTool):
    name = "waybackurls_discovery"
    binary = "waybackurls"
    description = (
        "Dump every URL the Wayback Machine has seen for a domain. JSON input:\n"
        "  {\"domain\": \"example.com\", \"include_subs\": true, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        include_subs = bool(parsed.get("include_subs", True))
        timeout = int(parsed.get("timeout", 600))

        import subprocess, time
        from beigebox.security_mcp._run import which
        resolved = which("waybackurls")
        if resolved is None:
            return {"ok": False, "error": "binary 'waybackurls' not on PATH"}
        argv = [resolved]
        if not include_subs:
            argv.append("-no-subs")
        start = time.monotonic()
        try:
            proc = subprocess.run(argv, input=domain, capture_output=True,
                                  text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s"}
        urls = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
        return {
            "ok": proc.returncode == 0,
            "binary": "waybackurls",
            "duration_s": round(time.monotonic() - start, 2),
            "url_count": len(urls),
            "urls": urls[:10000],
            "stderr": (proc.stderr or "")[:4000],
        }


class ArjunParameterDiscoveryTool(SecurityTool):
    name = "arjun_parameter_discovery"
    binary = "arjun"
    description = (
        "HTTP parameter discovery — finds hidden GET/POST params. JSON input:\n"
        "  {\"url\": \"https://example.com/api\", \"method\": \"GET|POST\", "
        "\"threads\": 10, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        method = str(parsed.get("method", "GET"))
        if method not in ("GET", "POST", "JSON", "XML"):
            return {"ok": False, "error": "method must be GET|POST|JSON|XML"}
        threads = int(parsed.get("threads", 10))
        timeout = int(parsed.get("timeout", 600))
        argv = ["arjun", "-u", url, "-m", method, "-t", str(threads),
                "--stable", "-oJ", "/dev/stdout"]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["results"] = json.loads(res.stdout) if res.stdout.strip() else {}
        except json.JSONDecodeError:
            pass
        return out


class ParamspiderMiningTool(SecurityTool):
    name = "paramspider_mining"
    binary = "paramspider"
    description = (
        "Mine parameters from archive URLs. JSON input:\n"
        "  {\"domain\": \"example.com\", \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        timeout = int(parsed.get("timeout", 300))
        argv = ["paramspider", "-d", domain, "--quiet"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class HakrawlerCrawlTool(SecurityTool):
    name = "hakrawler_crawl"
    binary = "hakrawler"
    description = (
        "Fast Go-based web crawler. JSON input:\n"
        "  {\"url\": \"https://example.com\", \"depth\": 2, "
        "\"include_subs\": false, \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        depth = int(parsed.get("depth", 2))
        include_subs = bool(parsed.get("include_subs", False))
        timeout = int(parsed.get("timeout", 300))

        import subprocess, time
        from beigebox.security_mcp._run import which
        resolved = which("hakrawler")
        if resolved is None:
            return {"ok": False, "error": "binary 'hakrawler' not on PATH"}
        argv = [resolved, "-d", str(depth)]
        if include_subs:
            argv.append("-subs")
        start = time.monotonic()
        try:
            proc = subprocess.run(argv, input=url, capture_output=True,
                                  text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s"}
        urls = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
        return {
            "ok": proc.returncode == 0,
            "binary": "hakrawler",
            "duration_s": round(time.monotonic() - start, 2),
            "url_count": len(urls),
            "urls": urls[:10000],
        }
