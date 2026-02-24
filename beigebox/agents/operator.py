"""
Operator agent — the brain behind `beigebox operator` and the web UI Operator tab.

Uses a custom JSON tool loop instead of LangChain ReAct text parsing.
The model emits structured JSON at each step which is far more reliable on
small models (llama3.2:3b, etc.) than free-text Action/Observation parsing.

Loop protocol:
  Each turn the model must respond with ONE of:
    {"thought": "...", "tool": "tool_name", "input": "..."}   ← call a tool
    {"thought": "...", "answer": "..."}                        ← done

No TUI dependency. Works from CLI, HTTP API, or any caller.

    op = Operator(vector_store=vs)
    answer = op.run("How many conversations happened today?")
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from beigebox.config import get_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are BeigeBox Operator, an admin assistant for a local LLM proxy.
You answer questions about conversations, system state, and anything the user needs.

You have access to tools. To use a tool, respond with ONLY this JSON (no markdown, no extra text):
{{"thought": "why I'm calling this tool", "tool": "TOOL_NAME", "input": "what to pass"}}

When you have enough information to answer, respond with ONLY this JSON:
{{"thought": "I have the answer", "answer": "your full answer here"}}

RULES:
- Respond with ONLY the JSON object. No markdown fences. No explanation outside the JSON.
- Use one tool at a time.
- If no tool is needed, go straight to the answer JSON.
- If a tool returns an error, try a different approach or explain the limitation in your answer.
- Never make up tool results.

AVAILABLE TOOLS:
{tools_block}
"""

_NO_TOOLS_SYSTEM = """\
You are BeigeBox Operator, an admin assistant for a local LLM proxy.
Answer the user's question directly and helpfully.
No tools are currently available.
"""


def _build_tools_block(registry_tools: dict) -> str:
    lines = []
    for name, tool_obj in registry_tools.items():
        desc = getattr(tool_obj, "description", f"Run the {name} tool")
        lines.append(f"  {name}: {desc}")
    return "\n".join(lines) if lines else "  (none)"


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """
    Extract the first JSON object from model output.
    Handles markdown fences and leading/trailing prose.
    """
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip()

    # Try the whole thing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first {...} block
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class Operator:
    """
    JSON-based tool loop agent. Does not use LangChain's ReAct parser.

    Each iteration:
      1. Send conversation history to LLM
      2. Parse JSON response
      3. If tool call -> execute tool, append result, loop
      4. If answer -> return
      5. If parse fails -> retry once with a correction prompt, then give up
    """

    def __init__(self, vector_store=None):
        from beigebox.tools.registry import ToolRegistry

        self.cfg = get_config()
        self.vector_store = vector_store
        self._registry = ToolRegistry(vector_store=vector_store)
        self._model = (
            self.cfg.get("operator", {}).get("model")
            or self.cfg.get("backend", {}).get("default_model", "")
        )
        self._backend_url = (
            self.cfg.get("embedding", {}).get("backend_url")
            or self.cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")
        self._max_iter = self.cfg.get("operator", {}).get("max_iterations", 8)
        self._timeout = self.cfg.get("operator", {}).get("timeout", 60)

        tools = self._registry.tools
        if tools:
            self._system = _SYSTEM.format(tools_block=_build_tools_block(tools))
        else:
            self._system = _NO_TOOLS_SYSTEM

        logger.info(
            "Operator ready (model=%s, tools=%s)",
            self._model,
            list(tools.keys()),
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict]) -> str:
        """Send messages to Ollama and return the assistant content string."""
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._backend_url}/v1/chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": 0,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _run_tool(self, name: str, input_str: str) -> str:
        tool = self._registry.tools.get(name)
        if tool is None:
            available = ", ".join(self._registry.tools.keys()) or "none"
            return f"Error: unknown tool '{name}'. Available: {available}"
        try:
            return str(tool.run(input_str))
        except Exception as e:
            return f"Error running {name}: {e}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, question: str) -> str:
        if not question.strip():
            return "No question provided."

        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": question},
        ]

        for iteration in range(self._max_iter):
            try:
                raw = self._chat(messages)
            except Exception as e:
                logger.error("Operator LLM call failed: %s", e)
                return f"Operator unavailable: {e}. Make sure Ollama is running with model '{self._model}'."

            logger.debug("Operator iter %d raw: %s", iteration, raw[:200])

            parsed = _extract_json(raw)

            # Parse failed — nudge the model once then return raw
            if parsed is None:
                if iteration == 0:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your response was not valid JSON. "
                            "You must respond with ONLY a JSON object. "
                            'Either {"thought": "...", "tool": "...", "input": "..."} '
                            'or {"thought": "...", "answer": "..."}. '
                            "No markdown, no extra text."
                        ),
                    })
                    continue
                else:
                    logger.warning("Operator: could not parse JSON after nudge, returning raw")
                    return raw.strip()

            # Final answer
            if "answer" in parsed:
                return str(parsed["answer"])

            # Tool call
            if "tool" in parsed:
                tool_name = parsed.get("tool", "")
                tool_input = str(parsed.get("input", ""))
                thought = parsed.get("thought", "")

                logger.info("Operator tool call: %s(%r) — %s", tool_name, tool_input, thought)

                observation = self._run_tool(tool_name, tool_input)

                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"Tool result for {tool_name}:\n{observation}",
                })
                continue

            # JSON present but neither 'answer' nor 'tool'
            content = parsed.get("thought", "") or str(parsed)
            if content:
                return content

        return "Operator reached max iterations without a final answer. Try rephrasing your question."
