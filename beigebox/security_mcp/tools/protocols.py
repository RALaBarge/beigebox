"""SNMP / NetBIOS / LDAP protocol enumeration wrappers."""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class SnmpwalkScanTool(SecurityTool):
    name = "snmpwalk_scan"
    binary = "snmpwalk"
    description = (
        "Walk an SNMP tree (v1/v2c/v3). JSON input:\n"
        "  {\"target\": \"10.0.0.5\", \"community\": \"public\", \"version\": \"2c\", "
        "\"oid\": \"1.3.6.1.2.1.1\", \"timeout\": 60}"
    )

    VERSIONS = {"1", "2c", "3"}

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        community = str(parsed.get("community", "public"))
        if not self.safe_arg(community):
            return {"ok": False, "error": "unsafe community string"}
        version = str(parsed.get("version", "2c"))
        if version not in self.VERSIONS:
            return {"ok": False, "error": "version must be 1|2c|3"}
        oid = str(parsed.get("oid", "1.3.6.1.2.1.1"))
        if not self.safe_arg(oid):
            return {"ok": False, "error": "unsafe oid"}
        timeout = int(parsed.get("timeout", 60))
        argv = ["snmpwalk", "-c", community, "-v", version, target, oid]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class OnesixtyoneScanTool(SecurityTool):
    name = "onesixtyone_scan"
    binary = "onesixtyone"
    description = (
        "Fast SNMP community-string scanner. JSON input:\n"
        "  {\"target\": \"10.0.0.0/24\", \"community_file\": \"/path/to/communities.txt\", "
        "\"timeout\": 120}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        cfile = parsed.get("community_file", "")
        if not self.safe_path(cfile):
            return {"ok": False, "error": "community_file invalid or missing"}
        timeout = int(parsed.get("timeout", 120))
        argv = ["onesixtyone", "-c", cfile, target]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class NbtscanScanTool(SecurityTool):
    name = "nbtscan_scan"
    binary = "nbtscan"
    description = (
        "NetBIOS name-service scanner across a subnet. JSON input:\n"
        "  {\"target\": \"10.0.0.0/24\", \"timeout\": 60}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        timeout = int(parsed.get("timeout", 60))
        argv = ["nbtscan", target]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class LdapsearchScanTool(SecurityTool):
    name = "ldapsearch_scan"
    binary = "ldapsearch"
    description = (
        "LDAP query (anonymous or bound). JSON input:\n"
        "  {\"uri\": \"ldap://10.0.0.5\", \"base\": \"dc=example,dc=com\", "
        "\"filter\": \"(objectClass=user)\", \"bind_dn\": \"\", \"bind_pw\": \"\", "
        "\"timeout\": 60}"
    )

    def _run(self, parsed: dict) -> dict:
        uri = str(parsed.get("uri", ""))
        if not uri.startswith(("ldap://", "ldaps://")):
            return {"ok": False, "error": "uri must start with ldap:// or ldaps://"}
        if not self.safe_arg(uri):
            return {"ok": False, "error": "unsafe uri"}
        base = str(parsed.get("base", ""))
        if not base or not self.safe_arg(base):
            return {"ok": False, "error": "base DN required and must be safe"}
        flt = str(parsed.get("filter", "(objectClass=*)"))
        # filter can contain parens — bypass safe_arg for this one but reject newlines/shell
        if any(ch in flt for ch in ";|&`$<>\\\"'\n\r\t"):
            return {"ok": False, "error": "unsafe filter"}
        bind_dn = parsed.get("bind_dn", "")
        bind_pw = parsed.get("bind_pw", "")
        if bind_dn and not self.safe_arg(bind_dn):
            return {"ok": False, "error": "unsafe bind_dn"}
        if bind_pw and not self.safe_arg(bind_pw):
            return {"ok": False, "error": "unsafe bind_pw"}
        timeout = int(parsed.get("timeout", 60))
        argv = ["ldapsearch", "-x", "-H", uri, "-b", base, flt]
        if bind_dn:
            argv += ["-D", bind_dn]
            if bind_pw:
                argv += ["-w", bind_pw]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
