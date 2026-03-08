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

import gzip
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
- Persistent notes: write key facts, preferences, or ongoing context to
  /workspace/out/operator_notes.md and they will be injected into your next session.

BROWSER TOOLS (via browserbox):
Browser actions are NOT direct tool names. Always call them through the `browserbox` tool.
CORRECT:
  {{"thought": "opening a tab", "tool": "browserbox", "input": {{"tool": "tabs.open", "input": "https://example.com"}}}}
  {{"thought": "reading page content", "tool": "browserbox", "input": {{"tool": "dom.snapshot", "input": ""}}}}
  {{"thought": "getting page text", "tool": "browserbox", "input": {{"tool": "dom.get_text", "input": ""}}}}
  {{"thought": "clicking a button", "tool": "browserbox", "input": {{"tool": "dom.click", "input": "#submit"}}}}
  {{"thought": "taking a screenshot", "tool": "browserbox", "input": {{"tool": "tabs.screenshot", "input": ""}}}}
WRONG — these will all fail:
  {{"tool": "tabs.open", ...}}            ← not a tool name
  {{"tool": "dom.snapshot", ...}}         ← not a tool name
  {{"tool": "browserbox.tabs.open", ...}} ← not a tool name, dot-notation doesn't work
  {{"tool": "browserbox.nav.go", ...}}    ← same mistake
  {{"tool": "browserbox", "input": {{"method": "open", "url": "..."}}}} ← wrong inner format

AVAILABLE TOOLS:
{tools_block}
{skills_block}"""

_PRE_HOOK_SYSTEM = """\
You are a query enrichment agent that runs before the main LLM sees a message.
Your job is to use tools to gather context and return an enriched version of the user's message.

You have access to tools. To use a tool, respond with ONLY this JSON:
{{"thought": "why I'm calling this tool", "tool": "TOOL_NAME", "input": "what to pass"}}

When ready, return an enriched version of the message:
{{"thought": "enrichment complete", "answer": "enriched message text here"}}

RULES:
- Do NOT answer the user's question. Enrich the message and return it.
- Use tools to fetch relevant context: memory recall, current info, system state, etc.
- Your answer becomes the message the main LLM will receive, so include the original intent plus any useful context you found.
- If no enrichment is needed, return the original message unchanged.
- Keep the enriched message focused — do not pad or waffle.

AVAILABLE TOOLS:
{tools_block}
{skills_block}"""

_POST_HOOK_SYSTEM = """\
You are a post-response processing agent that runs after the main LLM has answered.
Your job is to review the user's question and the assistant's response, then use tools
to take any useful follow-up actions (store facts, write notes, update workspace files, etc.).

You have access to tools. To use a tool, respond with ONLY this JSON:
{{"thought": "why I'm calling this tool", "tool": "TOOL_NAME", "input": "what to pass"}}

When done, signal completion:
{{"thought": "post-processing complete", "answer": "done"}}

RULES:
- Do NOT re-answer the user's question. Act on the response, don't restate it.
- Use tools to persist useful information, trigger side effects, or enrich workspace output.
- If no action is needed, return done immediately.
- Keep it fast — you have a limited iteration budget.

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
# Output extraction helpers (JSON primary, ReAct fallback)
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


def _extract_react(text: str) -> dict | None:
    """
    Parse ReAct-style (non-JSON) model output as a fallback.

    Looks for:
      Thought: ...
      Action: tool_name
      Action Input: input text

    or a terminal:
      Final Answer: ...
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Final answer patterns
    for pat in (r"Final Answer:\s*(.+)", r"Answer:\s*(.+)"):
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            return {"answer": m.group(1).strip()}

    # Tool call
    action_m = re.search(r"^Action:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    if action_m:
        input_m = re.search(
            r"^Action Input:\s*(.+?)(?=\n(?:Thought:|Observation:|Action:)|\Z)",
            text, re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )
        thought_m = re.search(
            r"^Thought:\s*(.+?)(?=\nAction:|\Z)",
            text, re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )
        return {
            "tool":    action_m.group(1).strip(),
            "input":   input_m.group(1).strip() if input_m else "",
            "thought": thought_m.group(1).strip() if thought_m else "",
        }

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

    def __init__(self, vector_store=None, blob_store=None,
                 model_override: str | None = None,
                 max_iterations_override: int | None = None,
                 pre_hook: bool = False, post_hook: bool = False):
        from beigebox.tools.registry import ToolRegistry

        self.cfg = get_config()
        self.vector_store = vector_store
        self._blob_store = blob_store
        self._session_id: str | None = None
        self._pre_hook = pre_hook
        self._post_hook = post_hook

        # Dump dir for hook tool I/O — pre/post hook calls go to workspace
        # files instead of ChromaDB to keep infrastructure noise out of the
        # main data chain.
        if pre_hook or post_hook:
            _hook_subdir = ".prehook" if pre_hook else ".posthook"
            _ws_path = self.cfg.get("workspace", {}).get("path", "./workspace")
            _app_root = Path(__file__).parent.parent.parent
            self._dump_dir: Path | None = (_app_root / _ws_path / "out" / _hook_subdir).resolve()
        else:
            self._dump_dir = None

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
        self._max_iter = max_iterations_override or self.cfg.get("operator", {}).get("max_iterations", 8)
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

        if pre_hook:
            self._system = _PRE_HOOK_SYSTEM.format(
                tools_block=_build_tools_block(tools) if tools else "(no tools available)",
                skills_block=skills_block,
            )
        elif post_hook:
            self._system = _POST_HOOK_SYSTEM.format(
                tools_block=_build_tools_block(tools) if tools else "(no tools available)",
                skills_block=skills_block,
            )
        elif tools:
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
    # Persistent notes (inject-then-acknowledge)
    # ------------------------------------------------------------------

    def _notes_path(self):
        from pathlib import Path
        ws = self.cfg.get("workspace", {}).get("path", "./workspace")
        p = Path(ws) / "out" / "operator_notes.md"
        if not p.is_absolute():
            p = Path(__file__).parent.parent.parent / p
        return p

    def _load_notes(self) -> str | None:
        """
        Load the operator's persistent cross-session notes.

        The operator can write to /workspace/out/operator_notes.md during a
        session. On the next session those notes are injected before the first
        user message and a synthetic assistant acknowledgment is prepended so
        the model treats the context as already digested.
        """
        try:
            p = self._notes_path()
            if p.exists():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    logger.info("Operator: loaded persistent notes (%d chars)", len(content))
                    return content
        except Exception as e:
            logger.debug("Operator: could not load notes: %s", e)
        return None

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
            raw_result = str(tool.run(input_str))
        except Exception as e:
            return f"Error running {name}: {e}"

        ts = datetime.now(timezone.utc).isoformat()

        if self._dump_dir is not None:
            # Hook mode: dump to workspace file, never touch ChromaDB.
            try:
                self._dump_dir.mkdir(parents=True, exist_ok=True)
                ts_safe = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                dump_path = self._dump_dir / f"{ts_safe}_{name}.json.gz"
                payload = json.dumps({
                    "tool": name, "input": input_str, "result": raw_result, "ts": ts,
                })
                with gzip.open(dump_path, "wb") as f:
                    f.write(payload.encode("utf-8"))
            except Exception as e:
                logger.warning("hook dump failed for %s: %s", name, e)
        elif getattr(tool, "capture_tool_io", False) and self.vector_store and self._blob_store:
            # Normal operator mode: store to blob + ChromaDB.
            if self._session_id is None:
                self._session_id = uuid.uuid4().hex[:12]
            try:
                blob_hash = self._blob_store.write(raw_result)
                self.vector_store.store_tool_result(
                    session_id=self._session_id,
                    tool_name=name,
                    tool_input=input_str,
                    blob_hash=blob_hash,
                    preview=raw_result[:300],
                    timestamp=ts,
                )
            except Exception as e:
                logger.warning("tool capture failed for %s: %s", name, e)
                blob_hash = ""

        # Truncate what the operator sees if the tool requests it.
        max_chars = getattr(tool, "max_context_chars", None)
        if max_chars and len(raw_result) > max_chars:
            hint = f" [{blob_hash[:8]}]" if (
                getattr(tool, "capture_tool_io", False) and self._blob_store
                and self._dump_dir is None
            ) else ""
            return raw_result[:max_chars] + f"\n[...truncated{hint} — full result stored]"
        return raw_result

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, question: str, history: list[dict] | None = None) -> str:
        self._reload_skills_if_changed()
        if not question.strip():
            return "No question provided."

        messages = [{"role": "system", "content": self._system}]

        # Inject-then-acknowledge: prepend persistent notes from previous sessions.
        # A synthetic assistant message acts as the acknowledgment so the model
        # treats the notes as already digested — no extra LLM round-trip needed.
        notes = self._load_notes()
        if notes:
            messages.append({"role": "user", "content": f"[NOTES FROM PREVIOUS SESSIONS]\n{notes}\n[END NOTES]"})
            messages.append({"role": "assistant", "content": '{"thought": "I have reviewed my previous session notes and am ready to assist.", "status": "ready"}'})

        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})

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

            # JSON failed — try ReAct-style fallback before giving up
            if parsed is None:
                parsed = _extract_react(raw)
                if parsed is not None:
                    logger.info("Operator: ReAct fallback parsed successfully (iter %d)", iteration)

            # Still nothing — nudge once then return raw
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
                    logger.warning("Operator: could not parse output after nudge, returning raw")
                    return raw.strip()

            # Final answer
            if "answer" in parsed:
                return str(parsed["answer"])

            # Tool call
            if "tool" in parsed:
                tool_name = parsed.get("tool", "")
                _inp = parsed.get("input", "")
                tool_input = json.dumps(_inp) if isinstance(_inp, (dict, list)) else str(_inp)
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

    async def run_stream(self, question: str, history: list[dict] | None = None):
        """
        Async generator yielding operator progress events.

        Event shapes:
          {"type": "tool_call",   "tool": str, "input": str, "thought": str}
          {"type": "tool_result", "tool": str, "result": str}
          {"type": "answer",      "content": str}
          {"type": "error",       "message": str}
        """
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()

        self._reload_skills_if_changed()
        if not question.strip():
            yield {"type": "error", "message": "No question provided."}
            return

        messages = [{"role": "system", "content": self._system}]

        # Inject-then-acknowledge persistent notes (same as sync run())
        notes = self._load_notes()
        if notes:
            messages.append({"role": "user", "content": f"[NOTES FROM PREVIOUS SESSIONS]\n{notes}\n[END NOTES]"})
            messages.append({"role": "assistant", "content": '{"thought": "I have reviewed my previous session notes and am ready to assist.", "status": "ready"}'})

        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})

        for iteration in range(self._max_iter):
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
                raw = await loop.run_in_executor(None, self._chat, messages)
            except Exception as e:
                logger.error("Operator LLM call failed: %s", e)
                yield {"type": "error", "message": f"LLM unavailable: {e}"}
                return

            logger.debug("Operator stream iter %d raw: %s", iteration, raw[:200])

            parsed = _extract_json(raw)

            # JSON failed — try ReAct-style fallback
            if parsed is None:
                parsed = _extract_react(raw)
                if parsed is not None:
                    logger.info("Operator stream: ReAct fallback parsed successfully (iter %d)", iteration)

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
                    logger.warning("Operator stream: could not parse output after nudge, returning raw")
                    yield {"type": "answer", "content": raw.strip()}
                    return

            if "answer" in parsed:
                yield {"type": "answer", "content": str(parsed["answer"])}
                return

            if "tool" in parsed:
                tool_name = parsed.get("tool", "")
                _inp = parsed.get("input", "")
                tool_input = json.dumps(_inp) if isinstance(_inp, (dict, list)) else str(_inp)
                thought = parsed.get("thought", "")

                logger.info("Operator stream tool call: %s(%r) — %s", tool_name, tool_input, thought)

                yield {"type": "tool_call", "tool": tool_name, "input": tool_input, "thought": thought}

                observation = await loop.run_in_executor(None, self._run_tool, tool_name, tool_input)

                yield {"type": "tool_result", "tool": tool_name, "result": observation}

                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"Tool result for {tool_name}:\n{observation}",
                })
                continue

            # JSON present but neither 'answer' nor 'tool'
            content = parsed.get("thought", "") or str(parsed)
            if content:
                yield {"type": "answer", "content": content}
                return

        yield {"type": "error", "message": "Operator reached max iterations without a final answer. Try rephrasing your question."}
