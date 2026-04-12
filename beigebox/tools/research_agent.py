"""
ResearchAgentTool — launches a focused research agent on a subtopic.

Uses BeigeBox's configured LLM backend to run a multi-turn research loop.
Returns structured findings with citations, confidence, and follow-up questions.

Input format (JSON string):
    {
        "topic": "RAG poisoning attacks",
        "research_questions": ["What are the main attack vectors?", "How to detect them?"],
        "max_turns": 10,
        "depth": "medium"
    }
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from beigebox.config import get_config, get_runtime_config

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()

# Depth presets: (max_turns_multiplier, system_prompt_addendum)
_DEPTH_PRESETS = {
    "quick":  (0.5, "Be concise. Provide a brief overview with key points only."),
    "medium": (1.0, "Be thorough. Cover main aspects with supporting detail."),
    "deep":   (2.0, "Be exhaustive. Cover every angle, include edge cases, cite specifics."),
}

_RESEARCH_SYSTEM = """\
You are a focused research agent. Your task is to investigate a specific topic and answer \
research questions with structured, evidence-based findings.

TOPIC: {topic}

RESEARCH QUESTIONS:
{questions}

INSTRUCTIONS:
- {depth_instruction}
- Structure your response as a comprehensive research report.
- Include specific facts, data points, and citations where possible.
- Identify areas of uncertainty and flag what you don't know.
- Suggest follow-up questions that would deepen understanding.

Respond with a JSON object:
{{
    "findings": "markdown-formatted research findings",
    "sources": ["list of sources/references cited"],
    "confidence": 0.0-1.0,
    "next_questions": ["unanswered or follow-up questions"]
}}

Output ONLY the JSON object. No prose before or after."""


class ResearchAgentTool:
    description = (
        'Launch a focused research agent on a topic. '
        'input MUST be a JSON object with keys: '
        '"topic" (string), "research_questions" (list of strings). '
        'Optional: "max_turns" (int, default 10), "depth" ("quick"|"medium"|"deep"). '
        'Returns structured findings with sources and confidence score. '
        'Example: {"topic":"MCP injection vectors","research_questions":["What are known attack patterns?"],"depth":"medium"}'
    )

    def __init__(self, workspace_out: Path | None = None):
        self._root = (workspace_out or _WORKSPACE_OUT).resolve()
        cfg = get_config()
        models_cfg = cfg.get("models", {})
        self._model = (
            models_cfg.get("profiles", {}).get("agentic")
            or models_cfg.get("default")
            or cfg.get("backend", {}).get("default_model", "")
        )
        self._backend_url = (
            cfg.get("embedding", {}).get("backend_url")
            or cfg.get("backend", {}).get("url", "http://localhost:11434")
        ).rstrip("/")
        self._timeout = cfg.get("operator", {}).get("timeout", 300)

    def _chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        """Synchronous LLM call via OpenAI-compatible endpoint."""
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "options": {"num_ctx": 8192},
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self._backend_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _chat_async(self, messages: list[dict], temperature: float = 0.3) -> str:
        """Async LLM call via OpenAI-compatible endpoint."""
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "options": {"num_ctx": 8192},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._backend_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    def _parse_findings(self, raw: str, topic: str) -> dict:
        """Extract structured findings from LLM output."""
        # Try JSON parse first
        raw = raw.strip()
        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

        try:
            parsed = json.loads(raw)
            return {
                "topic": topic,
                "findings": parsed.get("findings", raw),
                "sources": parsed.get("sources", []),
                "confidence": float(parsed.get("confidence", 0.5)),
                "next_questions": parsed.get("next_questions", []),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            # Fallback: treat entire output as findings text
            return {
                "topic": topic,
                "findings": raw,
                "sources": [],
                "confidence": 0.3,
                "next_questions": [],
            }

    def _save_findings(self, result: dict):
        """Save findings to workspace/out/{topic}_research.md."""
        self._root.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in result["topic"])
        safe_name = safe_name.strip().replace(" ", "_").lower()[:60]
        filepath = self._root / f"{safe_name}_research.md"

        lines = [
            f"# Research: {result['topic']}",
            f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
            f"*Confidence: {result['confidence']:.0%}*",
            "",
            "## Findings",
            result["findings"],
            "",
        ]
        if result["sources"]:
            lines.append("## Sources")
            for src in result["sources"]:
                lines.append(f"- {src}")
            lines.append("")
        if result["next_questions"]:
            lines.append("## Open Questions")
            for q in result["next_questions"]:
                lines.append(f"- {q}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return str(filepath)

    def _execute_research(self, topic: str, research_questions: list[str],
                          max_turns: int, depth: str) -> dict:
        """Run the research loop synchronously."""
        multiplier, depth_instruction = _DEPTH_PRESETS.get(depth, _DEPTH_PRESETS["medium"])
        effective_turns = max(1, int(max_turns * multiplier))

        questions_block = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(research_questions))
        system_prompt = _RESEARCH_SYSTEM.format(
            topic=topic,
            questions=questions_block,
            depth_instruction=depth_instruction,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Research the topic '{topic}' and answer the questions listed above. "
                f"Provide structured findings."
            )},
        ]

        # Multi-turn: for deep research, do iterative refinement
        last_raw = ""
        for turn in range(effective_turns):
            try:
                last_raw = self._chat(messages, temperature=0.3)
            except Exception as e:
                logger.error("Research agent LLM call failed (turn %d): %s", turn, e)
                if last_raw:
                    break
                return {
                    "topic": topic,
                    "findings": f"Research failed: {e}",
                    "sources": [],
                    "confidence": 0.0,
                    "next_questions": research_questions,
                }

            result = self._parse_findings(last_raw, topic)

            # If confidence is high enough or we only have one turn, stop
            if result["confidence"] >= 0.7 or effective_turns == 1:
                break

            # Otherwise, refine: ask for deeper investigation on gaps
            if result["next_questions"] and turn < effective_turns - 1:
                follow_up = "; ".join(result["next_questions"][:3])
                messages.append({"role": "assistant", "content": last_raw})
                messages.append({"role": "user", "content": (
                    f"Good findings so far. Now dig deeper into these gaps: {follow_up}. "
                    f"Integrate new findings with the previous ones and return updated JSON."
                )})
            else:
                break

        result = self._parse_findings(last_raw, topic)
        filepath = self._save_findings(result)
        result["saved_to"] = filepath
        return result

    async def execute(self, topic: str, research_questions: list[str],
                      max_turns: int = 10, depth: str = "medium") -> dict:
        """Async entry point for direct Python callers."""
        return await asyncio.to_thread(
            self._execute_research, topic, research_questions, max_turns, depth
        )

    def run(self, input_text: str) -> str:
        """Synchronous entry point for the Operator tool registry."""
        try:
            params = json.loads(input_text)
            if not isinstance(params, dict):
                raise ValueError("not a dict")
        except (json.JSONDecodeError, TypeError, ValueError):
            return (
                'Error: input must be a JSON object. '
                'Example: {"topic":"RAG poisoning","research_questions":["What are the vectors?"]}'
            )

        topic = params.get("topic", "").strip()
        if not topic:
            return 'Error: "topic" is required.'

        questions = params.get("research_questions", [])
        if not questions or not isinstance(questions, list):
            return 'Error: "research_questions" must be a non-empty list of strings.'

        max_turns = int(params.get("max_turns", 10))
        depth = params.get("depth", "medium")
        if depth not in _DEPTH_PRESETS:
            depth = "medium"

        t0 = time.monotonic()
        try:
            result = self._execute_research(topic, questions, max_turns, depth)
        except Exception as e:
            logger.error("ResearchAgentTool failed: %s", e)
            return json.dumps({
                "topic": topic,
                "findings": f"Research failed: {e}",
                "sources": [],
                "confidence": 0.0,
                "next_questions": questions,
                "error": str(e),
            })

        result["execution_time"] = round(time.monotonic() - t0, 2)
        return json.dumps(result)
