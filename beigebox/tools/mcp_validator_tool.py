"""
MCP Parameter Validator Tool — callable from the Operator / MCP.

Wraps the security.mcp_parameter_validator.ParameterValidator so it can be
invoked as a regular tool:

    {"tool": "mcp_parameter_validator", "input": "{\"tool\": \"workspace_file\", \"params\": {...}}"}

Also supports batch validation:

    {"tool": "mcp_parameter_validator", "input": "{\"batch\": [{\"tool\": ..., \"params\": ...}, ...]}"}

Configuration (config.yaml):
    security:
      mcp_validator:
        enabled: true
        allow_unsafe: false
        log_violations: true
"""

from __future__ import annotations

import json
import logging

from beigebox.config import get_config
from beigebox.security.mcp_parameter_validator import ParameterValidator, MCPValidationResult

logger = logging.getLogger(__name__)


class MCPValidatorTool:
    """
    Tool that validates MCP tool parameters before execution.

    Callable from the Operator as a regular tool, or used internally
    by the pre-execution hook.
    """

    name = "mcp_parameter_validator"
    description = (
        "Validate tool parameters for security before execution. "
        'Input: {"tool": "tool_name", "params": {...}} or '
        '{"batch": [{"tool": "tool_name", "params": {...}}, ...]}. '
        "Returns validation result with issues and sanitized params."
    )

    def __init__(
        self,
        validator: ParameterValidator | None = None,
        allow_unsafe: bool = False,
        log_violations: bool = True,
    ):
        cfg = get_config()
        sec_cfg = cfg.get("security", {}).get("mcp_validator", {})

        self.allow_unsafe = sec_cfg.get("allow_unsafe", allow_unsafe)
        self.log_violations = sec_cfg.get("log_violations", log_violations)

        # Build or reuse the validator
        workspace_cfg = cfg.get("workspace", {})
        workspace_path = workspace_cfg.get("path", "./workspace")

        self.validator = validator or ParameterValidator(
            workspace_root=workspace_path,
            allow_localhost_cdp=sec_cfg.get("allow_localhost_cdp", False),
            max_code_length=sec_cfg.get("max_code_length", 10_000),
            max_query_length=sec_cfg.get("max_query_length", 4_000),
            max_network_cidr=sec_cfg.get("max_network_cidr", 24),
            max_ports=sec_cfg.get("max_ports", 100),
            max_network_timeout=sec_cfg.get("max_network_timeout", 30.0),
        )

        logger.info(
            "MCPValidatorTool initialized (allow_unsafe=%s, log_violations=%s)",
            self.allow_unsafe, self.log_violations,
        )

    def run(self, input_text: str) -> str:
        """
        Validate tool parameters.

        Input formats:
          - Single: {"tool": "workspace_file", "params": {"action": "read", "path": "..."}}
          - Batch:  {"batch": [{"tool": ..., "params": ...}, ...]}
        """
        try:
            data = json.loads(input_text) if isinstance(input_text, str) else input_text
        except (json.JSONDecodeError, TypeError):
            return json.dumps({
                "valid": False,
                "issues": [{"tier": "schema", "severity": "critical",
                            "message": "Input must be valid JSON"}],
                "sanitized_params": {},
            })

        # Batch mode
        if isinstance(data, dict) and "batch" in data:
            results = self.validator.validate_batch(data["batch"])
            output = {
                "batch_valid": all(r.valid for r in results),
                "results": [r.to_dict() for r in results],
            }
            if self.log_violations:
                for i, r in enumerate(results):
                    if not r.valid:
                        self._log_violation(data["batch"][i].get("tool", "unknown"), r)
            return json.dumps(output)

        # Single mode
        tool_name = data.get("tool", "")
        params = data.get("params", data.get("input", {}))

        if not tool_name:
            return json.dumps({
                "valid": False,
                "issues": [{"tier": "schema", "severity": "high",
                            "message": "'tool' field is required"}],
                "sanitized_params": {},
            })

        result = self.validator.validate(tool_name, params)

        if self.log_violations and not result.valid:
            self._log_violation(tool_name, result)

        return json.dumps(result.to_dict())

    def validate_before_execution(
        self, tool_name: str, params: any,
    ) -> MCPValidationResult:
        """
        Direct validation call (used by the pre-execution hook).
        Returns MCPValidationResult without JSON serialization.
        """
        result = self.validator.validate(tool_name, params)

        if self.log_violations and not result.valid:
            self._log_violation(tool_name, result)

        return result

    def _log_violation(self, tool_name: str, result: MCPValidationResult) -> None:
        """Log a validation violation to the standard logger and Tap wire."""
        attack_types = {i.attack_type for i in result.issues}
        severities = {i.severity for i in result.issues}
        messages = [i.message for i in result.issues]

        logger.warning(
            "MCP_VALIDATION_BLOCKED tool=%s attacks=%s severities=%s: %s",
            tool_name,
            sorted(attack_types),
            sorted(severities),
            "; ".join(messages),
        )

        # Try to log to Tap wire (non-blocking)
        try:
            from beigebox.logging import log_tool_call
            log_tool_call(
                tool_name,
                "validation_blocked",
                latency_ms=result.elapsed_ms,
            )
        except Exception:
            pass
