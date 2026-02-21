"""
Operator agent — the brain behind `beigebox operator` and the web UI Operator tab.

Uses a LangChain ReAct loop with the enabled tool registry to answer questions
about conversations, system state, the web, and local data.

No TUI dependency. Works from CLI, HTTP API, or any caller.

    op = Operator(vector_store=vs)
    answer = op.run("How many conversations happened today?")
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class Operator:
    """
    Lightweight LLM agent using the BeigeBox tool registry.

    Wraps a LangChain ReAct chain with graceful fallback:
    - If LangChain / Ollama is unavailable, falls back to direct tool dispatch.
    - Tools list is pulled from ToolRegistry so config drives what's available.
    """

    def __init__(self, vector_store=None):
        from beigebox.config import get_config
        from beigebox.tools.registry import ToolRegistry

        self.cfg = get_config()
        self.vector_store = vector_store
        self._registry = ToolRegistry(vector_store=vector_store)
        self._chain = None
        self.tools: list[Any] = []
        self._model = (
            self.cfg.get("operator", {}).get("model")
            or self.cfg.get("backend", {}).get("default_model", "")
        )
        self._build_chain()

    # ------------------------------------------------------------------
    # Chain setup
    # ------------------------------------------------------------------

    def _build_chain(self) -> None:
        """Build a LangChain ReAct agent over the enabled tools."""
        try:
            from langchain_ollama import ChatOllama
            from langchain.agents import AgentExecutor, create_react_agent
            from langchain_core.prompts import PromptTemplate
            from langchain_core.tools import Tool

            backend_url = self.cfg.get("embedding", {}).get(
                "backend_url",
                self.cfg.get("backend", {}).get("url", "http://localhost:11434"),
            )

            llm = ChatOllama(
                model=self._model,
                base_url=backend_url,
                temperature=0,
            )

            # Wrap registry tools as LangChain Tool objects
            lc_tools = []
            for name, tool_obj in self._registry.tools.items():
                description = getattr(tool_obj, "description", f"Run the {name} tool")
                lc_tools.append(
                    Tool(
                        name=name,
                        func=lambda inp, t=tool_obj: t.run(inp),
                        description=description,
                    )
                )
            self.tools = lc_tools

            if not lc_tools:
                logger.warning("Operator: no tools available — running in direct LLM mode")

            prompt = PromptTemplate.from_template(
                "You are BeigeBox Operator, an assistant with access to tools.\n"
                "Answer the question as helpfully as possible.\n\n"
                "Tools available:\n{tools}\n\n"
                "Use this format:\n"
                "Question: the input question\n"
                "Thought: think step by step\n"
                "Action: the tool name\n"
                "Action Input: the input to the tool\n"
                "Observation: the result\n"
                "... (repeat Thought/Action/Observation as needed)\n"
                "Thought: I now know the final answer\n"
                "Final Answer: the answer\n\n"
                "Tool names: {tool_names}\n\n"
                "Question: {input}\n"
                "Thought: {agent_scratchpad}"
            )

            agent = create_react_agent(llm=llm, tools=lc_tools, prompt=prompt)
            max_iter = self.cfg.get("operator", {}).get("max_iterations", 10)
            self._chain = AgentExecutor(
                agent=agent,
                tools=lc_tools,
                verbose=False,
                max_iterations=max_iter,
                handle_parsing_errors=True,
            )
            logger.info(
                "Operator ready (model=%s, tools=%s)",
                self._model,
                [t.name for t in lc_tools],
            )

        except Exception as e:
            logger.warning("Operator: could not build ReAct chain: %s", e)
            self._chain = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, question: str) -> str:
        """
        Run the operator on a question. Returns the answer as a string.
        Falls back to direct LLM if the chain failed to build.
        """
        if not question.strip():
            return "No question provided."

        if self._chain is not None:
            try:
                result = self._chain.invoke({"input": question})
                return result.get("output", str(result))
            except Exception as e:
                logger.error("Operator chain error: %s", e)
                return f"Operator error: {e}"

        # Fallback: no chain, try direct LLM
        return self._fallback_llm(question)

    def _fallback_llm(self, question: str) -> str:
        """Direct LLM call without tool loop — last resort."""
        try:
            from langchain_ollama import ChatOllama
            backend_url = self.cfg.get("embedding", {}).get(
                "backend_url",
                self.cfg.get("backend", {}).get("url", "http://localhost:11434"),
            )
            llm = ChatOllama(model=self._model, base_url=backend_url, temperature=0)
            response = llm.invoke(question)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            return f"Operator unavailable: {e}. Make sure Ollama is running with model '{self._model}'."
