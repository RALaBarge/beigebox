"""
Credential testing / hash cracking. Sensitive — every wrapper here requires
an explicit ``authorization: true`` field to acknowledge you have permission
to run these tools against the target.
"""
from __future__ import annotations

import json

from beigebox.security_mcp._base import SecurityTool
from beigebox.security_mcp._run import run_argv


class HydraAttackTool(SecurityTool):
    name = "hydra_attack"
    binary = "hydra"
    requires_auth = True
    description = (
        "Online password brute-forcer. JSON input:\n"
        "  {\"target\": \"10.0.0.5\", \"service\": \"ssh|ftp|http-get|smb|...\", "
        "\"username\": \"admin\" | \"users_file\": \"/path/to/users.txt\", "
        "\"password_file\": \"/path/to/passwords.txt\", "
        "\"port\": 22, \"threads\": 4, \"authorization\": true, \"timeout\": 1800}\n"
        "REQUIRES authorization=true. DESTRUCTIVE — only run against authorized hosts."
    )

    SERVICES = {
        "ssh", "ftp", "telnet", "smtp", "smb", "rdp", "vnc",
        "http-get", "http-post", "http-form-get", "http-form-post",
        "https-get", "https-post", "mysql", "postgres", "mssql", "redis",
    }

    def _run(self, parsed: dict) -> dict:
        target = parsed.get("target", "")
        service = str(parsed.get("service", ""))
        if not self.safe_target(target):
            return {"ok": False, "error": "invalid target"}
        if service not in self.SERVICES:
            return {"ok": False, "error": f"service must be one of {sorted(self.SERVICES)}"}
        user = parsed.get("username")
        users_file = parsed.get("users_file")
        if not user and not users_file:
            return {"ok": False, "error": "either 'username' or 'users_file' is required"}
        pwd_file = parsed.get("password_file", "")
        if not pwd_file or not self.safe_path(pwd_file):
            return {"ok": False, "error": "valid 'password_file' path required"}
        if users_file and not self.safe_path(users_file):
            return {"ok": False, "error": "users_file path invalid or missing"}
        if user and not self.safe_arg(user):
            return {"ok": False, "error": "unsafe username"}
        port = int(parsed.get("port", 0))
        threads = int(parsed.get("threads", 4))
        timeout = int(parsed.get("timeout", 1800))

        argv = ["hydra", "-t", str(threads), "-V"]
        if user:
            argv += ["-l", user]
        else:
            argv += ["-L", users_file]
        argv += ["-P", pwd_file]
        if port:
            argv += ["-s", str(port)]
        argv += [target, service]
        res = run_argv(argv, timeout=timeout)
        return json.loads(res.to_json_str())


class JohnCrackTool(SecurityTool):
    name = "john_crack"
    binary = "john"
    requires_auth = True
    description = (
        "John the Ripper offline hash cracking. JSON input:\n"
        "  {\"hash_file\": \"/path/to/hashes.txt\", "
        "\"wordlist\": \"/usr/share/wordlists/rockyou.txt\", "
        "\"format\": \"raw-md5|raw-sha256|nt|...\", "
        "\"rules\": false, \"timeout\": 3600, \"authorization\": true}"
    )

    def _run(self, parsed: dict) -> dict:
        hash_file = parsed.get("hash_file", "")
        if not self.safe_path(hash_file):
            return {"ok": False, "error": "hash_file invalid or missing"}
        wordlist = parsed.get("wordlist", "/usr/share/wordlists/rockyou.txt")
        if not self.safe_path(wordlist, must_exist=False):
            return {"ok": False, "error": "unsafe wordlist path"}
        fmt = parsed.get("format")
        rules = bool(parsed.get("rules", False))
        timeout = int(parsed.get("timeout", 3600))

        argv = ["john", f"--wordlist={wordlist}"]
        if fmt:
            if not self.safe_arg(fmt):
                return {"ok": False, "error": "unsafe format"}
            argv.append(f"--format={fmt}")
        if rules:
            argv.append("--rules")
        argv.append(hash_file)
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # Run --show to extract cracked passwords if any.
        from beigebox.security_mcp._run import which
        if res.ok and which("john"):
            show_argv = ["john", "--show"]
            if fmt:
                show_argv.append(f"--format={fmt}")
            show_argv.append(hash_file)
            show = run_argv(show_argv, timeout=30)
            out["cracked"] = show.stdout
        return out


class HashcatCrackTool(SecurityTool):
    name = "hashcat_crack"
    binary = "hashcat"
    requires_auth = True
    description = (
        "GPU-accelerated hash cracking. JSON input:\n"
        "  {\"hash_file\": \"/path/to/hashes.txt\", "
        "\"wordlist\": \"/usr/share/wordlists/rockyou.txt\", "
        "\"hash_type\": 0, \"attack_mode\": 0, \"force\": false, "
        "\"timeout\": 7200, \"authorization\": true}\n"
        "hash_type and attack_mode follow hashcat's numeric IDs (0=MD5, 1000=NTLM, ...)."
    )

    def _run(self, parsed: dict) -> dict:
        hash_file = parsed.get("hash_file", "")
        if not self.safe_path(hash_file):
            return {"ok": False, "error": "hash_file invalid or missing"}
        wordlist = parsed.get("wordlist", "/usr/share/wordlists/rockyou.txt")
        if not self.safe_path(wordlist, must_exist=False):
            return {"ok": False, "error": "unsafe wordlist path"}
        hash_type = int(parsed.get("hash_type", 0))
        attack_mode = int(parsed.get("attack_mode", 0))
        force = bool(parsed.get("force", False))
        timeout = int(parsed.get("timeout", 7200))

        argv = ["hashcat", "-m", str(hash_type), "-a", str(attack_mode),
                "--quiet", "--status", hash_file, wordlist]
        if force:
            argv.append("--force")
        res = run_argv(argv, timeout=timeout)
        out = json.loads(res.to_json_str())
        # Run --show to extract cracked passwords if any.
        from beigebox.security_mcp._run import which
        if which("hashcat"):
            show = run_argv(
                ["hashcat", "-m", str(hash_type), "--show", hash_file],
                timeout=30,
            )
            out["cracked"] = show.stdout
        return out
