"""
Tool input validation registry.

Manages Pydantic models for tool parameter validation.
Called before tool.run() to validate and coerce inputs.

Usage:
    from beigebox.tools.validation_schemas import validate_tool_input

    try:
        validated_input = validate_tool_input("workspace_file", input_str)
        result = tool.run(validated_input)
    except ValidationError as e:
        return f"Invalid input: {e.summary()}"
"""

import json
import logging
from typing import Type, Dict, Any, Optional, Tuple
from pydantic import BaseModel, ValidationError

from beigebox.tools.schemas import (
    WorkspaceFileInput,
    NetworkAuditScanNetworkInput,
    NetworkAuditScanDeviceInput,
    NetworkAuditFingerprintServiceInput,
    NetworkAuditCheckVulnInput,
    PythonInterpreterInput,
    CDPNavigateInput,
    CDPClickInput,
    CDPEvaluateInput,
    ApexAnalyzerInput,
    AtlassianSearchJQLInput,
    AtlassianConfluenceSearchInput,
    WebSearchInput,
    CalculatorInput,
    DateTimeInput,
    MemoryInput,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema Registry
# ─────────────────────────────────────────────────────────────────────────────

# Single-schema tools (input is a single JSON object)
TOOL_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "workspace_file": WorkspaceFileInput,
    "python": PythonInterpreterInput,
    "web_search": WebSearchInput,
    "calculator": CalculatorInput,
    "datetime": DateTimeInput,
    "memory": MemoryInput,
}

# Command-based tools (input is "command arg1=val1 arg2=val2")
COMMAND_SCHEMAS: Dict[str, Dict[str, Type[BaseModel]]] = {
    "network_audit": {
        "scan_network": NetworkAuditScanNetworkInput,
        "scan_device": NetworkAuditScanDeviceInput,
        "fingerprint_service": NetworkAuditFingerprintServiceInput,
        "check_vulnerabilities": NetworkAuditCheckVulnInput,
        "get_status": None,  # No parameters
    },
}

# CDP nested format (input is {"tool": "...", "input": "..."})
CDP_SUB_TOOLS: Dict[str, Type[BaseModel]] = {
    "navigate": CDPNavigateInput,
    "click": CDPClickInput,
    "evaluate": CDPEvaluateInput,
}

# Atlassian actions
ATLASSIAN_ACTIONS: Dict[str, Type[BaseModel]] = {
    "search_jql": AtlassianSearchJQLInput,
    "search_confluence": AtlassianConfluenceSearchInput,
}

# Apex Analyzer (unified schema)
APEX_ANALYZER_SCHEMA = ApexAnalyzerInput

# User-friendly hints for error messages
TOOL_HINTS: Dict[str, str] = {
    "workspace_file": '{"action": "read|write|append|list", "path": "filename.md", "content": "..."}',
    "network_audit": "command [param=value ...]\n  Examples:\n    scan_network subnet=192.168.1.0/24\n    scan_device ip=192.168.1.1\n    check_vulnerabilities service=OpenSSH version=7.4",
    "python": '{"code": "print(2+2)"}',
    "cdp": '{"tool": "navigate|click|evaluate", "input": {...}}',
    "web_search": '{"query": "search terms", "max_results": 5}',
    "calculator": '{"expression": "2+2"}',
    "datetime": '{"action": "now|format", "timestamp": 1234567890}',
    "memory": '{"query": "what do you remember?", "max_results": 3}',
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def validate_tool_input(tool_name: str, input_text: str) -> str:
    """
    Validate and normalize tool input. Returns validated input as JSON string.

    Args:
        tool_name: Name of the tool (e.g., "workspace_file")
        input_text: Raw input (usually JSON, sometimes key=value for command tools)

    Returns:
        Validated input as JSON string (suitable for tool.run())

    Raises:
        ValidationError: If input is invalid
    """
    if tool_name in COMMAND_SCHEMAS:
        # Command-based tool (network_audit)
        return _validate_command_tool(tool_name, input_text)

    elif tool_name == "cdp":
        # CDP tool with nested structure
        return _validate_cdp_tool(input_text)

    elif tool_name in TOOL_SCHEMAS:
        # Standard JSON schema
        schema = TOOL_SCHEMAS[tool_name]
        return _validate_json_tool(tool_name, input_text, schema)

    else:
        # Tool not in validation registry — pass through with basic checks
        logger.debug(f"Tool '{tool_name}' not in validation registry; skipping validation")
        return input_text


def validate_tool_arguments(tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    Validate MCP tool arguments (from tools/call), return validated input string.

    Args:
        tool_name: Tool name
        arguments: Arguments dict from MCP call

    Returns:
        Validated input as JSON string
    """
    # Try standard validation first
    try:
        if tool_name in TOOL_SCHEMAS:
            schema = TOOL_SCHEMAS[tool_name]
            validated = schema(**arguments)
            return json.dumps(validated.model_dump())
    except Exception:
        pass

    # Fall back to raw JSON
    return json.dumps(arguments)


def get_schema_for_tool(tool_name: str) -> Optional[Type[BaseModel]]:
    """Get the Pydantic schema for a tool."""
    if tool_name in TOOL_SCHEMAS:
        return TOOL_SCHEMAS[tool_name]
    if tool_name in COMMAND_SCHEMAS:
        # Command tools don't have a single schema (command-dependent)
        return None
    return None


def get_tool_schema_json(tool_name: str) -> Dict[str, Any]:
    """
    Get JSON Schema for a tool (for MCP tools/list or OpenAPI).

    Returns a JSON Schema object suitable for tool.inputSchema.
    """
    schema_model = get_schema_for_tool(tool_name)
    if not schema_model:
        # Fallback for tools without schemas
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": f"Input for {tool_name}"
                }
            },
            "required": ["input"],
        }

    # Generate JSON Schema from Pydantic model
    json_schema = schema_model.model_json_schema()
    return {
        "type": "object",
        "properties": {
            "input": json_schema
        },
        "required": ["input"],
    }


def get_schema_hint(tool_name: str) -> str:
    """Get user-friendly hint for tool input format."""
    return TOOL_HINTS.get(tool_name, "(no hint available)")


# ─────────────────────────────────────────────────────────────────────────────
# Internal Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _validate_json_tool(
    tool_name: str, input_text: str, schema: Type[BaseModel]
) -> str:
    """Validate JSON input against Pydantic schema."""
    try:
        # Try parsing as JSON first
        data = json.loads(input_text)
    except json.JSONDecodeError as e:
        raise ValidationError(
            f"Invalid JSON input: {e}. Expected format: {get_schema_hint(tool_name)}"
        )

    # Validate with Pydantic model
    try:
        validated = schema(**data)
    except ValidationError as e:
        logger.debug(f"Validation error for {tool_name}: {e}")
        raise

    # Return validated data as JSON string
    return json.dumps(validated.model_dump())


def _validate_command_tool(tool_name: str, input_text: str) -> str:
    """Validate command-based tool (key=value format)."""
    if tool_name != "network_audit":
        raise ValueError(f"Unknown command-based tool: {tool_name}")

    # Parse command and arguments
    parts = input_text.strip().split()
    if not parts:
        raise ValidationError(
            "Empty command. Use: scan_network, scan_device, fingerprint_service, check_vulnerabilities"
        )

    command = parts[0].lower()
    kwargs = _parse_keyvalue_args(parts[1:])

    # Get schema for this command
    schema = COMMAND_SCHEMAS["network_audit"].get(command)
    if schema is None:
        if command == "get_status":
            # No parameters needed
            return json.dumps({"command": command})
        raise ValidationError(
            f"Unknown command: {command}. Use: {', '.join(COMMAND_SCHEMAS['network_audit'].keys())}"
        )

    # Validate arguments
    try:
        validated = schema(**kwargs)
    except ValidationError as e:
        logger.debug(f"Validation error for {tool_name} {command}: {e}")
        raise

    # Return as JSON suitable for tool.run()
    result = {"command": command}
    result.update(validated.model_dump())
    return json.dumps(result)


def _validate_cdp_tool(input_text: str) -> str:
    """Validate CDP tool with nested structure."""
    try:
        data = json.loads(input_text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON: {e}")

    if not isinstance(data, dict):
        raise ValidationError(f"CDP input must be a dict, got {type(data).__name__}")

    tool = data.get("tool", "").lower()
    tool_input = data.get("input", "")

    if tool not in CDP_SUB_TOOLS:
        raise ValidationError(
            f"Unknown CDP subtool: {tool}. Use: {', '.join(CDP_SUB_TOOLS.keys())}"
        )

    schema = CDP_SUB_TOOLS[tool]

    # Validate the input
    if isinstance(tool_input, dict):
        validated = schema(**tool_input)
    elif isinstance(tool_input, str):
        # For simple string inputs (URL, selector)
        validated = schema(url=tool_input) if tool == "navigate" else schema(selector=tool_input)
    else:
        raise ValidationError(f"tool_input must be dict or str, got {type(tool_input).__name__}")

    return json.dumps({
        "tool": tool,
        "input": validated.model_dump()
    })


def _parse_keyvalue_args(parts: list[str]) -> Dict[str, str]:
    """Parse 'key=value key=value' format into dict."""
    result = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.lower()] = value
        else:
            # Boolean flag (no value)
            result[part.lower()] = "true"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry
# ─────────────────────────────────────────────────────────────────────────────

def looks_like_injection(error: ValidationError) -> bool:
    """
    Heuristic: Does this validation error look like a security attack vs. user mistake?
    """
    error_msgs = [str(e.get("msg", "")).lower() for e in error.errors()]

    # Patterns suggestive of attack
    attack_indicators = [
        "traversal", "escape", "shell", "injection",
        "scheme", "javascript", "command"
    ]

    for msg in error_msgs:
        for indicator in attack_indicators:
            if indicator in msg:
                return True

    return False


def log_validation_failure(
    tool_name: str,
    error: ValidationError,
    input_sample: str = ""
) -> None:
    """Log validation failure (for security audit)."""
    from beigebox.logging import log_error_event

    is_attack = looks_like_injection(error)
    severity = "security" if is_attack else "warning"

    error_summary = "; ".join(str(e.get("msg", "")) for e in error.errors())

    try:
        log_error_event(
            "tool_input_validation_failed",
            f"{tool_name}: {error_summary}",
            severity=severity,
            tool_name=tool_name,
            error_count=len(error.errors()),
            looks_like_attack=is_attack,
        )
    except Exception as e:
        logger.debug(f"Failed to log validation error: {e}")
