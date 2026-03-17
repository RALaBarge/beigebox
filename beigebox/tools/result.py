"""
Structured tool result for operator tool calls.

Tools can return either a plain string (backward-compat) or a ToolResult.
The operator's _run_tool() detects ToolResult and uses status for loop
detection, and formats the observation with context to help the model
make better next-step decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """
    Structured result from a tool call.

    Fields
    ------
    status : "ok" | "error" | "partial"
    data   : main content string
    hint   : optional next-step hint shown on success
    recovery_hint : shown on error — suggests an alternative approach
    metadata : optional key-value extras (url, content_type, size, etc.)
    """

    status: str
    data: str
    hint: str | None = None
    recovery_hint: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_observation(self) -> str:
        """
        Compact text representation injected into the operator's context window.

        Format (success):
          [status: ok]
          <data>
          hint: <hint>              (omitted when None)

        Format (error):
          [status: error]
          <data>
          recovery_hint: <hint>     (omitted when None)
        """
        lines = [f"[status: {self.status}]", self.data]
        if self.hint and self.status != "error":
            lines.append(f"hint: {self.hint}")
        if self.recovery_hint and self.status == "error":
            lines.append(f"recovery_hint: {self.recovery_hint}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_observation()
