"""
Z-Commands — user-level routing overrides.

Prefix a message with 'z: <directive>' to bypass all routing logic.
The z: prefix is stripped before the message reaches the LLM.
The wiretap logs the override for debugging.

Syntax:
    z: simple          → force simple/fast model
    z: complex         → force complex/large model
    z: code            → force code route
    z: reason          → force reasoning route
    z: search          → force web search tool
    z: memory          → force memory/RAG lookup
    z: calc <expr>     → force calculator tool
    z: time            → force datetime tool
    z: sysinfo         → force system info tool
    z: <model_name>    → force specific model (e.g. z: llama3:8b)
    z: help            → list available z-commands

Chaining:
    z: complex,search  → force complex model AND run web search

The z: prefix is case-insensitive. Everything after the directives
is the actual user message.

Examples:
    "z: code How do I implement a binary tree in Rust?"
    "z: complex,search What happened in the news today?"
    "z: llama3:8b Explain quantum entanglement"
    "z: calc 2**16 + 3**10"
    "z: help"
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Recognized route aliases
ROUTE_ALIASES = {
    "simple": "fast",
    "easy": "fast",
    "fast": "fast",
    "complex": "large",
    "hard": "large",
    "large": "large",
    "code": "code",
    "coding": "code",
    "reason": "large",
    "reasoning": "large",
    "default": "default",
}

# Recognized tool directives
TOOL_DIRECTIVES = {
    "search": "web_search",
    "websearch": "web_search",
    "memory": "memory",
    "rag": "memory",
    "recall": "memory",
    "calc": "calculator",
    "math": "calculator",
    "time": "datetime",
    "date": "datetime",
    "clock": "datetime",
    "sysinfo": "system_info",
    "system": "system_info",
    "status": "system_info",
}

# The z: prefix pattern
Z_PATTERN = re.compile(
    r"^\s*z:\s*(.+)",
    re.IGNORECASE,
)

HELP_TEXT = """Available z-commands:

  ROUTING
    z: simple/easy/fast    → route to fast model
    z: complex/hard/large  → route to large model
    z: code/coding         → route to code model
    z: <model:tag>         → route to exact model (e.g. llama3:8b)

  TOOLS
    z: search              → force web search
    z: memory/rag/recall   → search past conversations
    z: calc/math <expr>    → evaluate math expression
    z: time/date/clock     → current time and date
    z: sysinfo/system      → system resource stats

  CHAINING
    z: complex,search      → combine multiple directives

  META
    z: help                → show this help"""


@dataclass
class ZCommand:
    """Parsed z-command result."""
    active: bool = False            # True if a z: prefix was found
    route: str = ""                 # Route name to force (empty = don't override)
    model: str = ""                 # Specific model to force (empty = use route)
    tools: list[str] = field(default_factory=list)  # Tools to force
    tool_input: str = ""            # Input for tool-only commands (e.g. calc expression)
    message: str = ""               # The actual user message (z: prefix stripped)
    raw_directives: str = ""        # The raw directive string for logging
    is_help: bool = False           # True if z: help


def parse_z_command(text: str) -> ZCommand:
    """
    Parse a user message for z: command prefix.

    Returns a ZCommand. If no z: prefix is found, returns
    ZCommand(active=False, message=<original text>).
    """
    match = Z_PATTERN.match(text)
    if not match:
        return ZCommand(active=False, message=text)

    rest = match.group(1).strip()

    # Split directives from the actual message
    # Directives are comma-separated words before the first "real" content
    parts = rest.split(None, 1)  # Split on first whitespace
    if not parts:
        return ZCommand(active=False, message=text)

    first_token = parts[0].rstrip(",").lower()
    remaining = parts[1] if len(parts) > 1 else ""

    # Check for help
    if first_token == "help":
        return ZCommand(active=True, is_help=True, message=HELP_TEXT, raw_directives="help")

    # Parse comma-separated directives from the first token
    directive_tokens = [d.strip().lower() for d in first_token.split(",") if d.strip()]

    route = ""
    model = ""
    tools = []
    tool_input = ""

    for directive in directive_tokens:
        # Check route aliases
        if directive in ROUTE_ALIASES:
            route = ROUTE_ALIASES[directive]
            continue

        # Check tool directives
        if directive in TOOL_DIRECTIVES:
            tool_name = TOOL_DIRECTIVES[directive]
            tools.append(tool_name)
            # For calc, the remaining text IS the expression
            if tool_name == "calculator" and remaining:
                tool_input = remaining
            continue

        # Check if it looks like a model string (contains : or /)
        if ":" in directive or "/" in directive:
            model = directive
            continue

        # Unknown directive — could be start of the actual message
        # Reconstruct: put it back with the rest
        remaining = f"{directive} {remaining}".strip() if remaining else directive

    cmd = ZCommand(
        active=True,
        route=route,
        model=model,
        tools=tools,
        tool_input=tool_input,
        message=remaining,
        raw_directives=first_token,
    )

    logger.info(
        "z-command: directives=%s route=%s model=%s tools=%s",
        first_token, route or "(none)", model or "(none)", tools or "(none)",
    )

    return cmd
