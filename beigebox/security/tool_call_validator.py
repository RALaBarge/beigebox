"""
MCP Tool Call Validator — P1-D Security Module

Pre-execution hook for MCP tool calls. Prevents parameter injection,
enforces tool namespace isolation, and rate-limits tool invocations.

Validation layers:
  1. Parameter Injection Detection — SQL, command, path traversal patterns
  2. Tool Namespace Isolation — prevent name collisions across MCP servers
  3. Rate Limiting — configurable calls/min per tool
  4. Schema Validation — registered tool spec compliance

Audit logging: tool_audit table captures all calls (success/blocked/error).

Config (config.yaml):
  security:
    tool_call_validator:
      enabled: true
      rate_limit_per_tool: 10        # calls per minute
      isolation_enabled: true        # namespace checking
      audit_enabled: true            # log to database
      allow_unsafe: false            # block high-risk params
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Optional
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


# ── Risk Levels ───────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Tool call risk classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Injection Patterns ─────────────────────────────────────────────────────────

class InjectionPatterns:
    """Compiled regex patterns for injection detection."""

    # SQL injection signatures (20+ variants)
    SQL_PATTERNS = [
        r"(?:union|select|insert|update|delete|drop|create|alter|exec|execute|declare)\s+",
        r"(?:or|and)\s+(?:1=1|'.*'=.*|\".*\"=.*)",
        r";\s*(?:drop|delete|update|insert|create)",
        r"--\s*$",  # SQL comment
        r"/\*.*?\*/",  # Multi-line comment
        r"xp_(?:cmdshell|regread|regwrite)",  # MSSQL extended stored procs
        r"sys_exec|exec_xp|sp_",  # Other SQL extensions
    ]

    # Command injection patterns (15+ variants)
    COMMAND_PATTERNS = [
        r"[;&|`$(){}[\]]",  # Shell metacharacters
        r"\$\(.*?\)|`.*?`",  # Command substitution
        r">\s*/(?:dev/null|tmp|etc)",  # Redirection to system dirs
        r"nc\s+-|ncat\s+-|bash\s+-",  # Reverse shell patterns
        r"(?:cat|head|tail|less|more)\s+/(?:etc/passwd|shadow|sudoers)",
    ]

    # Path traversal patterns (10+ variants)
    PATH_PATTERNS = [
        r"\.\./|\.\./\.\./",  # Relative traversal
        r"\.\.\\|\.\.\\\.\\",  # Windows traversal
        r"%2e%2e[/\\]",  # URL-encoded traversal
        r"\x00|%00",  # Null byte injection
        r"\\x00|\\u0000",  # Escaped null bytes
    ]

    # Prompt injection patterns (detect in params)
    PROMPT_PATTERNS = [
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?|constraints?)",
        r"disregard.*(?:instructions?|rules?|constraints?)",
        r"(?:system|user|assistant)\s*:\s*",  # Role markers
        r"<\s*/?(?:system|user|assistant)\s*>",  # XML-style roles
        r"you\s+are\s+now\s+(?:a\s+)?(?:evil|jailbreak|unrestricted|free)",
    ]

    # Compiled patterns (module-level for performance)
    _compiled = {
        "sql": [re.compile(p, re.IGNORECASE | re.DOTALL) for p in SQL_PATTERNS],
        "command": [re.compile(p, re.IGNORECASE) for p in COMMAND_PATTERNS],
        "path": [re.compile(p, re.IGNORECASE) for p in PATH_PATTERNS],
        "prompt": [re.compile(p, re.IGNORECASE | re.DOTALL) for p in PROMPT_PATTERNS],
    }

    @classmethod
    def detect(cls, text: str, category: str = "all") -> Optional[tuple[str, str]]:
        """
        Detect injection pattern in text.

        Returns: (pattern_type, matched_text) or None
        """
        text_str = str(text) if not isinstance(text, str) else text

        if category in ("all", "sql"):
            for pat in cls._compiled["sql"]:
                m = pat.search(text_str)
                if m:
                    return ("sql_injection", m.group(0)[:100])

        if category in ("all", "command"):
            for pat in cls._compiled["command"]:
                m = pat.search(text_str)
                if m:
                    return ("command_injection", m.group(0)[:100])

        if category in ("all", "path"):
            for pat in cls._compiled["path"]:
                m = pat.search(text_str)
                if m:
                    return ("path_traversal", m.group(0)[:100])

        if category in ("all", "prompt"):
            for pat in cls._compiled["prompt"]:
                m = pat.search(text_str)
                if m:
                    return ("prompt_injection", m.group(0)[:100])

        return None


# ── Result Types ──────────────────────────────────────────────────────────────

@dataclass
class ToolCallValidationResult:
    """Result of tool call validation."""
    valid: bool
    risk_level: RiskLevel
    issues: list[str] = field(default_factory=list)
    injections_detected: list[tuple[str, str]] = field(default_factory=list)
    rate_limit_exceeded: bool = False
    isolation_violation: bool = False
    args_too_large: bool = False
    arg_string_too_long: bool = False
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "risk_level": self.risk_level.value,
            "issues": self.issues,
            "injections_detected": [{"type": t, "sample": s} for t, s in self.injections_detected],
            "rate_limit_exceeded": self.rate_limit_exceeded,
            "isolation_violation": self.isolation_violation,
            "args_too_large": self.args_too_large,
            "arg_string_too_long": self.arg_string_too_long,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


# Always-on default caps. These are the second line of defense behind the
# per-route PolicyEngine (which is opt-in). See policy.py for the equivalent
# config-driven caps. The validator caps run on EVERY tool call regardless of
# route.
DEFAULT_MAX_ARGS_BYTES = 64 * 1024     # 64 KiB serialized JSON
DEFAULT_MAX_STRING_LENGTH = 8 * 1024   # 8 KiB single string


def _max_string_length(obj: Any) -> int:
    """Walk *obj* recursively and return the longest str value found."""
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, dict):
        best = 0
        for k, v in obj.items():
            if isinstance(k, str) and len(k) > best:
                best = len(k)
            n = _max_string_length(v)
            if n > best:
                best = n
        return best
    if isinstance(obj, list):
        best = 0
        for v in obj:
            n = _max_string_length(v)
            if n > best:
                best = n
        return best
    return 0


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class ToolRateLimiter:
    """Per-tool rate limiting (calls/minute)."""

    def __init__(self, calls_per_minute: int = 10):
        self.calls_per_minute = calls_per_minute
        self.window_size = 60  # seconds
        self._lock = Lock()
        self._calls: dict[str, deque] = defaultdict(lambda: deque())

    def is_allowed(self, tool_name: str) -> tuple[bool, int]:
        """
        Check if tool call is allowed.

        Returns: (allowed, current_count_in_window)
        """
        now = time.time()
        with self._lock:
            calls = self._calls[tool_name]

            # Remove expired calls (older than window)
            while calls and calls[0] < now - self.window_size:
                calls.popleft()

            # Check limit
            if len(calls) >= self.calls_per_minute:
                return (False, len(calls))

            # Record this call
            calls.append(now)
            return (True, len(calls))

    def get_stats(self, tool_name: str) -> dict:
        """Get rate limit stats for a tool."""
        now = time.time()
        with self._lock:
            calls = self._calls[tool_name]
            # Clean expired calls
            while calls and calls[0] < now - self.window_size:
                calls.popleft()
            return {
                "tool_name": tool_name,
                "calls_in_window": len(calls),
                "limit": self.calls_per_minute,
                "window_size_seconds": self.window_size,
            }


# ── Namespace Isolation ────────────────────────────────────────────────────────

class ToolNamespaceIsolator:
    """
    Prevents tool name collisions across MCP servers.

    Maintains registry of tool sources to detect:
      - Name shadowing (tool with same name from different server)
      - Unauthorized server registration
    """

    def __init__(self):
        self._lock = Lock()
        self._registry: dict[str, str] = {}  # tool_name -> source (server/origin)

    def register(self, tool_name: str, source: str) -> tuple[bool, Optional[str]]:
        """
        Register a tool with its source.

        Returns: (success, conflict_source or None)
        """
        with self._lock:
            existing = self._registry.get(tool_name)
            if existing and existing != source:
                return (False, existing)
            self._registry[tool_name] = source
            return (True, None)

    def validate_call(self, tool_name: str, expected_source: str) -> tuple[bool, Optional[str]]:
        """
        Validate that a tool call matches registered source.

        Returns: (valid, actual_source or None)
        """
        with self._lock:
            actual = self._registry.get(tool_name)
            if not actual:
                return (True, None)  # Not registered yet, allow
            if actual != expected_source:
                return (False, actual)
            return (True, None)


# ── Main Validator ────────────────────────────────────────────────────────────

class ToolCallValidator:
    """
    Pre-execution validator for MCP tool calls.

    Validates parameters for injection, enforces namespace isolation,
    rate limits, and logs to audit table.

    Usage:
        validator = ToolCallValidator(
            rate_limit_per_tool=10,
            isolation_enabled=True,
            audit_enabled=True,
        )
        result = validator.validate(
            tool_name="web_search",
            parameters={"query": "..."},
            caller="claude_desktop",
        )
        if not result.valid:
            logger.error("Tool call blocked: %s", result.issues)
    """

    def __init__(
        self,
        rate_limit_per_tool: int = 10,
        isolation_enabled: bool = True,
        audit_enabled: bool = True,
        allow_unsafe: bool = False,
        audit_store=None,  # Optional audit store backend
        max_args_bytes: int = DEFAULT_MAX_ARGS_BYTES,
        max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
    ):
        self.rate_limit_per_tool = rate_limit_per_tool
        self.isolation_enabled = isolation_enabled
        self.audit_enabled = audit_enabled
        self.allow_unsafe = allow_unsafe
        self.audit_store = audit_store
        # DoS caps — see DEFAULT_MAX_* constants. ``None`` or non-positive
        # disables that specific cap; the rest stay enforced.
        self.max_args_bytes: int | None = max_args_bytes if (max_args_bytes and max_args_bytes > 0) else None
        self.max_string_length: int | None = max_string_length if (max_string_length and max_string_length > 0) else None

        self._rate_limiter = ToolRateLimiter(rate_limit_per_tool)
        self._namespace_isolator = ToolNamespaceIsolator()

        logger.info(
            "ToolCallValidator initialized (rate_limit=%d/min, isolation=%s, audit=%s, "
            "allow_unsafe=%s, max_args_bytes=%s, max_string_length=%s)",
            rate_limit_per_tool,
            isolation_enabled,
            audit_enabled,
            allow_unsafe,
            self.max_args_bytes,
            self.max_string_length,
        )

    @classmethod
    def from_config(cls, cfg: dict | None, audit_store=None) -> "ToolCallValidator":
        """Build a validator from ``security.tool_call_validator.*`` config.

        Recognized keys (all optional):
          - ``rate_limit_per_tool`` (default 10)
          - ``isolation_enabled``   (default True)
          - ``audit_enabled``       (default True)
          - ``allow_unsafe``        (default False)
          - ``max_args_bytes``      (default 65536)
          - ``max_string_length``   (default 8192)
        """
        block = ((cfg or {}).get("security") or {}).get("tool_call_validator") or {}
        return cls(
            rate_limit_per_tool=int(block.get("rate_limit_per_tool", 10)),
            isolation_enabled=bool(block.get("isolation_enabled", True)),
            audit_enabled=bool(block.get("audit_enabled", True)),
            allow_unsafe=bool(block.get("allow_unsafe", False)),
            audit_store=audit_store,
            max_args_bytes=int(block.get("max_args_bytes", DEFAULT_MAX_ARGS_BYTES)),
            max_string_length=int(block.get("max_string_length", DEFAULT_MAX_STRING_LENGTH)),
        )

    def validate(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        caller: str = "unknown",
        expected_source: str = "beigebox",
    ) -> ToolCallValidationResult:
        """
        Validate a tool call before execution.

        Args:
            tool_name: Name of tool to call
            parameters: Dict of parameter name -> value
            caller: Identifier of caller (e.g., "claude_desktop", "operator")
            expected_source: Expected source/server for namespace validation

        Returns:
            ToolCallValidationResult with validation outcome
        """
        start = time.time()
        result = ToolCallValidationResult(
            valid=True,
            risk_level=RiskLevel.LOW,
        )

        # Layer 0: DoS size caps. Run BEFORE injection detection so an
        # attacker can't burn regex-engine cycles on a 10 MB blob. These caps
        # are the always-on tier; the per-route PolicyEngine is the opt-in
        # tier with stricter limits per endpoint.
        if self.max_args_bytes is not None:
            try:
                serialized = json.dumps(parameters, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                serialized = ""
            args_size = len(serialized.encode("utf-8", errors="ignore"))
            if args_size > self.max_args_bytes:
                result.args_too_large = True
                result.risk_level = RiskLevel.HIGH
                result.issues.append(
                    f"Tool args {args_size} bytes exceeds {self.max_args_bytes}"
                )
                result.valid = False
        if self.max_string_length is not None:
            longest = _max_string_length(parameters)
            if longest > self.max_string_length:
                result.arg_string_too_long = True
                result.risk_level = RiskLevel.HIGH
                result.issues.append(
                    f"Tool arg string length {longest} exceeds {self.max_string_length}"
                )
                result.valid = False

        # Layer 1: Parameter injection detection
        injections = self._detect_parameter_injections(parameters)
        if injections:
            result.injections_detected = injections
            result.risk_level = RiskLevel.CRITICAL
            result.issues.append(f"Injection patterns detected: {[t for t, _ in injections]}")
            if not self.allow_unsafe:
                result.valid = False

        # Layer 2: Rate limiting
        allowed, count = self._rate_limiter.is_allowed(tool_name)
        if not allowed:
            result.rate_limit_exceeded = True
            result.risk_level = RiskLevel.HIGH
            result.issues.append(f"Rate limit exceeded ({count}/{self.rate_limit_per_tool})")
            result.valid = False

        # Layer 3: Namespace isolation
        if self.isolation_enabled:
            is_isolated, conflict = self._namespace_isolator.validate_call(
                tool_name, expected_source
            )
            if not is_isolated:
                result.isolation_violation = True
                result.risk_level = RiskLevel.CRITICAL
                result.issues.append(
                    f"Namespace collision: {tool_name} registered to {conflict}, not {expected_source}"
                )
                result.valid = False

        # Layer 4: Schema validation (basic type checking)
        schema_issues = self._validate_schema(tool_name, parameters)
        if schema_issues:
            result.issues.extend(schema_issues)
            result.risk_level = max(result.risk_level, RiskLevel.MEDIUM)

        result.elapsed_ms = (time.time() - start) * 1000

        # Audit logging
        if self.audit_enabled:
            self._audit_log(
                tool_name=tool_name,
                caller=caller,
                valid=result.valid,
                risk_level=result.risk_level,
                params_hash=self._hash_params(parameters),
                issues=result.issues,
                elapsed_ms=result.elapsed_ms,
            )

        return result

    def _detect_parameter_injections(
        self, parameters: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """
        Scan all parameters for injection patterns.

        Returns: List of (pattern_type, matched_sample) tuples
        """
        injections = []

        for key, value in parameters.items():
            if value is None:
                continue

            # Convert to string for scanning
            if isinstance(value, (dict, list)):
                text = json.dumps(value)
            else:
                text = str(value)

            # Detect injections
            detection = InjectionPatterns.detect(text)
            if detection:
                injections.append((detection[0], detection[1]))

        return injections

    def _validate_schema(self, tool_name: str, parameters: dict) -> list[str]:
        """
        Basic schema validation (can be extended per tool).

        Returns: List of validation issues
        """
        issues = []

        # Basic checks for all tools
        for key, value in parameters.items():
            if not isinstance(key, str):
                issues.append(f"Parameter key must be string, got {type(key)}")
            if value is not None:
                # Check for extremely large values
                if isinstance(value, (str, bytes)) and len(value) > 1_000_000:
                    issues.append(f"Parameter '{key}' exceeds 1MB limit")

        return issues

    def _hash_params(self, parameters: dict) -> str:
        """Hash parameters for audit logging (privacy-preserving)."""
        try:
            json_str = json.dumps(parameters, sort_keys=True, default=str)
            return hashlib.sha256(json_str.encode()).hexdigest()[:16]
        except Exception:
            return "unknown"

    def _audit_log(
        self,
        tool_name: str,
        caller: str,
        valid: bool,
        risk_level: RiskLevel,
        params_hash: str,
        issues: list[str],
        elapsed_ms: float,
    ) -> None:
        """Log tool call to audit table (if store available)."""
        if not self.audit_store:
            return

        try:
            self.audit_store.log_tool_audit(
                tool_name=tool_name,
                caller=caller,
                valid=valid,
                risk_level=risk_level.value,
                params_hash=params_hash,
                issues="; ".join(issues) if issues else "",
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Failed to write tool audit log: %s", e)

    def register_tool(self, tool_name: str, source: str = "beigebox") -> tuple[bool, Optional[str]]:
        """Register a tool with its source for namespace isolation."""
        return self._namespace_isolator.register(tool_name, source)

    def get_rate_limit_stats(self, tool_name: str) -> dict:
        """Get rate limit stats for a tool."""
        return self._rate_limiter.get_stats(tool_name)
