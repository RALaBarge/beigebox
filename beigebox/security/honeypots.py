"""
Honeypot Canaries for Bypass Detection

If an attacker finds a bypass in our isolation validator, they will likely try
to use it. We plant intentionally vulnerable "canaries" in the tool definitions
to detect when someone activates a bypass.

These are NOT real tools - they exist solely to alert on bypass attempts.

Reference: Post-breach detection is easier than perfect prevention.
"""

from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HoneypotDefinition:
    """Definition of a honeypot canary tool."""
    name: str
    description: str
    honeypot_type: str      # "path_traversal", "command_injection", "symlink", etc.
    exploit_method: str     # How an attacker would use this

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "exploit_method": self.exploit_method,
        }


class HoneypotManager:
    """
    Manages honeypot canaries that detect bypass attempts.

    The idea: if we find a bypass in isolation validator, attackers will try
    to use it to access these forbidden paths/tools. When they do, we alert.

    This is a "trap" for adversaries.
    """

    def __init__(self, audit_logger=None):
        self.audit_logger = audit_logger
        self.honeypots = self._build_honeypots()

    def _build_honeypots(self) -> dict[str, HoneypotDefinition]:
        """Define honeypot canaries."""
        return {
            # Path traversal honeypot
            "__INTERNAL_ESCAPE_TEST__": HoneypotDefinition(
                name="__INTERNAL_ESCAPE_TEST__",
                description="[HONEYPOT] This file shouldn't be accessible. If you're reading this, you found a path traversal bypass.",
                honeypot_type="path_traversal",
                exploit_method="Someone used path traversal (../) or symlinks to escape /workspace/in/",
            ),

            # Command injection honeypot
            "__INTERNAL_CMD_INJECTION__": HoneypotDefinition(
                name="__INTERNAL_CMD_INJECTION__",
                description="[HONEYPOT] This command should never execute. If invoked, command injection was successful.",
                honeypot_type="command_injection",
                exploit_method="Someone used $(...), $((...)), or ${...@P} to execute arbitrary code",
            ),

            # Bash expansion honeypot
            "__INTERNAL_BASH_EXPANSION__": HoneypotDefinition(
                name="__INTERNAL_BASH_EXPANSION__",
                description="[HONEYPOT] This parameter bypasses our expansion filters.",
                honeypot_type="bash_expansion",
                exploit_method="Someone used $IFS, $(...), or other shell expansion tricks",
            ),

            # Argument abbreviation honeypot (git, sort, sed, etc.)
            "__INTERNAL_ARG_ABBREV__": HoneypotDefinition(
                name="__INTERNAL_ARG_ABBREV__",
                description="[HONEYPOT] Tests if argument abbreviation was used to bypass filters.",
                honeypot_type="argument_abbreviation",
                exploit_method="git --upload-pa instead of --upload-pack, sort --compress-prog instead of --compress-program",
            ),

            # Unicode/encoding trick honeypot
            "__INTERNAL_ENCODING_TRICK__": HoneypotDefinition(
                name="__INTERNAL_ENCODING_TRICK__",
                description="[HONEYPOT] Uses unicode characters to bypass ASCII-only filters.",
                honeypot_type="unicode_bypass",
                exploit_method="Fullwidth characters (＄, ．，．), unicode lookalikes, or URL encoding (%2e%2e%2f)",
            ),

            # Null byte injection honeypot
            "__INTERNAL_NULL_BYTE__": HoneypotDefinition(
                name="__INTERNAL_NULL_BYTE__",
                description="[HONEYPOT] Contains null bytes to test null-byte-injection bypass.",
                honeypot_type="null_byte",
                exploit_method="path.txt%00.jpg or similar null-byte tricks",
            ),

            # Symlink honeypot
            "__INTERNAL_SYMLINK_TEST__": HoneypotDefinition(
                name="__INTERNAL_SYMLINK_TEST__",
                description="[HONEYPOT] This entire path is a symlink escaping the workspace.",
                honeypot_type="symlink_bypass",
                exploit_method="Symlink from /tmp/attacker_link -> /etc/passwd, accessed as workspace/attacker_link",
            ),

            # Whitespace/padding honeypot (like Claude Code bypass #4)
            "__INTERNAL_PADDING_TEST__": HoneypotDefinition(
                name="__INTERNAL_PADDING_TEST__",
                description="[HONEYPOT] Tests if padding/whitespace in commands bypasses validation.",
                honeypot_type="padding_bypass",
                exploit_method="Commands with >50 subcommands (&&, ||, ;) that bypass deny rules",
            ),
        }

    def get_honeypot_definitions(self) -> dict[str, dict]:
        """
        Return honeypot definitions as tool specs.

        These are injected into the tool registry so if someone tries
        to call them, we know it's an attack.
        """
        return {
            name: defn.to_dict()
            for name, defn in self.honeypots.items()
        }

    def on_honeypot_triggered(
        self,
        honeypot_name: str,
        context: dict = None,
    ) -> None:
        """
        Called when someone accesses a honeypot.

        This indicates they found a bypass!
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        alert_msg = f"""
╔════════════════════════════════════════════════════════════════╗
║                    🚨 HONEYPOT TRIGGERED 🚨                    ║
╚════════════════════════════════════════════════════════════════╝

Honeypot: {honeypot_name}
Time: {timestamp}
Context: {context}

TYPE: Bypass Detected
SEVERITY: CRITICAL

Someone found a way to bypass our isolation validator!

This alert was triggered because code accessed:
  {self.honeypots[honeypot_name].description}

Method used:
  {self.honeypots[honeypot_name].exploit_method}

ACTION REQUIRED:
  1. Review audit logs for all recent DENY decisions
  2. Analyze the exact parameters used to trigger this honeypot
  3. Update isolation validator to block this bypass
  4. Deploy patched version immediately
"""

        # Log to console with max visibility
        logger.critical(alert_msg)

        # Also log to audit if available
        if self.audit_logger:
            self.audit_logger.log_validation(
                tool="honeypot",
                action="triggered",
                params={"honeypot": honeypot_name, "context": context},
                decision="ALARM",
                reason=f"Honeypot triggered: {honeypot_name}",
                severity="critical",
                bypass_attempt=True,
            )

    def check_for_honeypot_attempt(self, tool_name: str) -> bool:
        """
        Check if the requested tool is a honeypot.

        Returns True if it is, triggering an alert.
        """
        if tool_name in self.honeypots:
            self.on_honeypot_triggered(
                tool_name,
                context={"tool_requested": tool_name}
            )
            return True
        return False

    def generate_bypass_guide(self) -> str:
        """
        Generate a document listing all honeypots and their attack methods.

        This is used for RED TEAMING - understanding how to find bypasses.
        """
        guide = """
# Security Honeypot Guide

This document lists all honeypots planted to detect security bypasses.

## Understanding Honeypots

Honeypots are deliberately vulnerable or forbidden resources. If you can access them,
you've found a bypass in the isolation validator.

## Honeypots by Type

"""

        # Group by type
        by_type = {}
        for name, defn in self.honeypots.items():
            htype = defn.honeypot_type
            if htype not in by_type:
                by_type[htype] = []
            by_type[htype].append((name, defn))

        for htype in sorted(by_type.keys()):
            guide += f"\n### {htype.replace('_', ' ').title()}\n\n"
            for name, defn in by_type[htype]:
                guide += f"**{name}**\n"
                guide += f"- Description: {defn.description}\n"
                guide += f"- How to exploit: {defn.exploit_method}\n"
                guide += f"- If triggered: Security team receives alert\n\n"

        guide += """
## Red-Teaming Strategy

If you're security-testing this system, your goal is to trigger honeypots.

1. **Path Traversal**: Try to read `__INTERNAL_ESCAPE_TEST__` using various path tricks
   - `../../../__INTERNAL_ESCAPE_TEST__`
   - `..%2f..%2f__INTERNAL_ESCAPE_TEST__`
   - `/tmp/symlink_to_workspace/__INTERNAL_ESCAPE_TEST__`

2. **Command Injection**: Try to invoke `__INTERNAL_CMD_INJECTION__` using:
   - `$(command)` substitution
   - `${var@P}` prompt expansion
   - Chained pipes/semicolons

3. **Encoding Tricks**: Use unicode, URL encoding, null bytes:
   - Fullwidth characters: `＄(command)`
   - URL encoding: `%2e%2e%2f`
   - Null bytes: `path%00`

4. **Argument Abbreviation**: Try shortened flags:
   - `git --upload-pa` instead of `--upload-pack`
   - `sort --compress-prog` instead of `--compress-program`

If ANY honeypot is accessed, the system alerts and logging captures:
  - Exact parameters used
  - Timestamp
  - Which isolation check was bypassed
  - Audit trail of how you got there

## What Happens When You Trigger a Honeypot

1. An CRITICAL alert is logged immediately
2. The audit logger records the exact bypass parameters
3. Security team is notified
4. The bypass method is documented for patching
5. The system may terminate or ask for approval before proceeding

## Prevention Strategy

Honeypots work in conjunction with isolation:

```
ISOLATION (Strongest Defense)
  ↓ (If somehow bypassed)
HONEYPOT DETECTION (Alert & Log)
  ↓ (If somehow triggered)
INCIDENT RESPONSE (Security team reaction)
```

The goal is not to perfectly prevent bypasses (impossible), but to:
  1. Make bypasses as hard as possible (isolation)
  2. Detect when they happen (honeypots)
  3. Respond quickly (audit logging + alerts)
"""

        return guide
