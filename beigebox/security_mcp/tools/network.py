"""Network discovery / port scanning wrappers."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class NmapScanTool(SecurityTool):
    name = "nmap_scan"
    binary = "nmap"
    description = (
        "Run nmap against a target. JSON input:\n"
        "  {\"target\": \"scanme.nmap.org\", \"ports\": \"1-1000\", "
        "\"profile\": \"default|quick|service|aggressive\", "
        "\"scripts\": [\"vuln\"], \"timeout\": 600}\n"
        "Returns parsed XML (-oX -) findings as JSON when possible, raw stdout otherwise."
    )

    PROFILES = {
        "default": ["-sS", "-sV", "-Pn"],
        "quick": ["-sS", "-T4", "--top-ports", "100", "-Pn"],
        "service": ["-sS", "-sV", "-sC", "-Pn"],
        "aggressive": ["-A", "-T4", "-Pn"],
    }

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target (no shell metachars; hostname/ip[/cidr][:port] only)"}
        profile = str(parsed.get("profile", "default"))
        if profile not in self.PROFILES:
            return {"ok": False, "error": f"unknown profile (use one of {list(self.PROFILES)})"}
        ports = parsed.get("ports")
        scripts = parsed.get("scripts") or []
        timeout = int(parsed.get("timeout", 600))

        argv = ["nmap", *self.PROFILES[profile], "-oX", "-"]
        if ports:
            if not self.safe_arg(str(ports)):
                return {"ok": False, "error": "unsafe ports value"}
            argv += ["-p", str(ports)]
        if scripts:
            if not all(self.safe_arg(s) for s in scripts):
                return {"ok": False, "error": "unsafe script name"}
            argv += ["--script", ",".join(scripts)]
        argv.append(target)

        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # Try to parse XML for a structured findings list.
        try:
            findings = _parse_nmap_xml(res.stdout)
            if findings is not None:
                out["findings"] = findings
                # Drop raw XML once parsed to keep response small.
                out["stdout"] = f"(XML parsed: {len(findings)} hosts)"
        except Exception:
            pass
        return out


def _parse_nmap_xml(xml: str) -> list[dict] | None:
    if not xml or not xml.lstrip().startswith("<?xml"):
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    hosts: list[dict] = []
    for h in root.findall("host"):
        addr_el = h.find("address")
        addr = addr_el.get("addr") if addr_el is not None else None
        status_el = h.find("status")
        status = status_el.get("state") if status_el is not None else None
        ports = []
        for p in h.findall(".//port"):
            state_el = p.find("state")
            svc_el = p.find("service")
            ports.append({
                "port": int(p.get("portid", 0)),
                "proto": p.get("protocol"),
                "state": state_el.get("state") if state_el is not None else None,
                "service": svc_el.get("name") if svc_el is not None else None,
                "product": svc_el.get("product") if svc_el is not None else None,
                "version": svc_el.get("version") if svc_el is not None else None,
            })
        hosts.append({"addr": addr, "status": status, "ports": ports})
    return hosts


class MasscanScanTool(SecurityTool):
    name = "masscan_scan"
    binary = "masscan"
    description = (
        "High-rate port sweep. JSON input:\n"
        "  {\"target\": \"10.0.0.0/24\", \"ports\": \"1-65535\", "
        "\"rate\": 1000, \"timeout\": 900}\n"
        "Requires CAP_NET_RAW or root."
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        ports = str(parsed.get("ports", "1-1000"))
        if not self.safe_arg(ports):
            return {"ok": False, "error": "unsafe ports"}
        rate = int(parsed.get("rate", 1000))
        timeout = int(parsed.get("timeout", 900))
        argv = [
            "masscan", target, "-p", ports, "--rate", str(rate),
            "--output-format", "json", "--output-filename", "-",
        ]
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # masscan emits NDJSON-ish lines wrapped in []; keep raw + parsed.
        try:
            lines = [l for l in res.stdout.splitlines() if l.strip().startswith("{")]
            findings = []
            for l in lines:
                l = l.rstrip(",")
                try:
                    findings.append(json.loads(l))
                except json.JSONDecodeError:
                    continue
            if findings:
                out["findings"] = findings
        except Exception:
            pass
        return out


class DnsenumScanTool(SecurityTool):
    name = "dnsenum_scan"
    binary = "dnsenum"
    description = (
        "DNS enumeration / zone transfer attempt / brute force. JSON input:\n"
        "  {\"domain\": \"example.com\", \"threads\": 5, \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        threads = int(parsed.get("threads", 5))
        timeout = int(parsed.get("timeout", 300))
        argv = ["dnsenum", "--noreverse", "--threads", str(threads), domain]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
