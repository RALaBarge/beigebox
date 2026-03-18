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

import asyncio
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

from beigebox.config import get_config, get_runtime_config
from beigebox.agents.skill_loader import load_skills, skills_to_xml, skills_fingerprint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are BeigeBox Operator, an intelligent assistant with access to tools.
You can answer any question — coding, research, system tasks, general knowledge.

RESPONSE FORMAT — you must respond with EXACTLY one of these two JSON shapes, nothing else:

To call a tool:
{{"thought": "why I need this tool", "tool": "TOOL_NAME", "input": "the exact string to pass"}}

To give a final answer (use this when you have all the information needed):
{{"thought": "I have the answer", "answer": "your complete response here"}}

STRICT RULES:
- Output ONLY the JSON object. No markdown fences, no prose before or after it.
- The only valid top-level keys are: thought, tool, input, answer.
- Do NOT output {{"plan": ...}}, {{"steps": ...}}, or any other custom structure — it will be rejected.
- If the user asks you to plan or outline, put the entire plan text inside the "answer" field.
- Use one tool at a time. Check the result before deciding to call another.
- For web_search: "input" must be a specific search query string (e.g. "ALSA PulseAudio Linux audio stack comparison"), never empty, never {{}}.
- If a tool returns an error, try a different approach or explain the limitation in your answer.

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

_SYSTEM_AUTONOMOUS = """\
You are BeigeBox Operator running in autonomous multi-turn mode.
Your job is to make CONCRETE PROGRESS on the task each turn — not plan, not summarise, not repeat.

RESPONSE FORMAT — EXACTLY one of these two JSON shapes, nothing else:

To call a tool (preferred — do real work this turn):
{{"thought": "why I need this tool", "tool": "TOOL_NAME", "input": "the exact string to pass"}}

To report progress and hand off to the next turn:
{{"thought": "what I did this turn", "answer": "description of work done this turn"}}

AUTONOMOUS RULES:
- Output ONLY the JSON object. No markdown fences, no prose outside the JSON.
- The only valid top-level keys are: thought, tool, input, answer.
- Use as many tool calls as needed this turn before giving an answer — exhaust the iteration budget.
- Every answer should represent REAL WORK DONE (code written, file saved, data retrieved).
- Do NOT give an answer that is just a plan or intention — only answer after doing work.
- Do NOT repeat work from previous turns — read plan.md first to see what's been done.
- Use workspace_file to read and update plan.md so your progress persists across turns.
- For web_search: "input" must be a specific search query string, never empty, never {{}}.
- If a tool returns an error, try a different approach — never give up after one failure.

CODE WRITING RULES (when the task involves writing a program or script):
- ALWAYS save code to /workspace/out/ using workspace_file — do NOT put code in your "answer" field.
- Each source file gets its own workspace_file write call: {{"action":"write","path":"main.py","content":"<full file content>"}}.
- Write complete, runnable code — not snippets, not pseudocode, not placeholders.
- Your "answer" field should only say what files you wrote and where, not contain the code itself.

WORKSPACE:
- Input files: /workspace/in/ (read-only) — files the user has provided.
- Output files: /workspace/out/ — write all code and output here using workspace_file.
- Progress tracking: keep /workspace/out/plan.md updated with what's done and what's next.

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
    # Rendered verbatim into the system prompt so the LLM has a live inventory
    # of available tools. This is why the system prompt is rebuilt whenever tools
    # or skills change — there's no separate tools/list API call at runtime.
    lines = []
    for name, tool_obj in registry_tools.items():
        desc = getattr(tool_obj, "description", None) or f'Call the {name} tool. input = string argument.'
        lines.append(f"  {name}: {desc}")
    return "\n".join(lines) if lines else "  (none)"


def _build_tool_rubric(tool_names: list[str]) -> str:
    """Generate a tool selection rubric based on which tools are available.

    Injected into the system prompt so the model knows when to use each tool
    rather than guessing from the description alone.
    """
    _RUBRIC: dict[str, str] = {
        "web_search":      "current events, docs, facts — use a specific query string",
        "web_scraper":     "read a specific URL's full content after web_search finds it",
        "browser":         "interact with pages: click, fill forms, navigate (call discover first)",
        "browserbox":      "interact with pages: click, fill forms, navigate",
        "calculator":      "arithmetic and math expressions",
        "datetime":        "current date/time or date arithmetic",
        "memory":          "recall facts stored from previous sessions",
        "workspace_file":  "read/write local files in /workspace/",
        "shell":           "run shell commands — use sparingly",
        "system_info":     "system stats: CPU, memory, disk",
        "read_skill":      "read a skill document for detailed guidance on a task",
    }
    lines = []
    for name in tool_names:
        if name in _RUBRIC:
            lines.append(f"  {name}: {_RUBRIC[name]}")
    return "\n".join(lines) if lines else ""


_SMALL_MODEL_ADDENDUM = """\

SMALL MODEL MODE — STRICT RULES:
- Use ONLY ONE tool per turn. Never plan multi-step sequences.
- Prefer the simplest tool that answers the question.
- If unsure which tool to use, call web_search first.
- Give your final answer as soon as you have enough information.
"""


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

    # Find the outermost {...} block (handles nested braces).
    # Depth-tracking cursor: when depth returns to 0 we have a complete {…} span.
    # If that span isn't valid JSON (e.g. prose mid-object from a chatty model),
    # reset start=None and keep scanning for the next candidate block.
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

    Fires only after _extract_json fails — small models sometimes ignore the
    JSON-only rule and emit classic Thought/Action/Observation text instead.
    The two parsers together make the loop resilient to format drift without
    adding latency for well-behaved models.

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
# Tool profile helpers
# ---------------------------------------------------------------------------

import fnmatch as _fnmatch


def _resolve_tool_profile(model: str, cfg: dict) -> str | None:
    """Return the tool profile name for a given model, or None for 'use all tools'.

    Checks operator.model_tool_profiles in config.yaml using fnmatch patterns.
    Example config::

        operator:
          model_tool_profiles:
            "*:1b": minimal
            "*:3b": minimal
            "*:7b": standard
            "*:8b": standard
            default: full
    """
    op_cfg = cfg.get("operator", {})
    profile_map: dict = op_cfg.get("model_tool_profiles", {})
    if not profile_map:
        return None
    model_lower = model.lower()
    for pattern, profile in profile_map.items():
        if pattern == "default":
            continue
        if _fnmatch.fnmatch(model_lower, pattern.lower()):
            return profile if profile != "full" else None
    default = profile_map.get("default")
    if not default or default == "full":
        return None
    return default


def _is_small_model(model: str, cfg: dict) -> bool:
    """Return True when model is classified as small-tier.

    Uses the same model_tool_profiles map — if the model resolves to "minimal"
    it is considered small. Falls back to heuristic parameter-count suffixes.
    """
    profile = _resolve_tool_profile(model, cfg)
    if profile is not None:
        return profile == "minimal"
    # Heuristic: param-count suffixes typical of small models
    model_lower = model.lower()
    return any(model_lower.endswith(s) for s in (":1b", ":3b", "-1b", "-3b", "1b", "3b"))


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
                 max_tool_calls: int | None = None,
                 pre_hook: bool = False, post_hook: bool = False,
                 autonomous: bool = False,
                 tool_registry=None,
                 sqlite_store=None,
                 wire_log=None):
        from beigebox.tools.registry import ToolRegistry

        self.cfg = get_config()
        self.rt = get_runtime_config()
        self.vector_store = vector_store
        self._blob_store = blob_store
        self._wire_db = sqlite_store  # optional SQLiteStore for structured tap events
        self._wire_log = wire_log      # optional WireLog for JSONL dual-write
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

        self._registry = tool_registry or ToolRegistry(vector_store=vector_store)
        self._model = (
            model_override
            or (self.rt and self.rt.get("operator_model"))
            or self.cfg.get("operator", {}).get("model")
            or self.cfg.get("backend", {}).get("default_model", "")
        )
        self._backend_url = (
            self.cfg.get("embedding", {}).get("backend_url")
            or self.cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")
        self._max_iter = max_tool_calls or max_iterations_override or self.cfg.get("operator", {}).get("max_iterations", 8)
        self._timeout = self.cfg.get("operator", {}).get("timeout", 300)
        # Total wall-clock cap across all iterations. Capped at 3 digits (999s max via config).
        _wall = (
            (self.rt and self.rt.get("operator_run_timeout"))
            or self.cfg.get("operator", {}).get("run_timeout", 600)
        )
        self._run_timeout = max(1, min(int(_wall), 999))

        # Tool sandboxing: restrict which tools the LLM agent can call.
        # When allowed_tools is set, silently drop every other entry from the
        # dict the LLM sees — the model cannot call a tool it cannot name in
        # the system prompt, so exclusion is enforced at the prompt level.
        allowed_tools = self.cfg.get("operator", {}).get("allowed_tools", [])
        tools = self._registry.tools
        if allowed_tools:
            tools = {k: v for k, v in tools.items() if k in allowed_tools}
            blocked = set(self._registry.tools.keys()) - set(tools.keys())
            if blocked:
                logger.info("Operator tool sandbox: blocked %s", sorted(blocked))

        # Tool profile: further filter tools based on model tier.
        # Profiles are defined in config.yaml under operator.tool_profiles.
        # model_tool_profiles maps fnmatch patterns to profile names.
        _profile_name = _resolve_tool_profile(self._model, self.cfg)
        if _profile_name:
            _profile_tools = self.cfg.get("operator", {}).get(
                "tool_profiles", {}
            ).get(_profile_name, [])
            if _profile_tools:
                tools = {k: v for k, v in tools.items() if k in _profile_tools}
                logger.info("Operator tool profile '%s': tools=%s", _profile_name, list(tools.keys()))

        # BrowserMetaTool: swap in the lazy-loading wrapper when config requests it.
        # The meta-tool shows a stub description and serves discover on demand
        # instead of inlining the full namespace list in the system prompt.
        if self.cfg.get("operator", {}).get("browser_meta_tool", False):
            if "browserbox" in tools:
                from beigebox.tools.browser_meta import BrowserMetaTool
                tools = dict(tools)  # don't mutate registry dict
                tools["browser"] = BrowserMetaTool(tools.pop("browserbox"))
                logger.info("Operator: browserbox replaced with BrowserMetaTool (lazy loading)")

        self._tools = tools
        self._is_small_model = _is_small_model(self._model, self.cfg)

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
            skills_block = f"\n{skills_to_xml(self._skills)}"

        _tools_block = _build_tools_block(tools) if tools else "(no tools available)"

        # Build rubric and small-model addendum dynamically.
        _rubric = _build_tool_rubric(list(tools.keys()))
        _rubric_block = f"\nTOOL SELECTION GUIDE:\n{_rubric}" if _rubric else ""
        _small_addendum = _SMALL_MODEL_ADDENDUM if self._is_small_model else ""

        if pre_hook:
            self._system = _PRE_HOOK_SYSTEM.format(
                tools_block=_tools_block, skills_block=skills_block,
            )
        elif post_hook:
            self._system = _POST_HOOK_SYSTEM.format(
                tools_block=_tools_block, skills_block=skills_block,
            )
        elif autonomous and tools:
            self._system = _SYSTEM_AUTONOMOUS.format(
                tools_block=_tools_block, skills_block=skills_block,
            ) + _rubric_block + _small_addendum
        elif tools:
            self._system = _SYSTEM.format(
                tools_block=_tools_block, skills_block=skills_block,
            ) + _rubric_block + _small_addendum
        else:
            self._system = _NO_TOOLS_SYSTEM

        logger.info(
            "Operator ready (model=%s, tools=%s, skills=%s)",
            self._model,
            list(tools.keys()),
            [s["name"] for s in self._skills],
        )

    def _resolve_backend_url(self, model: str) -> str:
        """Return the backend URL that should serve this model.

        Mirrors the multi-backend router's allowed_models whitelist logic so the
        operator routes to the correct backend rather than always hitting the
        default Ollama URL.
        """
        import fnmatch as _fnmatch
        rt = get_runtime_config() or {}
        # Static backends live under cfg["backends"] (top-level of config.yaml).
        # get_config() doesn't include this key in the returned dict — load it
        # directly from the router or fall back to rt backends only.
        rt_backends = rt.get("backends", [])
        all_backends = sorted(
            rt_backends,
            key=lambda b: b.get("priority", 99),
        )
        for backend in all_backends:
            allowed = backend.get("allowed_models", [])
            if allowed and any(_fnmatch.fnmatch(model, pat) for pat in allowed):
                return backend.get("url", self._backend_url).rstrip("/")
        return self._backend_url

    # ------------------------------------------------------------------
    # Skills hot-reload
    # ------------------------------------------------------------------

    def _reload_skills_if_changed(self) -> None:
        """Re-scan skills dir and rebuild system prompt if any SKILL.md changed.

        skills_fingerprint() returns a hash/mtime digest of the skills directory.
        On the hot path (nothing changed) this is a fast dict comparison with no
        extra filesystem I/O beyond what fingerprint itself does.
        """
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
            skills_block = f"\n{skills_to_xml(self._skills)}"
        if self._tools:
            _rubric = _build_tool_rubric(list(self._tools.keys()))
            _rubric_block = f"\nTOOL SELECTION GUIDE:\n{_rubric}" if _rubric else ""
            _small_addendum = _SMALL_MODEL_ADDENDUM if self._is_small_model else ""
            self._system = _SYSTEM.format(
                tools_block=_build_tools_block(self._tools),
                skills_block=skills_block,
            ) + _rubric_block + _small_addendum
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

    def _chat(self, messages: list[dict]) -> str:
        """Send messages to Ollama and return the assistant content string.
        Retries up to 2 times with exponential backoff on transient errors."""
        _is_thinker = any(t in self._model.lower() for t in ("qwen3", "r1", "deepseek-r"))
        opts: dict = {"num_ctx": 8192}
        if _is_thinker:
            opts["think"] = False
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "options": opts,
        }
        _backend_url = self._resolve_backend_url(self._model)

        last_exc: Exception | None = None
        for _attempt in range(3):
            if _attempt > 0:
                delay = 1.5 ** _attempt
                logger.warning(
                    "Operator _chat attempt %d failed (%s), retrying in %.1fs",
                    _attempt, last_exc, delay,
                )
                time.sleep(delay)
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    # Payload log — full operator context dump (hot-toggled)
                    try:
                        from beigebox.config import get_runtime_config as _grc
                        from beigebox.payload_log import get_payload_log as _gpl
                        if _grc().get("payload_log_enabled", False):
                            _gpl(self.cfg).log(
                                source="operator",
                                payload=payload,
                                backend=_backend_url,
                                model=self._model,
                            )
                    except Exception:
                        pass  # never block on logging

                    resp = client.post(
                        f"{_backend_url}/v1/chat/completions",
                        json=payload,
                    )
                    resp.raise_for_status()
                    result = resp.json()["choices"][0]["message"]["content"]

                    # Payload log — capture operator response
                    try:
                        from beigebox.config import get_runtime_config as _grc2
                        from beigebox.payload_log import get_payload_log as _gpl2
                        if _grc2().get("payload_log_enabled", False):
                            _gpl2(self.cfg).log(
                                source="operator_response",
                                payload={},
                                response=result,
                                backend=_backend_url,
                                model=self._model,
                            )
                    except Exception:
                        pass

                    return result
            except Exception as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]

    async def _chat_async(self, messages: list[dict]) -> str:
        """Async version of _chat using httpx.AsyncClient — used by run_stream()
        so the event loop is never blocked waiting for Ollama."""
        _is_thinker = any(t in self._model.lower() for t in ("qwen3", "r1", "deepseek-r"))
        opts: dict = {"num_ctx": 8192}
        if _is_thinker:
            opts["think"] = False
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "options": opts,
        }
        _backend_url = self._resolve_backend_url(self._model)

        last_exc: Exception | None = None
        for _attempt in range(3):
            if _attempt > 0:
                delay = 1.5 ** _attempt
                logger.warning(
                    "Operator _chat_async attempt %d failed (%s), retrying in %.1fs",
                    _attempt, last_exc, delay,
                )
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    try:
                        from beigebox.config import get_runtime_config as _grc
                        from beigebox.payload_log import get_payload_log as _gpl
                        if _grc().get("payload_log_enabled", False):
                            _gpl(self.cfg).log(source="operator", payload=payload,
                                               backend=_backend_url, model=self._model)
                    except Exception:
                        pass

                    resp = await client.post(
                        f"{_backend_url}/v1/chat/completions",
                        json=payload,
                    )
                    resp.raise_for_status()
                    result = resp.json()["choices"][0]["message"]["content"]

                    try:
                        from beigebox.config import get_runtime_config as _grc2
                        from beigebox.payload_log import get_payload_log as _gpl2
                        if _grc2().get("payload_log_enabled", False):
                            _gpl2(self.cfg).log(source="operator_response", payload={},
                                                response=result, backend=_backend_url,
                                                model=self._model)
                    except Exception:
                        pass

                    return result
            except Exception as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Tap event helper
    # ------------------------------------------------------------------

    def _wire(self, event_type: str, run_id: str, content: str = "",
              turn_id: str | None = None, tool_id: str | None = None,
              meta: dict | None = None) -> None:
        """Write a structured wire event to SQLite tap and/or WireLog JSONL. Never raises."""
        token_estimate = max(1, len(content) // 4) if content else 0
        elapsed_ms = (meta or {}).get("elapsed_ms")

        if self._wire_db is not None:
            try:
                self._wire_db.log_wire_event(
                    event_type=event_type,
                    source="operator",
                    content=content,
                    model=self._model,
                    run_id=run_id,
                    turn_id=turn_id,
                    tool_id=tool_id,
                    meta=meta,
                )
            except Exception as e:
                logger.debug("_wire SQLite failed (%s): %s", event_type, e)

        if self._wire_log is not None:
            try:
                self._wire_log.log(
                    direction="internal",
                    role="operator",
                    content=content,
                    model=self._model,
                    token_count=token_estimate,
                    latency_ms=elapsed_ms,
                    event_type=event_type,
                    source="operator",
                    run_id=run_id,
                    turn_id=turn_id,
                    tool_id=tool_id,
                    meta=meta,
                )
            except Exception as e:
                logger.debug("_wire JSONL failed (%s): %s", event_type, e)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _run_tool(self, name: str, input_str: str) -> str:
        from beigebox.tools.result import ToolResult

        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self._tools.keys()) or "none"
            return f"Error: unknown tool '{name}'. Available: {available}"
        try:
            result = tool.run(input_str)
        except Exception as e:
            return f"Error running {name}: {e}"

        # ToolResult: use structured observation; plain strings pass through as-is.
        if isinstance(result, ToolResult):
            raw_result = result.to_observation()
        else:
            raw_result = str(result)

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

        _run_id = uuid.uuid4().hex[:12]
        _t_run_start = time.monotonic()
        _cumulative_tokens = 0
        self._wire("operator_start", _run_id, content=question[:500],
                   meta={"model": self._model, "max_iter": self._max_iter,
                         "question_len": len(question)})

        _run_deadline = time.monotonic() + self._run_timeout
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

        # Loop / failure guards
        _recent_calls: list[tuple[str, str]] = []   # (tool_name, tool_input) ring buffer
        _consec_fail: dict[str, int] = {}            # consecutive failure count per tool

        for iteration in range(self._max_iter):
            if time.monotonic() > _run_deadline:
                logger.warning("Operator run_timeout (%ds) exceeded after %d iterations", self._run_timeout, iteration)
                return f"Operator stopped: run_timeout of {self._run_timeout}s exceeded."

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

            # Still nothing — nudge once then return raw.
            # The correction prompt is sent only on iteration 0. If the model
            # still fails to produce JSON after one nudge we return the raw text
            # rather than looping indefinitely on correction messages.
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

            # Tool call (checked before answer — model may emit both; tool takes priority)
            if "tool" in parsed:
                tool_name = parsed.get("tool", "")
                if not tool_name:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            'The "tool" field was empty. Specify a tool name from the list. '
                            "Example: {\"thought\": \"I need to search\", \"tool\": \"web_search\", \"input\": \"your query\"}\n"
                            f"Available tools: {', '.join(self._tools.keys()) if self._tools else 'none'}"
                        ),
                    })
                    continue
                _inp = parsed.get("input", "")
                # Re-serialise dict/list inputs to a JSON string so tools that
                # call json.loads() on their input (e.g. browserbox) receive a
                # valid JSON string instead of Python's str(dict) representation.
                tool_input = json.dumps(_inp) if isinstance(_inp, (dict, list)) else str(_inp)
                thought = parsed.get("thought", "")
                _turn_id = f"{_run_id}:{iteration}"
                _tool_id = f"{_turn_id}:{tool_name}"

                logger.info("Operator tool call: %s(%r) — %s", tool_name, tool_input, thought)

                if thought:
                    _cumulative_tokens += len(thought) // 4
                    self._wire("operator_thought", _run_id, content=thought,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_next": tool_name})

                self._wire("operator_tool_call", _run_id,
                           content=tool_input[:100],
                           turn_id=_turn_id, tool_id=_tool_id,
                           meta={"tool": tool_name, "iteration": iteration,
                                 "input_preview": tool_input[:100]})

                _t_tool = time.monotonic()
                observation = self._run_tool(tool_name, tool_input)
                _tool_elapsed = round((time.monotonic() - _t_tool) * 1000, 1)
                _cumulative_tokens += len(observation) // 4

                self._wire("operator_tool_result", _run_id,
                           content=observation[:500],
                           turn_id=_turn_id, tool_id=_tool_id,
                           meta={"tool": tool_name, "iteration": iteration,
                                 "elapsed_ms": _tool_elapsed,
                                 "result_len": len(observation)})

                # --- Loop detection ---
                _recent_calls.append((tool_name, tool_input))
                if len(_recent_calls) > 6:
                    _recent_calls.pop(0)
                _is_failure = observation.lower().startswith(
                    ("no results", "error", "not found", "unknown tool", "file not found",
                     "[status: error]")
                )
                _consec_fail[tool_name] = (_consec_fail.get(tool_name, 0) + 1) if _is_failure else 0

                _loop_nudge: str | None = None
                if len(_recent_calls) >= 3 and _recent_calls[-3:].count(_recent_calls[-1]) >= 3:
                    _loop_nudge = (
                        f"You have called {tool_name!r} with the same input {3} times in a row "
                        f"and are not making progress. Stop repeating this call. "
                        f"Either try a different tool, a different input, or give your final answer now."
                    )
                elif _consec_fail.get(tool_name, 0) >= 3:
                    _loop_nudge = (
                        f"The {tool_name!r} tool has failed {_consec_fail[tool_name]} consecutive times. "
                        f"Stop using it. Try a completely different approach or give your best answer based on what you already know."
                    )

                self._wire("operator_iteration_end", _run_id,
                           turn_id=_turn_id,
                           meta={"iteration": iteration,
                                 "cumulative_tokens": _cumulative_tokens,
                                 "tool": tool_name,
                                 "elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1)})

                messages.append({"role": "assistant", "content": raw})
                if _loop_nudge:
                    self._wire("operator_nudge", _run_id, content=_loop_nudge,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_name": tool_name,
                                     "consec_fail": _consec_fail.get(tool_name, 0)})
                    messages.append({"role": "user", "content": _loop_nudge})
                else:
                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_name}:\n{observation}",
                    })
                continue

            # Final answer
            if "answer" in parsed:
                _answer = str(parsed["answer"])
                _cumulative_tokens += len(_answer) // 4
                self._wire("operator_finish", _run_id, content=_answer[:500],
                           meta={"total_iterations": iteration + 1,
                                 "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                                 "cumulative_tokens": _cumulative_tokens,
                                 "status": "answer"})
                return _answer

            # JSON parsed but has no usable keys — covers {}, {"thought": "..."}-only, or wrong keys
            bad_keys = [k for k in parsed if k not in ("thought", "tool", "input", "answer")]
            if not parsed or (not bad_keys and "tool" not in parsed and "answer" not in parsed):
                nudge = (
                    "Your response was an empty or incomplete JSON object. "
                    "You MUST include either \"tool\" (to call a tool) or \"answer\" (to give a final answer).\n"
                    "Use one of these exact shapes:\n"
                    '{"thought": "why I need this", "tool": "TOOL_NAME", "input": "..."}\n'
                    'or {"thought": "I have the answer", "answer": "your full response here"}'
                )
            else:
                nudge = (
                    f"Invalid response — unexpected keys: {bad_keys}. "
                    "You MUST respond with ONLY one of:\n"
                    '{"thought": "...", "tool": "TOOL_NAME", "input": "..."}\n'
                    'or {"thought": "...", "answer": "your full answer here"}\n'
                    "Put any planning or explanation inside the \"answer\" field."
                )
            if iteration < self._max_iter - 1:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": nudge})
                continue
            content = parsed.get("thought", "") or str(parsed)
            self._wire("operator_finish", _run_id, content=content[:500],
                       meta={"total_iterations": iteration + 1,
                             "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                             "cumulative_tokens": _cumulative_tokens,
                             "status": "thought_fallback"})
            return content

        self._wire("operator_finish", _run_id,
                   meta={"total_iterations": self._max_iter,
                         "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                         "cumulative_tokens": _cumulative_tokens,
                         "status": "max_iterations"})
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
        self._reload_skills_if_changed()
        if not question.strip():
            yield {"type": "error", "message": "No question provided."}
            return

        _run_id = uuid.uuid4().hex[:12]
        _t_run_start = time.monotonic()
        self._wire("op_start", _run_id, content=question[:500],
                   meta={"model": self._model, "max_iter": self._max_iter,
                         "question_len": len(question)})

        messages = [{"role": "system", "content": self._system}]

        # Inject-then-acknowledge persistent notes (same as sync run())
        notes = self._load_notes()
        if notes:
            messages.append({"role": "user", "content": f"[NOTES FROM PREVIOUS SESSIONS]\n{notes}\n[END NOTES]"})
            messages.append({"role": "assistant", "content": '{"thought": "I have reviewed my previous session notes and am ready to assist.", "status": "ready"}'})

        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})

        # Loop / failure guards (mirrors sync run())
        _recent_calls: list[tuple[str, str]] = []
        _consec_fail: dict[str, int] = {}
        _run_deadline = time.monotonic() + self._run_timeout
        _cumulative_tokens = 0

        for iteration in range(self._max_iter):
            if time.monotonic() > _run_deadline:
                logger.warning("Operator run_stream timeout (%ds) exceeded after %d iterations", self._run_timeout, iteration)
                self._wire("op_error", _run_id,
                           content=f"run_timeout of {self._run_timeout}s exceeded",
                           meta={"iteration": iteration, "elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1)})
                yield {"type": "error", "message": f"Operator stopped: run_timeout of {self._run_timeout}s exceeded."}
                return

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
                raw = await self._chat_async(messages)
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

            if "tool" in parsed:
                tool_name = parsed.get("tool", "")
                if not tool_name:
                    # Empty tool name — nudge the model to provide a real tool name
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            'The "tool" field was empty. You must specify a tool name from the list. '
                            "Example: {\"thought\": \"I need to search\", \"tool\": \"web_search\", \"input\": \"your query\"}\n"
                            f"Available tools: {', '.join(self._tools.keys()) if self._tools else 'none'}"
                        ),
                    })
                    continue
                _inp = parsed.get("input", "")
                tool_input = json.dumps(_inp) if isinstance(_inp, (dict, list)) else str(_inp)
                thought = parsed.get("thought", "")
                _turn_id = f"{_run_id}:{iteration}"
                _tool_id = f"{_turn_id}:{tool_name}"

                logger.info("Operator stream tool call: %s(%r) — %s", tool_name, tool_input, thought)

                if thought:
                    _cumulative_tokens += len(thought) // 4
                    self._wire("op_thought", _run_id, content=thought,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_next": tool_name})
                    self._wire("operator_thought", _run_id, content=thought,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_next": tool_name})

                self._wire("op_tool_call", _run_id, content=tool_input[:100],
                           turn_id=_turn_id, tool_id=_tool_id,
                           meta={"tool_name": tool_name, "iteration": iteration,
                                 "input_preview": tool_input[:100],
                                 "input_len": len(tool_input)})
                self._wire("operator_tool_call", _run_id, content=tool_input[:100],
                           turn_id=_turn_id, tool_id=_tool_id,
                           meta={"tool": tool_name, "iteration": iteration,
                                 "input_preview": tool_input[:100]})

                yield {"type": "tool_call", "tool": tool_name, "input": tool_input, "thought": thought}

                _t_tool = time.monotonic()
                observation = await asyncio.get_running_loop().run_in_executor(None, self._run_tool, tool_name, tool_input)
                _tool_elapsed = round((time.monotonic() - _t_tool) * 1000, 1)
                _cumulative_tokens += len(observation) // 4

                self._wire("op_tool_result", _run_id, content=observation[:500],
                           turn_id=_turn_id, tool_id=_tool_id,
                           meta={"tool_name": tool_name, "iteration": iteration,
                                 "elapsed_ms": _tool_elapsed,
                                 "result_len": len(observation),
                                 "is_failure": observation.lower().startswith(
                                     ("no results", "error", "not found", "unknown tool", "file not found"))})

                yield {"type": "tool_result", "tool": tool_name, "result": observation}

                # --- Loop detection ---
                _recent_calls.append((tool_name, tool_input))
                if len(_recent_calls) > 6:
                    _recent_calls.pop(0)
                _is_failure = observation.lower().startswith(
                    ("no results", "error", "not found", "unknown tool", "file not found",
                     "[status: error]")
                )
                _consec_fail[tool_name] = (_consec_fail.get(tool_name, 0) + 1) if _is_failure else 0

                _loop_nudge: str | None = None
                if len(_recent_calls) >= 3 and _recent_calls[-3:].count(_recent_calls[-1]) >= 3:
                    _loop_nudge = (
                        f"You have called {tool_name!r} with the same input {3} times in a row "
                        f"and are not making progress. Stop repeating this call. "
                        f"Either try a different tool, a different input, or give your final answer now."
                    )
                elif _consec_fail.get(tool_name, 0) >= 3:
                    _loop_nudge = (
                        f"The {tool_name!r} tool has failed {_consec_fail[tool_name]} consecutive times. "
                        f"Stop using it. Try a completely different approach or give your best answer based on what you already know."
                    )

                self._wire("operator_iteration_end", _run_id,
                           turn_id=_turn_id,
                           meta={"iteration": iteration,
                                 "cumulative_tokens": _cumulative_tokens,
                                 "tool": tool_name,
                                 "elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1)})

                messages.append({"role": "assistant", "content": raw})
                if _loop_nudge:
                    self._wire("op_loop_nudge", _run_id, content=_loop_nudge,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_name": tool_name,
                                     "consec_fail": _consec_fail.get(tool_name, 0)})
                    self._wire("operator_nudge", _run_id, content=_loop_nudge,
                               turn_id=_turn_id,
                               meta={"iteration": iteration, "tool_name": tool_name,
                                     "consec_fail": _consec_fail.get(tool_name, 0)})
                    messages.append({"role": "user", "content": _loop_nudge})
                else:
                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_name}:\n{observation}",
                    })
                continue

            if "answer" in parsed:
                _answer = str(parsed["answer"])
                _cumulative_tokens += len(_answer) // 4
                self._wire("op_answer", _run_id, content=_answer[:500],
                           meta={"iteration": iteration,
                                 "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                                 "answer_len": len(_answer)})
                self._wire("operator_finish", _run_id, content=_answer[:500],
                           meta={"total_iterations": iteration + 1,
                                 "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                                 "cumulative_tokens": _cumulative_tokens,
                                 "status": "answer"})
                yield {"type": "answer", "content": _answer}
                return

            # JSON parsed but has no usable keys — covers {}, {"thought": "..."}-only, or wrong keys
            bad_keys = [k for k in parsed if k not in ("thought", "tool", "input", "answer")]
            if not parsed or (not bad_keys and "tool" not in parsed and "answer" not in parsed):
                nudge = (
                    "Your response was an empty or incomplete JSON object. "
                    "You MUST include either \"tool\" (to call a tool) or \"answer\" (to give a final answer).\n"
                    "Use one of these exact shapes:\n"
                    '{"thought": "why I need this", "tool": "TOOL_NAME", "input": "..."}\n'
                    'or {"thought": "I have the answer", "answer": "your full response here"}'
                )
            else:
                nudge = (
                    f"Invalid response — unexpected keys: {bad_keys}. "
                    "You MUST respond with ONLY one of:\n"
                    '{"thought": "...", "tool": "TOOL_NAME", "input": "..."}\n'
                    'or {"thought": "...", "answer": "your full answer here"}\n'
                    "Put any planning or explanation inside the \"answer\" field."
                )
            if iteration < self._max_iter - 1:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": nudge})
                continue
            # Last resort — surface the thought as the answer
            content = parsed.get("thought", "") or str(parsed)
            self._wire("operator_finish", _run_id, content=content[:500],
                       meta={"total_iterations": iteration + 1,
                             "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                             "cumulative_tokens": _cumulative_tokens,
                             "status": "thought_fallback"})
            yield {"type": "answer", "content": content}
            return

        self._wire("operator_finish", _run_id,
                   meta={"total_iterations": self._max_iter,
                         "total_elapsed_ms": round((time.monotonic() - _t_run_start) * 1000, 1),
                         "cumulative_tokens": _cumulative_tokens,
                         "status": "max_iterations"})
        yield {"type": "error", "message": "Operator reached max iterations without a final answer. Try rephrasing your question."}
