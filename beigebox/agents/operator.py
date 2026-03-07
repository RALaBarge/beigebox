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
import time
from typing import Any

import httpx

from beigebox.config import get_config
from beigebox.agents.skill_loader import load_skills, skills_to_xml, skills_fingerprint

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

WORKSPACE:
- Input files: /workspace/in/ (read-only) — files the user has provided for you to read.
- Output files: /workspace/out/ — write any files you produce here using the shell tool.
  Example: {{"tool": "system_info", "input": "echo 'result' > /workspace/out/report.txt"}}
  Always tell the user the filename when you write to workspace/out/.

AVAILABLE TOOLS:
{tools_block}
{skills_block}"""

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
    Handles markdown fences, leading/trailing prose, and Qwen3 <think> blocks.
    """
    text = text.strip()
    # Strip Qwen3 / deepseek-r1 style thinking blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown fences
    text = re.sub(r"```(?:json)?", "", text).strip()

    # Try the whole thing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the outermost {...} block (handles nested braces)
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = None  # keep scanning for the next candidate

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

    def __init__(self, vector_store=None, model_override: str | None = None):
        from beigebox.tools.registry import ToolRegistry

        self.cfg = get_config()
        self.vector_store = vector_store
        self._registry = ToolRegistry(vector_store=vector_store)
        self._model = (
            model_override
            or self.cfg.get("operator", {}).get("model")
            or self.cfg.get("backend", {}).get("default_model", "")
        )
        self._backend_url = (
            self.cfg.get("embedding", {}).get("backend_url")
            or self.cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")
        self._max_iter = self.cfg.get("operator", {}).get("max_iterations", 8)
        self._timeout = self.cfg.get("operator", {}).get("timeout", 300)

        # Tool sandboxing: restrict which tools the LLM agent can call
        allowed_tools = self.cfg.get("operator", {}).get("allowed_tools", [])
        tools = self._registry.tools
        if allowed_tools:
            tools = {k: v for k, v in tools.items() if k in allowed_tools}
            blocked = set(self._registry.tools.keys()) - set(tools.keys())
            if blocked:
                logger.info("Operator tool sandbox: blocked %s", sorted(blocked))
        self._tools = tools

        # ── Agent Skills ──────────────────────────────────────────────────
        from pathlib import Path as _Path
        skills_path = (
            self.cfg.get("skills", {}).get("path")
            or str(_Path(__file__).parent.parent.parent / "2600" / "skills")
        )
        self._skills_dir = skills_path
        self._skills = load_skills(skills_path)
        self._skills_fp = skills_fingerprint(skills_path)

        if self._skills:
            from beigebox.tools.skill_reader import SkillReaderTool
            self._tools["read_skill"] = SkillReaderTool(self._skills)
            logger.info("Skills available: %s", [s["name"] for s in self._skills])

        tools = self._tools
        skills_block = ""
        if self._skills:
            skills_xml = skills_to_xml(self._skills)
            skills_block = (
                f"\nAGENT SKILLS:\n"
                f"You have access to skills that provide domain expertise and step-by-step\n"
                f"instructions. Call read_skill with the skill name to load full instructions\n"
                f"before following a skill.\n\n"
                f"{skills_xml}\n"
            )

        if tools:
            self._system = _SYSTEM.format(
                tools_block=_build_tools_block(tools),
                skills_block=skills_block,
            )
        else:
            self._system = _NO_TOOLS_SYSTEM

        logger.info(
            "Operator ready (model=%s, tools=%s, skills=%s)",
            self._model,
            list(tools.keys()),
            [s["name"] for s in self._skills],
        )

    # ------------------------------------------------------------------
    # Skills hot-reload
    # ------------------------------------------------------------------

    def _reload_skills_if_changed(self) -> None:
        """Re-scan skills dir and rebuild system prompt if any SKILL.md changed."""
        fp = skills_fingerprint(self._skills_dir)
        if fp == self._skills_fp:
            return
        logger.info("Skills changed — reloading")
        self._skills_fp = fp
        self._skills = load_skills(self._skills_dir)

        # Re-register read_skill with updated list (or remove if no skills)
        self._tools.pop("read_skill", None)
        if self._skills:
            from beigebox.tools.skill_reader import SkillReaderTool
            self._tools["read_skill"] = SkillReaderTool(self._skills)

        # Rebuild system prompt
        skills_block = ""
        if self._skills:
            skills_xml = skills_to_xml(self._skills)
            skills_block = (
                f"\nAGENT SKILLS:\n"
                f"You have access to skills that provide domain expertise and step-by-step\n"
                f"instructions. Call read_skill with the skill name to load full instructions\n"
                f"before following a skill.\n\n"
                f"{skills_xml}\n"
            )
        if self._tools:
            self._system = _SYSTEM.format(
                tools_block=_build_tools_block(self._tools),
                skills_block=skills_block,
            )
        logger.info(
            "Skills reloaded: %s",
            [s["name"] for s in self._skills] if self._skills else [],
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict], _attempt: int = 0) -> str:
        """Send messages to Ollama and return the assistant content string.
        Retries up to 2 times with exponential backoff on transient errors."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                # Disable thinking mode for models that support it (Qwen3, DeepSeek-R1, etc.)
                # Thinking tokens bloat the context and break JSON-only output parsing.
                _is_thinker = any(t in self._model.lower() for t in ("qwen3", "r1", "deepseek-r"))
                opts: dict = {"num_ctx": 8192}
                if _is_thinker:
                    opts["think"] = False
                resp = client.post(
                    f"{self._backend_url}/v1/chat/completions",
                    json={
                        "model": self._model,
                        "messages": messages,
                        "temperature": 0,
                        "stream": False,
                        "options": opts,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if _attempt < 2:
                delay = 1.5 ** (_attempt + 1)
                logger.warning(
                    "Operator _chat attempt %d failed (%s), retrying in %.1fs",
                    _attempt + 1, e, delay,
                )
                time.sleep(delay)
                return self._chat(messages, _attempt + 1)
            raise

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _run_tool(self, name: str, input_str: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self._tools.keys()) or "none"
            return f"Error: unknown tool '{name}'. Available: {available}"
        try:
            return str(tool.run(input_str))
        except Exception as e:
            return f"Error running {name}: {e}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, question: str) -> str:
        self._reload_skills_if_changed()
        if not question.strip():
            return "No question provided."

        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": question},
        ]

        for iteration in range(self._max_iter):
            # Two iterations from the limit — nudge the model to wrap up rather
            # than burning the last step on another tool call with no answer.
            if iteration == self._max_iter - 2:
                messages.append({
                    "role": "user",
                    "content": (
                        "You are approaching the maximum number of steps. "
                        "Please synthesise what you have found so far and provide "
                        'a final {"thought": "...", "answer": "..."} response now.'
                    ),
                })

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
