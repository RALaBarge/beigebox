"""
BeigeBox Operator — LangChain agent with access to web, data stores, and terminal.

The agent runs against your local Ollama model and has three tool domains:
  - Web: DuckDuckGo search + page scraping
  - Data: ChromaDB semantic search + named SQLite queries (config-driven)
  - Shell: allowlisted terminal commands (you control the list in config.yaml)

Invoke from CLI:  beigebox operator "your question"
Invoke as REPL:   beigebox operator  (no args)
Import directly:  from beigebox.agents.operator import Operator
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.prebuilt import create_react_agent
from langchain_core.tools import Tool
from langchain_ollama import ChatOllama
from langchain_community.tools import DuckDuckGoSearchResults

from beigebox.config import get_config

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are the BeigeBox Operator — a local AI assistant with access to the \
BeigeBox proxy's data stores, web search, and a restricted terminal.

Rules:
- Only use tools when you actually need them
- Prefer data store tools over web search for questions about past conversations
- Be concise — the user is a developer, not a civilian
- If a shell command fails or is blocked, say so and suggest an alternative
- Never make up results — if a tool returns nothing, say so"""


# ---------------------------------------------------------------------------
# Shell tool — allowlist enforced
# ---------------------------------------------------------------------------

class AllowlistedShell:
    def __init__(self, allowed_commands: list[str], blocked_patterns: list[str]):
        self.allowed = set(allowed_commands)
        self.blocked = blocked_patterns

    def run(self, command: str) -> str:
        command = command.strip()
        if not command:
            return "Error: empty command"

        try:
            parts = shlex.split(command)
        except ValueError as e:
            return f"Error: could not parse command: {e}"

        base = Path(parts[0]).name

        if base not in self.allowed:
            return (
                f"Blocked: '{base}' is not in the allowed command list.\n"
                f"Allowed: {sorted(self.allowed) or '(none configured)'}\n"
                f"Edit operator.shell.allowed_commands in config.yaml to add it."
            )

        for pattern in self.blocked:
            if pattern.lower() in command.lower():
                return f"Blocked: command contains disallowed pattern '{pattern}'"

        try:
            result = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
            output = result.stdout.strip()
            stderr = result.stderr.strip()
            if result.returncode != 0:
                return f"Exit {result.returncode}\n{stderr or output}"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 15 seconds"
        except FileNotFoundError:
            return f"Error: '{base}' not found on PATH"
        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# SQLite named query tool
# ---------------------------------------------------------------------------

class SQLiteQueryTool:
    def __init__(self, db_path: str, named_queries: dict[str, str]):
        self.db_path = Path(db_path)
        self.named_queries = named_queries

    def list_queries(self) -> str:
        if not self.named_queries:
            return (
                "No named queries configured. "
                "Add them under operator.data.sqlite_queries in config.yaml."
            )
        return "Available queries:\n" + "\n".join(f"  - {k}" for k in self.named_queries)

    def run(self, query_name: str) -> str:
        query_name = query_name.strip()

        if query_name.lower() in ("list", "help", "?", ""):
            return self.list_queries()

        param = None
        if "|" in query_name:
            parts = query_name.split("|", 1)
            query_name = parts[0].strip()
            param = parts[1].strip()

        sql = self.named_queries.get(query_name)
        if not sql:
            close = [k for k in self.named_queries if query_name.lower() in k.lower()]
            hint = f" Did you mean: {close}?" if close else ""
            return f"Unknown query '{query_name}'.{hint}\n{self.list_queries()}"

        if not self.db_path.exists():
            return f"Database not found at {self.db_path}. Run 'beigebox dial' first."

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if param and "?" in sql:
                cursor.execute(sql, (f"%{param}%",))
            else:
                cursor.execute(sql)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return "Query returned no results."

            cols = rows[0].keys()
            lines = ["  ".join(str(c).ljust(16) for c in cols)]
            lines.append("  ".join("─" * 16 for _ in cols))
            for row in rows[:50]:
                lines.append("  ".join(str(row[c])[:16].ljust(16) for c in cols))
            if len(rows) > 50:
                lines.append(f"... ({len(rows) - 50} more rows)")
            return "\n".join(lines)

        except Exception as e:
            return f"Query failed: {e}"


# ---------------------------------------------------------------------------
# ChromaDB semantic search tool
# ---------------------------------------------------------------------------

class SemanticSearchTool:
    def __init__(self, vector_store):
        self.vs = vector_store

    def run(self, query: str) -> str:
        try:
            results = self.vs.search(query.strip(), n_results=5)
            if not results:
                return "No relevant past conversations found."

            lines = []
            for i, hit in enumerate(results, 1):
                score = round(1 - hit["distance"], 3)
                meta = hit.get("metadata", {})
                content = hit.get("content", "")[:400]
                role = meta.get("role", "?")
                model = meta.get("model", "?")
                lines.append(
                    f"[{i}] score={score} role={role} model={model}\n    {content}"
                )
            return "\n\n".join(lines)

        except Exception as e:
            return f"Semantic search failed: {e}"


# ---------------------------------------------------------------------------
# Operator — the assembled agent
# ---------------------------------------------------------------------------

class Operator:
    """
    LangGraph ReAct agent with web, data, and shell tools.

    Usage:
        op = Operator()
        result = op.run("What have we talked about regarding Docker networking?")
    """

    def __init__(self, vector_store=None):
        cfg = get_config()
        op_cfg = cfg.get("operator", {})
        model_name = op_cfg.get("model") or cfg["backend"].get("default_model", "")
        backend_url = cfg["backend"]["url"].rstrip("/")

        self.llm = ChatOllama(
            model=model_name,
            base_url=backend_url,
            temperature=0.1,
        )

        self.tools = self._build_tools(cfg, op_cfg, vector_store)

        self.executor = create_react_agent(
            self.llm,
            self.tools,
            prompt=_SYSTEM_PROMPT,
        )

        logger.info(
            "Operator initialized: model=%s tools=%s",
            model_name,
            [t.name for t in self.tools],
        )

    def _build_tools(self, cfg: dict, op_cfg: dict, vector_store) -> list[Tool]:
        tools: list[Tool] = []

        # Web search
        try:
            ddg = DuckDuckGoSearchResults(max_results=5)
            tools.append(Tool(
                name="web_search",
                func=lambda q: ddg.invoke(q),
                description=(
                    "Search the web using DuckDuckGo. "
                    "Input: a search query string. "
                    "Use for current events, facts, documentation lookups."
                ),
            ))
        except Exception as e:
            logger.warning("DDG search tool unavailable: %s", e)

        # Web scraper
        try:
            from beigebox.tools.web_scraper import WebScraperTool
            scraper = WebScraperTool()
            tools.append(Tool(
                name="web_scrape",
                func=scraper.run,
                description=(
                    "Fetch and extract text content from a URL. "
                    "Input: a full URL (https://...). "
                    "Use after web_search to read the actual content of a page."
                ),
            ))
        except Exception as e:
            logger.warning("Web scraper tool unavailable: %s", e)

        # Semantic search
        if vector_store is not None:
            sem = SemanticSearchTool(vector_store)
            tools.append(Tool(
                name="conversation_search",
                func=sem.run,
                description=(
                    "Semantic search over stored conversation history. "
                    "Input: a natural language query about past conversations. "
                    "Use to find what was discussed previously, recall past solutions, etc."
                ),
            ))

        # SQLite named queries
        data_cfg = op_cfg.get("data", {})
        named_queries = data_cfg.get("sqlite_queries", {})
        db_path = cfg["storage"]["sqlite_path"]
        sq = SQLiteQueryTool(db_path, named_queries)
        tools.append(Tool(
            name="database_query",
            func=sq.run,
            description=(
                "Run a named query against the conversation database. "
                "Input: the query name (e.g. 'recent_conversations'), "
                "or 'list' to see available queries. "
                "For queries that accept a search term, use 'query_name | search term'. "
                "Use to get stats, find conversations, or analyze usage."
            ),
        ))

        # Shell (allowlisted)
        shell_cfg = op_cfg.get("shell", {})
        if shell_cfg.get("enabled", False):
            shell = AllowlistedShell(
                allowed_commands=shell_cfg.get("allowed_commands", []),
                blocked_patterns=shell_cfg.get("blocked_patterns", []),
            )
            tools.append(Tool(
                name="shell",
                func=shell.run,
                description=(
                    "Run an allowlisted shell command and return the output. "
                    "Input: a shell command string. "
                    "Only commands on the configured allowlist will execute. "
                    "Use for system info, checking Ollama status, file inspection, etc."
                ),
            ))

        return tools

    def run(self, question: str) -> str:
        """Run the agent on a single question. Returns the final answer."""
        try:
            result = self.executor.invoke(
                {"messages": [{"role": "user", "content": question}]}
            )
            return result["messages"][-1].content
        except Exception as e:
            logger.error("Operator failed: %s", e)
            return f"Error: {e}"

    def stream(self, question: str):
        """
        Stream agent steps. Yields (step_type, content) tuples.
        step_type is one of: 'action', 'observation', 'answer', 'error'
        Used by the TUI OperatorScreen for live display.
        """
        try:
            for chunk in self.executor.stream(
                {"messages": [{"role": "user", "content": question}]}
            ):
                if "agent" in chunk:
                    for msg in chunk["agent"].get("messages", []):
                        if msg.content:
                            yield ("answer", msg.content)
                elif "tools" in chunk:
                    for msg in chunk["tools"].get("messages", []):
                        yield ("observation", str(msg.content)[:400])
        except Exception as e:
            yield ("error", str(e))
