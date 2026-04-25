"""
Active Directory / Kerberos enumeration via the impacket suite + kerbrute.
All tools here that touch creds require ``authorization: true``.
"""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


def _safe_creds(parsed: dict, tool: SecurityTool) -> tuple[bool, dict]:
    """Validate domain/user/password/hash bundle. Returns (ok, error_dict)."""
    for k in ("domain", "username", "password", "hash"):
        v = parsed.get(k, "")
        if v and not tool.safe_arg(v):
            return False, {"ok": False, "error": f"unsafe {k}"}
    return True, {}


class ImpacketSecretsdumpTool(SecurityTool):
    name = "impacket_secretsdump"
    binary = "impacket-secretsdump"  # also installed as 'secretsdump.py' on some systems
    requires_auth = True
    description = (
        "Dump SAM/LSA/DCSync secrets via impacket. JSON input:\n"
        "  {\"target\": \"DC.example.com\", \"domain\": \"EXAMPLE\", "
        "\"username\": \"admin\", \"password\": \"\", \"hash\": \"LM:NT\", "
        "\"just_dc\": false, \"timeout\": 600, \"authorization\": true}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        ok, err = _safe_creds(parsed, self)
        if not ok:
            return err
        domain = parsed.get("domain", "")
        user = parsed.get("username", "")
        pwd = parsed.get("password", "")
        nthash = parsed.get("hash", "")
        timeout = int(parsed.get("timeout", 600))

        from beigebox.security_mcp._run import which_any
        _, binary = which_any("impacket-secretsdump", "secretsdump.py")
        if binary is None:
            return {"ok": False, "error": "neither 'impacket-secretsdump' nor 'secretsdump.py' on PATH"}

        # impacket target syntax: DOMAIN/USER:PASSWORD@TARGET
        target_spec = ""
        if domain:
            target_spec = f"{domain}/"
        if user:
            target_spec += user
        if pwd:
            target_spec += f":{pwd}"
        if user or domain:
            target_spec += "@"
        target_spec += target

        argv = [binary, target_spec]
        if nthash:
            argv += ["-hashes", nthash]
        if not pwd and not nthash:
            argv += ["-no-pass"]
        if parsed.get("just_dc"):
            argv += ["-just-dc"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class ImpacketGetuserspnsTool(SecurityTool):
    name = "impacket_getuserspns"
    binary = "impacket-GetUserSPNs"
    requires_auth = True
    description = (
        "Kerberoasting — request TGS for SPN-bearing accounts. JSON input:\n"
        "  {\"target\": \"DC.example.com\", \"domain\": \"EXAMPLE\", "
        "\"username\": \"user\", \"password\": \"...\", \"request\": true, "
        "\"timeout\": 300, \"authorization\": true}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        ok, err = _safe_creds(parsed, self)
        if not ok:
            return err
        domain = parsed.get("domain", "")
        user = parsed.get("username", "")
        pwd = parsed.get("password", "")
        if not domain or not user:
            return {"ok": False, "error": "domain and username are required"}
        timeout = int(parsed.get("timeout", 300))

        from beigebox.security_mcp._run import which_any
        _, binary = which_any("impacket-GetUserSPNs", "GetUserSPNs.py")
        if binary is None:
            return {"ok": False, "error": "neither 'impacket-GetUserSPNs' nor 'GetUserSPNs.py' on PATH"}

        target_spec = f"{domain}/{user}"
        if pwd:
            target_spec += f":{pwd}"
        argv = [binary, target_spec, "-dc-ip", target]
        if parsed.get("request", True):
            argv.append("-request")
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class ImpacketGetnpusersTool(SecurityTool):
    name = "impacket_getnpusers"
    binary = "impacket-GetNPUsers"
    requires_auth = True
    description = (
        "AS-REP roasting — request AS-REP for users with no preauth. JSON input:\n"
        "  {\"target\": \"DC.example.com\", \"domain\": \"EXAMPLE\", "
        "\"users_file\": \"/path/users.txt\", \"timeout\": 300, \"authorization\": true}"
    )

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        domain = parsed.get("domain", "")
        users_file = parsed.get("users_file", "")
        if not domain or not self.safe_arg(domain):
            return {"ok": False, "error": "domain required and must be safe"}
        if not self.safe_path(users_file):
            return {"ok": False, "error": "users_file invalid or missing"}
        timeout = int(parsed.get("timeout", 300))

        from beigebox.security_mcp._run import which_any
        _, binary = which_any("impacket-GetNPUsers", "GetNPUsers.py")
        if binary is None:
            return {"ok": False, "error": "neither 'impacket-GetNPUsers' nor 'GetNPUsers.py' on PATH"}

        argv = [binary, f"{domain}/", "-usersfile", users_file,
                "-no-pass", "-dc-ip", target, "-format", "hashcat"]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class KerbruteUserenumTool(SecurityTool):
    name = "kerbrute_userenum"
    binary = "kerbrute"
    description = (
        "Kerberos username enumeration (no preauth needed, no logs in many configs). "
        "JSON input:\n  {\"domain\": \"example.com\", \"dc\": \"10.0.0.5\", "
        "\"users_file\": \"/path/users.txt\", \"threads\": 10, \"timeout\": 300}"
    )

    def _run(self, parsed: dict) -> dict:
        domain = parsed.get("domain", "")
        if not self.safe_target(domain):
            return {"ok": False, "error": "invalid domain"}
        dc = parsed.get("dc", "")
        if dc and not self.safe_target(dc):
            return {"ok": False, "error": "invalid dc"}
        users_file = parsed.get("users_file", "")
        if not self.safe_path(users_file):
            return {"ok": False, "error": "users_file invalid or missing"}
        threads = int(parsed.get("threads", 10))
        timeout = int(parsed.get("timeout", 300))
        argv = ["kerbrute", "userenum", "-d", domain, "-t", str(threads)]
        if dc:
            argv += ["--dc", dc]
        argv.append(users_file)
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())
