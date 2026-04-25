"""Web vuln scanners and content discovery."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class NucleiScanTool(SecurityTool):
    name = "nuclei_scan"
    binary = "nuclei"
    description = (
        "Template-based vulnerability scanner. JSON input:\n"
        "  {\"target\": \"https://example.com\", \"severity\": \"critical,high\", "
        "\"tags\": \"cve,oast\", \"templates\": [\"http/cves/\"], \"timeout\": 1200}\n"
        "Returns per-finding JSONL parsed to a 'findings' array."
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target, allow_url=True):
            return {"ok": False, "error": "invalid target"}
        severity = parsed.get("severity")
        tags = parsed.get("tags")
        templates = parsed.get("templates") or []
        timeout = int(parsed.get("timeout", 1200))

        argv = ["nuclei", "-u", target, "-jsonl", "-silent", "-no-color"]
        if severity:
            if not self.safe_arg(str(severity)):
                return {"ok": False, "error": "unsafe severity"}
            argv += ["-s", str(severity)]
        if tags:
            if not self.safe_arg(str(tags)):
                return {"ok": False, "error": "unsafe tags"}
            argv += ["-tags", str(tags)]
        for t in templates:
            if not self.safe_arg(t):
                return {"ok": False, "error": f"unsafe template path: {t}"}
            argv += ["-t", t]

        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        findings = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                findings.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        out["findings"] = findings
        out["finding_count"] = len(findings)
        # Drop bulky raw stdout once parsed.
        if findings:
            out["stdout"] = f"({len(findings)} findings parsed)"
        return out


class FfufScanTool(SecurityTool):
    name = "ffuf_scan"
    binary = "ffuf"
    description = (
        "Fast web fuzzer (dir, vhost, param). JSON input:\n"
        "  {\"url\": \"https://example.com/FUZZ\", "
        "\"wordlist\": \"/usr/share/wordlists/dirb/common.txt\", "
        "\"match_codes\": \"200,204,301,302,307,401,403\", \"threads\": 40, "
        "\"timeout\": 600}\n"
        "URL must contain the 'FUZZ' marker."
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        if "FUZZ" not in url:
            return {"ok": False, "error": "url must contain a FUZZ marker"}
        wordlist = parsed.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
        if not self.safe_arg(wordlist):
            return {"ok": False, "error": "unsafe wordlist path"}
        match = str(parsed.get("match_codes", "200,204,301,302,307,401,403"))
        if not self.safe_arg(match):
            return {"ok": False, "error": "unsafe match_codes"}
        threads = int(parsed.get("threads", 40))
        timeout = int(parsed.get("timeout", 600))

        argv = [
            "ffuf", "-u", url, "-w", wordlist,
            "-mc", match, "-t", str(threads),
            "-of", "json", "-o", "/dev/stdout", "-s",
        ]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # ffuf's -of json writes a single JSON doc to the file.
        try:
            doc = json.loads(res.stdout)
            out["results"] = doc.get("results", [])
            out["result_count"] = len(out["results"])
            out["stdout"] = f"({out['result_count']} results parsed)"
        except json.JSONDecodeError:
            pass
        return out


class GobusterScanTool(SecurityTool):
    name = "gobuster_scan"
    binary = "gobuster"
    description = (
        "Brute-force directories / DNS / vhosts. JSON input:\n"
        "  {\"mode\": \"dir|dns|vhost\", \"url\": \"https://example.com\", "
        "\"wordlist\": \"/usr/share/wordlists/dirb/common.txt\", "
        "\"threads\": 30, \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        mode = str(parsed.get("mode", "dir"))
        if mode not in ("dir", "dns", "vhost"):
            return {"ok": False, "error": "mode must be dir|dns|vhost"}
        target = parsed.get("url", "") if mode != "dns" else parsed.get("domain", "")
        allow_url = mode != "dns"
        if not self.safe_target(target, allow_url=allow_url):
            return {"ok": False, "error": "invalid target"}
        wordlist = parsed.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
        if not self.safe_arg(wordlist):
            return {"ok": False, "error": "unsafe wordlist"}
        threads = int(parsed.get("threads", 30))
        timeout = int(parsed.get("timeout", 600))

        flag = "-u" if allow_url else "-d"
        argv = ["gobuster", mode, flag, target, "-w", wordlist, "-t", str(threads), "-q"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class NiktoScanTool(SecurityTool):
    name = "nikto_scan"
    binary = "nikto"
    description = (
        "Classic web server scanner. JSON input:\n"
        "  {\"target\": \"https://example.com\", \"timeout\": 1200}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target, allow_url=True):
            return {"ok": False, "error": "invalid target"}
        timeout = int(parsed.get("timeout", 1200))
        argv = ["nikto", "-h", target, "-Format", "json", "-o", "/dev/stdout", "-ask", "no"]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            doc = json.loads(res.stdout)
            out["results"] = doc
            out["stdout"] = "(json parsed)"
        except json.JSONDecodeError:
            pass
        return out


class SqlmapScanTool(SecurityTool):
    name = "sqlmap_scan"
    binary = "sqlmap"
    requires_auth = True  # extra flags can include --os-shell etc → RCE
    description = (
        "SQL injection detection / exploitation. JSON input:\n"
        "  {\"url\": \"https://example.com/page?id=1\", \"level\": 1, "
        "\"risk\": 1, \"batch\": true, \"timeout\": 1800, \"extra\": [], "
        "\"authorization\": true}\n"
        "REQUIRES authorization=true — extra args allow flags like --os-shell "
        "that escalate to RCE on the target."
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        level = int(parsed.get("level", 1))
        risk = int(parsed.get("risk", 1))
        batch = bool(parsed.get("batch", True))
        timeout = int(parsed.get("timeout", 1800))
        extra = parsed.get("extra") or []
        if not all(self.safe_arg(a) for a in extra):
            return {"ok": False, "error": "unsafe extra arg"}
        argv = ["sqlmap", "-u", url, "--level", str(level), "--risk", str(risk),
                "--disable-coloring"]
        if batch:
            argv.append("--batch")
        argv += extra
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class KatanaCrawlTool(SecurityTool):
    name = "katana_crawl"
    binary = "katana"
    description = (
        "Headless JS-aware web crawler (ProjectDiscovery). JSON input:\n"
        "  {\"url\": \"https://example.com\", \"depth\": 3, \"js_crawl\": true, "
        "\"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        depth = int(parsed.get("depth", 3))
        js = bool(parsed.get("js_crawl", True))
        timeout = int(parsed.get("timeout", 600))
        argv = ["katana", "-u", url, "-d", str(depth), "-jsonl", "-silent", "-no-color"]
        if js:
            argv.append("-jc")
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        urls = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    urls.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if urls:
            out["urls"] = urls
            out["url_count"] = len(urls)
            out["stdout"] = f"({len(urls)} URLs crawled)"
        return out


class FeroxbusterScanTool(SecurityTool):
    name = "feroxbuster_scan"
    binary = "feroxbuster"
    description = (
        "Recursive content discovery. JSON input:\n"
        "  {\"url\": \"https://example.com\", "
        "\"wordlist\": \"/usr/share/wordlists/dirb/common.txt\", "
        "\"depth\": 4, \"threads\": 50, \"timeout\": 900}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        wordlist = parsed.get("wordlist", "/usr/share/wordlists/dirb/common.txt")
        if not self.safe_arg(wordlist):
            return {"ok": False, "error": "unsafe wordlist"}
        depth = int(parsed.get("depth", 4))
        threads = int(parsed.get("threads", 50))
        timeout = int(parsed.get("timeout", 900))
        argv = ["feroxbuster", "-u", url, "-w", wordlist,
                "-d", str(depth), "-t", str(threads), "--silent", "--json"]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        results = [json.loads(l) for l in res.stdout.splitlines() if l.strip().startswith("{")]
        if results:
            out["results"] = results
            out["result_count"] = len(results)
        return out


class DirsearchScanTool(SecurityTool):
    name = "dirsearch_scan"
    binary = "dirsearch"
    description = (
        "Python directory brute-forcer. JSON input:\n"
        "  {\"url\": \"https://example.com\", \"extensions\": \"php,html,js\", "
        "\"threads\": 30, \"timeout\": 900}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        ext = str(parsed.get("extensions", "php,html,js,txt"))
        if not self.safe_arg(ext):
            return {"ok": False, "error": "unsafe extensions"}
        threads = int(parsed.get("threads", 30))
        timeout = int(parsed.get("timeout", 900))
        argv = ["dirsearch", "-u", url, "-e", ext,
                "-t", str(threads), "--format", "json", "-o", "/dev/stdout", "--quiet"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class DalfoxXssScanTool(SecurityTool):
    name = "dalfox_xss_scan"
    binary = "dalfox"
    description = (
        "XSS vulnerability scanner. JSON input:\n"
        "  {\"url\": \"https://example.com/search?q=test\", "
        "\"mode\": \"url|pipe|file\", \"timeout\": 600}"
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        mode = str(parsed.get("mode", "url"))
        if mode not in ("url", "pipe", "file"):
            return {"ok": False, "error": "mode must be url|pipe|file"}
        timeout = int(parsed.get("timeout", 600))
        argv = ["dalfox", mode, url, "--format", "json", "--silence"]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["results"] = json.loads(res.stdout) if res.stdout.strip() else []
        except json.JSONDecodeError:
            pass
        return out


class WpscanScanTool(SecurityTool):
    name = "wpscan_scan"
    binary = "wpscan"
    description = (
        "WordPress security scanner. JSON input:\n"
        "  {\"url\": \"https://example.com\", \"enumerate\": \"vp,vt,u\", "
        "\"api_token\": \"...\", \"timeout\": 900}\n"
        "api_token enables WPVulnDB plugin/theme vuln lookups (from wpscan.com)."
    )

    def _run(self, parsed: dict) -> dict:
        url = parsed.get("url", "")
        if not self.safe_target(url, allow_url=True):
            return {"ok": False, "error": "invalid url"}
        enumerate = str(parsed.get("enumerate", "vp,vt,u"))
        if not self.safe_arg(enumerate):
            return {"ok": False, "error": "unsafe enumerate"}
        api_token = parsed.get("api_token")
        timeout = int(parsed.get("timeout", 900))
        argv = ["wpscan", "--url", url, "--enumerate", enumerate, "-f", "json", "--no-banner"]
        if api_token:
            if not self.safe_arg(api_token):
                return {"ok": False, "error": "unsafe api_token"}
            argv += ["--api-token", api_token]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        try:
            out["results"] = json.loads(res.stdout)
            out["stdout"] = "(json parsed)"
        except json.JSONDecodeError:
            pass
        return out
