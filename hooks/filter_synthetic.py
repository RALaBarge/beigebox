"""
Example hook: filter Open WebUI synthetic requests.

Open WebUI sends auto-generated follow-up suggestion requests after
every real message. These clutter the conversation log with metadata.

This hook detects and tags them so the proxy can skip logging.

To use: copy this file to your hooks/ directory and enable in config.yaml.
"""


def pre_request(body: dict, context: dict) -> dict:
    """
    Detect Open WebUI's follow-up suggestion requests and tag them.

    These requests typically contain a system-like prompt starting with
    "### Task:" asking the model to suggest follow-up questions.
    """
    messages = body.get("messages", [])
    if not messages:
        return body

    last_msg = messages[-1]
    content = last_msg.get("content", "")

    # Open WebUI follow-up detection patterns
    synthetic_markers = [
        "### Task:",
        "Suggest 3-5 relevant follow-up",
        "suggest follow-up questions",
        "Generate a concise",  # Title generation requests
    ]

    for marker in synthetic_markers:
        if marker in content:
            # Tag the request so the proxy knows to skip logging
            body["_beigebox_synthetic"] = True
            body["_beigebox_synthetic_type"] = "openwebui_followup"
            break

    return body
"""
Example hook: system prompt injection.

Prepends a custom system prompt to every request.
Useful for adding personality, constraints, or context
to all conversations regardless of frontend.

To use: copy this file to your hooks/ directory, customize the
SYSTEM_PROMPT below, and enable in config.yaml.
"""

# SYSTEM_PROMPT = "You are a helpful assistant. Be concise and direct."
#
# def pre_request(body: dict, context: dict) -> dict:
#     messages = body.get("messages", [])
#     if messages and messages[0].get("role") != "system":
#         messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
#         body["messages"] = messages
#     return body
