"""
ConnectionTool — operator tool for making credentialed API calls.

The agent never sees a raw token. It calls a named connection; the registry
injects the Authorization header internally and returns only the response body.

Input JSON:
  {"connection": "github", "method": "GET", "path": "/user/repos"}
  {"connection": "github", "method": "POST", "path": "/gists", "body": {...}}

The "connection" name must exist in config.yaml connections: section AND
have a token stored via: python -m beigebox.connections add <name>
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class ConnectionTool:
    description = (
        "Make authenticated API calls via named connections. "
        "Input: JSON {\"connection\": \"name\", \"method\": \"GET|POST|PUT|DELETE\", "
        "\"path\": \"/endpoint\", \"body\": {...}}. "
        "The connection name must be configured in config.yaml and have a stored token. "
        "Tokens are never visible — only the response body is returned. "
        "List available connections with: connection=list (special input). "
        "Example: {\"connection\": \"github\", \"method\": \"GET\", \"path\": \"/user\"}"
    )

    def __init__(self, registry):
        self._registry = registry

    def run(self, input_str: str) -> str:
        input_str = input_str.strip()

        # Special shorthand: just "list"
        if input_str.lower() in ("list", '"list"', "list connections"):
            names = self._registry.list_connections()
            return f"Available connections: {', '.join(names)}" if names else "No connections configured."

        try:
            params = json.loads(input_str)
        except json.JSONDecodeError:
            return (
                'Error: input must be JSON — '
                '{"connection": "name", "method": "GET", "path": "/endpoint"}'
            )

        name   = params.get("connection", "")
        method = params.get("method", "GET")
        path   = params.get("path", "")
        body   = params.get("body")

        if not name:
            return "Error: missing 'connection' field"
        if not path:
            return "Error: missing 'path' field"

        try:
            result = self._registry.call(name, method, path, body=body)
            status = result["status"]
            body_text = result["body"]
            prefix = f"[HTTP {status}]\n" if status != 200 else ""
            return prefix + body_text
        except PermissionError as e:
            return f"Access denied: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("connection.call failed: %s", e)
            return f"Error: {e}"
