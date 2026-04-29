#!/usr/bin/env bash
# scaffold.sh — create a new BeigeBox tool skeleton.
#
# Writes:
#   beigebox/tools/<name>.py                 (module skeleton)
#   tests/test_<name>_tool.py                (init + happy + error tests)
# Prints:
#   the registry import + instantiation block to paste into
#   beigebox/tools/registry.py, and the config.yaml block to append.
#
# Idempotent-refusing: will not overwrite existing files.
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: scaffold.sh [--default-enabled] [--input-shape natural|json] [--repo-root DIR] <tool-name>

Creates a new tool module + test stub matching the BeigeBox conventions.
The class name is derived as PascalCase + "Tool" (e.g. foo_bar -> FooBarTool).

Options:
  --default-enabled       Generate config block with enabled: true (default: false)
  --input-shape <kind>    'natural' (raw string) or 'json' (parsed dict). Default: natural.
  --repo-root DIR         Override repo root. Default: auto-detect from script location.
  -h, --help              Show this help

Tool names must be lower_snake_case ([a-z0-9_]+).
EOF
}

default_enabled=0
input_shape="natural"
repo_root=""
name=""

while [ $# -gt 0 ]; do
  case "$1" in
    --default-enabled) default_enabled=1; shift ;;
    --input-shape)     input_shape="$2"; shift 2 ;;
    --repo-root)       repo_root="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage; exit 2 ;;
    *)  if [ -z "$name" ]; then name="$1"; shift; else echo "unexpected arg: $1" >&2; usage; exit 2; fi ;;
  esac
done

if [ -z "$name" ]; then usage; exit 2; fi
if ! [[ "$name" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "tool name must be lower_snake_case (got: $name)" >&2
  exit 2
fi
if [ "$input_shape" != "natural" ] && [ "$input_shape" != "json" ]; then
  echo "--input-shape must be 'natural' or 'json' (got: $input_shape)" >&2
  exit 2
fi

# PascalCase the snake_case name and append "Tool"
class_name="$(echo "$name" | awk -F_ '{ for(i=1;i<=NF;i++) printf "%s%s", toupper(substr($i,1,1)), substr($i,2); }')Tool"

if [ -z "$repo_root" ]; then
  here="$(cd "$(dirname "$0")" && pwd)"
  # script lives at beigebox/skills/make-tool/scripts/scaffold.sh
  guess="$(cd "$here/../../../.." && pwd)"
  if [ -d "$guess/beigebox/tools" ]; then
    repo_root="$guess"
  else
    repo_root="$(pwd)"
  fi
fi

tool_path="$repo_root/beigebox/tools/$name.py"
test_path="$repo_root/tests/test_${name}_tool.py"

if [ -e "$tool_path" ]; then echo "refusing to overwrite: $tool_path" >&2; exit 1; fi
if [ -e "$test_path" ]; then echo "refusing to overwrite: $test_path" >&2; exit 1; fi

mkdir -p "$repo_root/beigebox/tools" "$repo_root/tests"

# ---- module ----
if [ "$input_shape" = "json" ]; then
  parse_block=$(cat <<'PY'
        try:
            params = json.loads(input_str)
        except json.JSONDecodeError:
            return 'Error: input must be JSON, e.g. {"key": "value"}'
        if not isinstance(params, dict):
            return "Error: input must be a JSON object"
PY
)
  imports="import json\nimport logging"
  desc_example='{"tool": "'"$name"'", "input": {"key": "value"}}'
else
  parse_block=$(cat <<'PY'
        text = input_str.strip()
        if not text:
            return "Error: empty input"
PY
)
  imports="import logging"
  desc_example='{"tool": "'"$name"'", "input": "natural language phrasing"}'
fi

cat > "$tool_path" <<EOF
"""
$class_name — <one-line pitch>.

<Why this tool exists / what gap it fills.>

Examples the decision LLM would route here:
  "<phrasing 1>"
  "<phrasing 2>"
"""
$(printf '%b' "$imports")

logger = logging.getLogger(__name__)

__version__ = 1


class $class_name:
    """<One-line class summary>."""

    description = (
        "<Verb-first one-sentence pitch>. "
        "Input: <natural language | JSON shape>. "
        'Example: $desc_example.'
    )

    def __init__(self):
        # Constructor shape is up to you. Add config-driven kwargs
        # (api_key, timeout, base_url) or injected dependencies
        # (registry, vector_store) as needed. Update the registry.py
        # paste block printed below to match.
        pass

    def run(self, input_str: str) -> str:
        """<What this returns>. Never raises — error strings only."""
$parse_block

        try:
            # TODO: implement
            return "not yet implemented"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("$name.run failed: %s", e)
            return f"Error: {e}"
EOF

# ---- test ----
cat > "$test_path" <<EOF
"""Tests for $class_name."""
from beigebox.tools.$name import $class_name


def test_${name}_init():
    tool = $class_name()
    assert tool is not None


def test_${name}_empty_input_returns_error():
    tool = $class_name()
    out = tool.run("")
    assert isinstance(out, str)
    # Error flavors vary across tools ("Error: ...", "Could not ...", "[HTTP 4xx]").
    # Tighten this assertion to your tool's specific error string once implemented.
    assert len(out) > 0


def test_${name}_happy_path():
    tool = $class_name()
    out = tool.run("sample input")
    assert isinstance(out, str)
    # TODO: tighten this assertion once run() is implemented
EOF

# ---- enabled default ----
if [ "$default_enabled" -eq 1 ]; then
  enabled_default="True"
  enabled_yaml="true"
else
  enabled_default="False"
  enabled_yaml="false"
fi

echo "created $tool_path"
echo "created $test_path"
echo
echo "─── PASTE INTO beigebox/tools/registry.py ─────────────────────────────"
echo "1) Add to imports near the other tool imports:"
echo
echo "    from beigebox.tools.$name import $class_name"
echo
echo "2) Add an instantiation block inside ToolRegistry.__init__:"
echo
echo "    # --- $class_name ---"
echo "    ${name}_cfg = tools_cfg.get(\"$name\", {})"
echo "    if ${name}_cfg.get(\"enabled\", $enabled_default):"
echo "        # Adjust kwargs to match your $class_name.__init__ signature."
echo "        self.tools[\"$name\"] = $class_name()"
echo "        logger.info(\"$name tool registered\")"
echo
echo "─── APPEND TO config.yaml under tools: ────────────────────────────────"
echo
echo "  # ── $class_name (<one-line pitch>) ─────────────────────────────────"
echo "  # Requires: <daemons / env vars / installs>"
echo "  # Operator calls: $desc_example"
echo "  $name:"
echo "    enabled: $enabled_yaml"
echo "    # Add tool-specific config keys here (api_key: \${${name^^}_API_KEY:-}, timeout: 10, etc.)"
echo
