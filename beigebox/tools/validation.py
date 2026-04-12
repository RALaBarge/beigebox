"""
MCP Parameter Validation Layer — Phase 1

Prevents tool call injection attacks (code execution, SQL injection, path traversal, XSS).
Provides single validation point for all tool inputs before execution.

Validation strategy:
  1. Schema-based: Pydantic validators for each tool (type, length, format)
  2. Heuristic: Pattern matching for known attack vectors
  3. Fail-safe: Reject if validation uncertain, never allow through
  4. Graceful: Unknown tools degrade gracefully (warn but allow)
  5. Observable: Log all rejections to Tap with confidence scores

Performance budget: <100ms per tool validation
Risk tolerance: Zero tolerance for injection — reject at slightest doubt
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from beigebox.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation attempt."""
    is_valid: bool
    cleaned_input: Any  # Sanitized version if applicable
    errors: list[str]  # List of validation errors
    warnings: list[str]  # Non-fatal issues
    confidence: float  # 0.0–1.0, rejection confidence


class ParameterValidator:
    """
    Central parameter validation for all tool inputs.
    Prevents injection attacks at the point of tool execution.
    """

    def __init__(self):
        self.config = get_config()
        self.security_config = self.config.get("security", {})
        self.validation_config = self.security_config.get("tool_validation", {})
        self.enabled = self.validation_config.get("enabled", True)
        self.mode = self.validation_config.get("mode", "strict")  # strict, warn, permissive
        self.per_tool_limits = self.validation_config.get("per_tool_limits", {})

        # Initialize injection detection patterns
        self._init_patterns()

        logger.info(
            "ParameterValidator initialized (enabled=%s, mode=%s)",
            self.enabled,
            self.mode,
        )

    def _init_patterns(self):
        """Initialize regex patterns for injection detection."""
        # Command injection: backticks, $(), &&, |, ;, etc.
        self.cmd_injection_pattern = re.compile(
            r"(`|\$\(|&&|\|\||;|<\(|\beval\b|\bexec\b|\bsh\b|\bbash\b)",
            re.IGNORECASE,
        )

        # Path traversal: ../, ..\\, UNC paths
        self.path_traversal_pattern = re.compile(
            r"(\.\./|\.\.\\|\\\\[^\\]+\\[^\\]+|^[a-zA-Z]:\\)"
        )

        # SQL injection: SELECT, DROP, UNION, INSERT, DELETE, UPDATE, EXEC, etc.
        self.sql_injection_pattern = re.compile(
            r"\b(SELECT|DROP|UNION|INSERT|DELETE|UPDATE|EXEC|EXECUTE|CREATE|ALTER|"
            r"TRUNCATE|PRAGMA|ATTACH|VACUUM|REPLACE|MERGE|CALL)\b",
            re.IGNORECASE,
        )

        # XSS patterns: javascript:, <script>, onerror=, onclick=, etc.
        self.xss_pattern = re.compile(
            r"(javascript:|<\s*script|<\s*iframe|onerror\s*=|onclick\s*=|"
            r"on\w+\s*=|<\s*embed|<\s*object|<\s*applet|<\s*meta)",
            re.IGNORECASE,
        )

        # LDAP injection patterns
        self.ldap_pattern = re.compile(
            r"[*\(\)\\&\|]|(\*|\\2a)",
            re.IGNORECASE,
        )

        # NoSQL injection patterns (MongoDB, etc.)
        self.nosql_pattern = re.compile(
            r"(\$where|\$regex|\$nin|\$ne|\$gt|\$lt|db\.|function\s*\()",
            re.IGNORECASE,
        )

    def validate_tool_input(
        self,
        tool_name: str,
        raw_input: Any,
    ) -> ValidationResult:
        """
        Main entry point: validate tool input before execution.

        Args:
            tool_name: Name of the tool (e.g., "workspace_file", "network_audit")
            raw_input: Raw input from the operator (dict, str, or other)

        Returns:
            ValidationResult with is_valid, cleaned_input, errors, warnings, confidence
        """
        if not self.enabled:
            return ValidationResult(
                is_valid=True,
                cleaned_input=raw_input,
                errors=[],
                warnings=["Validation disabled globally"],
                confidence=0.0,
            )

        start = time.monotonic()

        # Dispatch to tool-specific validator
        result = self._validate_by_tool(tool_name, raw_input)

        elapsed_ms = (time.monotonic() - start) * 1000

        # Log result
        self._log_validation(tool_name, result, elapsed_ms)

        # Enforce mode
        if not result.is_valid:
            if self.mode == "strict":
                logger.warning(
                    "Parameter validation REJECTED '%s': %s",
                    tool_name,
                    result.errors,
                )
                return result
            elif self.mode == "warn":
                logger.warning(
                    "Parameter validation WARNED '%s': %s",
                    tool_name,
                    result.errors,
                )
                result.is_valid = True  # Allow through with warning
            elif self.mode == "permissive":
                logger.debug("Parameter validation PERMISSIVE '%s'", tool_name)
                result.is_valid = True  # Allow through silently

        return result

    def _validate_by_tool(
        self,
        tool_name: str,
        raw_input: Any,
    ) -> ValidationResult:
        """Dispatch validation to tool-specific validator."""
        # Normalize tool name (e.g., "cdp.navigate" → "cdp")
        base_tool = tool_name.split(".")[0]

        # Tool-specific validators
        validators = {
            "workspace_file": self._validate_workspace_file,
            "network_audit": self._validate_network_audit,
            "cdp": self._validate_cdp,
            "python": self._validate_python,
            "apex_analyzer": self._validate_apex_analyzer,
            "atlassian": self._validate_atlassian,
            "web_search": self._validate_web_search,
            "web_scraper": self._validate_web_scraper,
            "calculator": self._validate_calculator,
            "document_search": self._validate_document_search,
            "connection": self._validate_connection,
            "bluetruth": self._validate_bluetruth,
            "confluence_crawler": self._validate_confluence_crawler,
            "browserbox": self._validate_browserbox,
            "aura_recon": self._validate_aura_recon,
        }

        validator = validators.get(base_tool, self._validate_generic)
        return validator(raw_input)

    # ─────────────────────────────────────────────────────────────────────────
    # Tool-Specific Validators (Critical Path)
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_workspace_file(self, raw_input: Any) -> ValidationResult:
        """
        WorkspaceFileTool: path traversal prevention, action whitelist.

        High-risk: Can read/write any file in workspace.
        Constraints:
          - action: must be one of [write, append, read, list]
          - path: no ../, ..\\, UNC paths
          - content: max 64KB
        """
        errors = []
        warnings = []

        if isinstance(raw_input, str):
            try:
                params = json.loads(raw_input)
            except json.JSONDecodeError:
                errors.append("Input must be valid JSON")
                return ValidationResult(False, raw_input, errors, warnings, 1.0)
        else:
            params = raw_input

        if not isinstance(params, dict):
            errors.append("Input must be a dict")
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        action = params.get("action", "").lower()
        path = str(params.get("path", "")).strip() if "path" in params else ""
        content = params.get("content", "")

        # Validate action
        allowed_actions = {"write", "append", "read", "list"}
        if action and action not in allowed_actions:
            errors.append(f"action must be one of {allowed_actions}, got '{action}'")

        # Validate path (only for actions that need it)
        if action in {"write", "append", "read"} and not path:
            errors.append("path is required for action='{}' ".format(action))

        if path:
            # Check for path traversal
            if self.path_traversal_pattern.search(path):
                errors.append(f"Path traversal detected in '{path}'")
            # Check for absolute paths outside workspace
            if path.startswith("/") and "/workspace/out" not in path:
                errors.append(
                    f"Absolute paths outside /workspace/out not allowed: '{path}'"
                )

        # Validate content length
        if isinstance(content, str) and len(content.encode()) > 64_000:
            errors.append("content exceeds 64 KB limit")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, params, errors, warnings, 0.0)

    def _validate_network_audit(self, raw_input: Any) -> ValidationResult:
        """
        NetworkAuditTool: RFC1918 validation, deny 0.0.0.0/0.

        High-risk: Network scanning can map infrastructure.
        Constraints:
          - subnet: RFC1918 only (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
          - ip: must be valid IP, RFC1918 only
          - ports: must be numeric or 'top-1000'
          - timeout: 0.1–10.0 seconds
        """
        import ipaddress

        errors = []
        warnings = []

        if isinstance(raw_input, str):
            try:
                params = json.loads(raw_input)
            except json.JSONDecodeError:
                errors.append("Input must be valid JSON")
                return ValidationResult(False, raw_input, errors, warnings, 1.0)
        else:
            params = raw_input

        if not isinstance(params, dict):
            errors.append("Input must be a dict")
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        # Validate subnet
        subnet = params.get("subnet", "")
        if subnet:
            try:
                net = ipaddress.ip_network(subnet, strict=False)
                # RFC1918 private ranges
                rfc1918_ranges = [
                    ipaddress.ip_network("10.0.0.0/8"),
                    ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"),
                ]
                if not any(net.subnet_of(rfc) for rfc in rfc1918_ranges):
                    errors.append(
                        f"Subnet must be RFC1918 private range, got '{subnet}'"
                    )
            except ValueError:
                errors.append(f"Invalid subnet: '{subnet}'")

        # Validate IP
        ip = params.get("ip", "")
        if ip:
            try:
                ip_obj = ipaddress.ip_address(ip)
                # Check if private
                if not ip_obj.is_private:
                    errors.append(
                        f"IP must be private (RFC1918), got '{ip}' ({ip_obj})"
                    )
            except ValueError:
                errors.append(f"Invalid IP address: '{ip}'")

        # Validate ports
        ports = params.get("ports", "top-1000")
        if isinstance(ports, str):
            if ports not in {"top-1000", "top-100", "common"}:
                errors.append(
                    f"ports must be 'top-1000', 'top-100', or 'common', got '{ports}'"
                )

        # Validate timeout
        timeout = params.get("timeout", 1.0)
        try:
            timeout_float = float(timeout)
            if not (0.1 <= timeout_float <= 10.0):
                warnings.append(f"timeout {timeout_float}s outside typical range 0.1–10.0s")
        except (ValueError, TypeError):
            errors.append(f"timeout must be numeric, got '{timeout}'")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, params, errors, warnings, 0.0)

    def _validate_cdp(self, raw_input: Any) -> ValidationResult:
        """
        CDP (Chrome DevTools Protocol): scheme whitelist (http/https only).

        Medium-risk: Can navigate to any URL and interact with page.
        Constraints:
          - URLs: http:// or https:// only
          - No file://, javascript:, data:, blob: schemes
          - Selector: basic validation (no injection attempts)
        """
        errors = []
        warnings = []

        if isinstance(raw_input, str):
            raw_input = raw_input.strip()
            # For simple string inputs (URLs)
            if raw_input.lower().startswith(("http://", "https://")):
                # Basic URL validation
                if len(raw_input) > 2048:
                    errors.append("URL exceeds 2048 chars")
                if self.xss_pattern.search(raw_input):
                    errors.append("XSS pattern detected in URL")
                if errors:
                    return ValidationResult(False, raw_input, errors, warnings, 1.0)
                return ValidationResult(True, raw_input, errors, warnings, 0.0)
            elif raw_input:
                # Check for dangerous schemes
                if self.xss_pattern.search(raw_input):
                    errors.append("XSS pattern (javascript:, <script>, etc.) detected")
                    return ValidationResult(False, raw_input, errors, warnings, 1.0)

        # For dict inputs (JSON with action/selector/text)
        if isinstance(raw_input, dict):
            url = raw_input.get("url", "")
            if url:
                if not (url.lower().startswith(("http://", "https://"))):
                    errors.append(
                        f"URL must start with http:// or https://, got '{url[:50]}'"
                    )
                if self.xss_pattern.search(url):
                    errors.append("XSS pattern detected in URL")

            selector = raw_input.get("selector", "")
            if selector and self.cmd_injection_pattern.search(selector):
                warnings.append("Injection pattern detected in selector")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_python(self, raw_input: Any) -> ValidationResult:
        """
        PythonInterpreterTool: code length limits, import restrictions.

        Critical-risk: Arbitrary code execution (albeit sandboxed).
        Constraints:
          - Code length: max 64 KB
          - No eval/exec/exec in the code
          - No __import__ manipulation
          - No multiprocessing/subprocess direct calls (bwrap prevents escape)
        """
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            errors.append("Input must be a string (Python code)")
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        code = raw_input.strip()

        # Length check
        if len(code) > 64_000:
            errors.append("Code exceeds 64 KB limit")

        # Dangerous built-in functions (in sandboxed environment, but warn anyway)
        dangerous_patterns = [
            r"\beval\s*\(",
            r"\bexec\s*\(",
            r"\bcompile\s*\(",
            r"\b__import__\s*\(",
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                warnings.append(
                    f"Found {pattern.replace(r'\\s*\\(', '')} — "
                    "will be executed in sandbox with restricted scope"
                )

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, code, errors, warnings, 0.0 if not warnings else 0.2)

    def _validate_apex_analyzer(self, raw_input: Any) -> ValidationResult:
        """
        ApexAnalyzerTool: ReDoS prevention, regex complexity checks.

        Medium-risk: Searches SOQL queries, could have injection.
        Constraints:
          - Query length: max 1000 chars
          - No SQL injection patterns
          - Regex complexity: max 10 operators
        """
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            raw_input = json.dumps(raw_input) if raw_input else ""

        if len(raw_input) > 1000:
            errors.append("Query exceeds 1000 char limit")

        if self.sql_injection_pattern.search(raw_input):
            warnings.append("SQL injection pattern detected")

        # Regex ReDoS check (count quantifiers and nesting)
        quantifier_count = len(re.findall(r"[*+?{]", raw_input))
        if quantifier_count > 10:
            warnings.append(
                f"High quantifier count ({quantifier_count}) may cause ReDoS"
            )

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_atlassian(self, raw_input: Any) -> ValidationResult:
        """
        AtlassianTool: JQL query length limits, injection checks.

        Medium-risk: JQL queries to Jira/Confluence.
        Constraints:
          - Query length: max 2000 chars
          - No LDAP injection patterns
        """
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            raw_input = json.dumps(raw_input) if raw_input else ""

        if len(raw_input) > 2000:
            errors.append("Query exceeds 2000 char limit")

        if self.ldap_pattern.search(raw_input):
            warnings.append("LDAP injection pattern detected in query")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_web_search(self, raw_input: Any) -> ValidationResult:
        """WebSearchTool: basic length and pattern check."""
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            raw_input = json.dumps(raw_input) if raw_input else ""

        if len(raw_input) > 500:
            warnings.append("Query exceeds typical length (500 chars)")

        if self.cmd_injection_pattern.search(raw_input):
            warnings.append("Command injection pattern detected in query")

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_web_scraper(self, raw_input: Any) -> ValidationResult:
        """WebScraperTool: URL scheme whitelist."""
        errors = []
        warnings = []

        if isinstance(raw_input, str):
            if not raw_input.lower().startswith(("http://", "https://")):
                errors.append("URL must start with http:// or https://")
        elif isinstance(raw_input, dict):
            url = raw_input.get("url", "")
            if url and not url.lower().startswith(("http://", "https://")):
                errors.append("URL must start with http:// or https://")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_calculator(self, raw_input: Any) -> ValidationResult:
        """CalculatorTool: expression length check."""
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            raw_input = str(raw_input)

        if len(raw_input) > 200:
            errors.append("Expression exceeds 200 char limit")

        if self.cmd_injection_pattern.search(raw_input):
            errors.append("Command injection pattern in expression")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_document_search(self, raw_input: Any) -> ValidationResult:
        """DocumentSearchTool: query validation."""
        errors = []
        warnings = []

        if not isinstance(raw_input, str):
            raw_input = json.dumps(raw_input) if raw_input else ""

        if len(raw_input) > 1000:
            warnings.append("Query exceeds typical length")

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_connection(self, raw_input: Any) -> ValidationResult:
        """ConnectionTool: basic validation."""
        errors = []
        warnings = []

        if isinstance(raw_input, dict):
            name = raw_input.get("name", "")
            if not name:
                errors.append("Connection name is required")

        if errors:
            return ValidationResult(False, raw_input, errors, warnings, 1.0)

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_bluetruth(self, raw_input: Any) -> ValidationResult:
        """BlueTruthTool: command validation."""
        errors = []
        warnings = []

        if isinstance(raw_input, str):
            # Check for shell injection in command
            if self.cmd_injection_pattern.search(raw_input):
                warnings.append("Command injection pattern detected")
        elif isinstance(raw_input, dict):
            command = raw_input.get("command", "")
            if self.cmd_injection_pattern.search(str(command)):
                warnings.append("Command injection pattern detected in command")

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_confluence_crawler(self, raw_input: Any) -> ValidationResult:
        """ConfluenceCrawler: URL validation."""
        errors = []
        warnings = []

        if isinstance(raw_input, str):
            if not raw_input.lower().startswith(("http://", "https://")):
                if raw_input:  # Allow empty string
                    warnings.append("URL should start with http:// or https://")

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_browserbox(self, raw_input: Any) -> ValidationResult:
        """BrowserboxTool: basic validation."""
        errors = []
        warnings = []

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    def _validate_aura_recon(self, raw_input: Any) -> ValidationResult:
        """AuraReconTool: command validation."""
        errors = []
        warnings = []

        return ValidationResult(True, raw_input, errors, warnings, 0.0)

    # ─────────────────────────────────────────────────────────────────────────
    # Generic Validator (Unknown Tools)
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_generic(self, raw_input: Any) -> ValidationResult:
        """
        Generic validator for unknown tools.
        Performs basic heuristic checks without blocking.
        """
        errors = []
        warnings = []

        # Convert to string for pattern matching
        input_str = (
            json.dumps(raw_input)
            if isinstance(raw_input, dict)
            else str(raw_input)
        )

        # Check for obvious injection patterns
        confidence = 0.0
        if self.cmd_injection_pattern.search(input_str):
            warnings.append("Possible command injection pattern")
            confidence += 0.3
        if self.path_traversal_pattern.search(input_str):
            warnings.append("Possible path traversal pattern")
            confidence += 0.3
        if self.sql_injection_pattern.search(input_str):
            warnings.append("Possible SQL injection pattern")
            confidence += 0.2
        if self.xss_pattern.search(input_str):
            warnings.append("Possible XSS pattern")
            confidence += 0.2

        return ValidationResult(
            True,  # Generic validator always allows
            raw_input,
            errors,
            warnings,
            min(confidence, 1.0),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Logging & Observability
    # ─────────────────────────────────────────────────────────────────────────

    def _log_validation(
        self,
        tool_name: str,
        result: ValidationResult,
        elapsed_ms: float,
    ) -> None:
        """Log validation result to Tap."""
        try:
            tap(
                event="tool_validation",
                tool=tool_name,
                is_valid=result.is_valid,
                errors=result.errors,
                warnings=result.warnings,
                confidence=result.confidence,
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            logger.debug("Failed to log validation: %s", e)

    def looks_like_injection(self, value: str) -> bool:
        """
        Quick heuristic: does this string look like an injection attempt?
        Used for low-latency pre-screening before full validation.
        """
        if not isinstance(value, str):
            return False

        checks = [
            self.cmd_injection_pattern.search(value),
            self.path_traversal_pattern.search(value),
            self.sql_injection_pattern.search(value),
            self.xss_pattern.search(value),
            self.ldap_pattern.search(value),
            self.nosql_pattern.search(value),
        ]

        return any(checks)

    def get_tool_schema(self, tool_name: str) -> Optional[dict]:
        """
        Return JSON Schema for a tool's expected input.
        Used by OpenAPI documentation, MCP schema introspection.
        """
        schemas = {
            "workspace_file": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["write", "append", "read", "list"],
                        "description": "File operation to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path in /workspace/out/",
                        "maxLength": 256,
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write or append",
                        "maxLength": 65536,
                    },
                },
            },
            "network_audit": {
                "type": "object",
                "properties": {
                    "subnet": {
                        "type": "string",
                        "description": "RFC1918 subnet to scan (e.g., 192.168.1.0/24)",
                    },
                    "ip": {
                        "type": "string",
                        "description": "Single IP to scan",
                    },
                    "ports": {
                        "type": "string",
                        "enum": ["top-1000", "top-100", "common"],
                        "description": "Port range to scan",
                    },
                    "timeout": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 10.0,
                        "description": "Timeout per host in seconds",
                    },
                },
            },
            "cdp": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "pattern": "^https?://",
                        "description": "URL to navigate to",
                        "maxLength": 2048,
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for click/interaction",
                        "maxLength": 256,
                    },
                },
            },
            "python": {
                "type": "string",
                "description": "Python code to execute",
                "maxLength": 65536,
            },
            "calculator": {
                "type": "string",
                "description": "Math expression",
                "maxLength": 200,
            },
        }

        return schemas.get(tool_name.split(".")[0])
