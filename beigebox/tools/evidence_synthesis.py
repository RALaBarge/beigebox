"""
EvidenceSynthesisTool — analyzes multiple research findings, extracts patterns,
generates actionable recommendations.

Takes findings from ResearchAgentTool / ParallelResearchTool and synthesizes
them into a coherent analysis with pattern extraction, contradiction detection,
and evidence gap identification.

Input format (JSON string):
    {
        "findings_list": [
            {"topic": "RAG poisoning", "findings": "...", "sources": [...]},
            {"topic": "MCP injection", "findings": "...", "sources": [...]}
        ],
        "synthesis_question": "What are the key strategic recommendations?",
        "output_format": "analysis"
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

from beigebox.config import get_config

logger = logging.getLogger(__name__)

_WORKSPACE_OUT = Path(
    os.environ.get("WORKSPACE_OUT", "/app/workspace/out")
).resolve()

_FORMAT_INSTRUCTIONS = {
    "analysis": (
        "Provide a detailed analytical synthesis. Include: executive summary, "
        "cross-cutting patterns, detailed analysis of each pattern, contradictions "
        "between sources, evidence gaps, and strategic recommendations."
    ),
    "summary": (
        "Provide a concise summary (2-3 paragraphs). Focus on the most important "
        "patterns and top 3-5 recommendations. Be brief but actionable."
    ),
    "recommendations": (
        "Focus exclusively on actionable recommendations. For each recommendation: "
        "state what to do, why (citing evidence), priority level (critical/high/medium/low), "
        "and effort estimate. Skip background analysis."
    ),
}

_SYNTHESIS_SYSTEM = """\
You are an evidence synthesis agent. You analyze multiple research findings to extract \
cross-cutting patterns, identify contradictions, and generate actionable recommendations.

SYNTHESIS QUESTION: {question}

FORMAT: {format_instruction}

You have been given {count} research findings to synthesize.

Respond with a JSON object:
{{
    "synthesis": "markdown-formatted synthesis/analysis",
    "patterns": ["list of cross-cutting patterns identified"],
    "recommendations": ["list of actionable recommendations"],
    "confidence": 0.0-1.0,
    "contradictions": ["list of contradictions between findings, if any"],
    "evidence_gaps": ["list of things still unknown or under-investigated"]
}}

Output ONLY the JSON object. No prose before or after."""


class EvidenceSynthesisTool:
    description = (
        'Synthesize multiple research findings into patterns and recommendations. '
        'input MUST be a JSON object with keys: '
        '"findings_list" (list of finding objects), "synthesis_question" (string). '
        'Optional: "output_format" ("analysis"|"summary"|"recommendations"). '
        'Each finding: {"topic": str, "findings": str, "sources": [str]}. '
        'Returns synthesis with patterns, recommendations, contradictions, and evidence gaps. '
        'Example: {"findings_list":[{"topic":"X","findings":"...","sources":[]}],'
        '"synthesis_question":"What are the key risks?"}'
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

    def _chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        """Synchronous LLM call."""
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

    def _format_findings_for_prompt(self, findings_list: list[dict]) -> str:
        """Format findings into a structured prompt block."""
        blocks = []
        for i, f in enumerate(findings_list, 1):
            topic = f.get("topic", f"Finding {i}")
            text = f.get("findings", "No findings provided.")
            sources = f.get("sources", [])
            block = f"### Finding {i}: {topic}\n{text}"
            if sources:
                block += "\n**Sources:** " + ", ".join(sources)
            blocks.append(block)
        return "\n\n".join(blocks)

    def _parse_synthesis(self, raw: str) -> dict:
        """Extract structured synthesis from LLM output."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

        try:
            parsed = json.loads(raw)
            return {
                "synthesis": parsed.get("synthesis", raw),
                "patterns": parsed.get("patterns", []),
                "recommendations": parsed.get("recommendations", []),
                "confidence": float(parsed.get("confidence", 0.5)),
                "contradictions": parsed.get("contradictions", []),
                "evidence_gaps": parsed.get("evidence_gaps", []),
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return {
                "synthesis": raw,
                "patterns": [],
                "recommendations": [],
                "confidence": 0.3,
                "contradictions": [],
                "evidence_gaps": [],
            }

    def _save_synthesis(self, result: dict, question: str):
        """Save synthesis to workspace/out/synthesis_result.md."""
        self._root.mkdir(parents=True, exist_ok=True)
        filepath = self._root / "synthesis_result.md"

        lines = [
            "# Evidence Synthesis",
            f"*Question: {question}*",
            f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
            f"*Confidence: {result['confidence']:.0%}*",
            "",
            "## Synthesis",
            result["synthesis"],
            "",
        ]
        if result["patterns"]:
            lines.append("## Patterns")
            for p in result["patterns"]:
                lines.append(f"- {p}")
            lines.append("")
        if result["recommendations"]:
            lines.append("## Recommendations")
            for r in result["recommendations"]:
                lines.append(f"- {r}")
            lines.append("")
        if result["contradictions"]:
            lines.append("## Contradictions")
            for c in result["contradictions"]:
                lines.append(f"- {c}")
            lines.append("")
        if result["evidence_gaps"]:
            lines.append("## Evidence Gaps")
            for g in result["evidence_gaps"]:
                lines.append(f"- {g}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return str(filepath)

    def _execute_synthesis(self, findings_list: list[dict],
                           synthesis_question: str,
                           output_format: str) -> dict:
        """Run the synthesis LLM call."""
        format_instruction = _FORMAT_INSTRUCTIONS.get(
            output_format, _FORMAT_INSTRUCTIONS["analysis"]
        )
        findings_text = self._format_findings_for_prompt(findings_list)

        system_prompt = _SYNTHESIS_SYSTEM.format(
            question=synthesis_question,
            format_instruction=format_instruction,
            count=len(findings_list),
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Here are the research findings to synthesize:\n\n{findings_text}\n\n"
                f"Synthesize these findings to answer: {synthesis_question}"
            )},
        ]

        try:
            raw = self._chat(messages)
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            return {
                "synthesis": f"Synthesis failed: {e}",
                "patterns": [],
                "recommendations": [],
                "confidence": 0.0,
                "contradictions": [],
                "evidence_gaps": [synthesis_question],
                "error": str(e),
            }

        result = self._parse_synthesis(raw)
        filepath = self._save_synthesis(result, synthesis_question)
        result["saved_to"] = filepath
        return result

    async def execute(self, findings_list: list[dict], synthesis_question: str,
                      output_format: str = "analysis") -> dict:
        """Async entry point for direct Python callers."""
        return await asyncio.to_thread(
            self._execute_synthesis, findings_list, synthesis_question, output_format
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
                'Example: {"findings_list":[...],"synthesis_question":"What are the risks?"}'
            )

        findings_list = params.get("findings_list", [])
        if not findings_list or not isinstance(findings_list, list):
            return 'Error: "findings_list" must be a non-empty list of finding objects.'

        question = params.get("synthesis_question", "").strip()
        if not question:
            return 'Error: "synthesis_question" is required.'

        output_format = params.get("output_format", "analysis")
        if output_format not in _FORMAT_INSTRUCTIONS:
            output_format = "analysis"

        t0 = time.monotonic()
        try:
            result = self._execute_synthesis(findings_list, question, output_format)
        except Exception as e:
            logger.error("EvidenceSynthesisTool failed: %s", e)
            return json.dumps({
                "synthesis": f"Synthesis failed: {e}",
                "patterns": [],
                "recommendations": [],
                "confidence": 0.0,
                "contradictions": [],
                "evidence_gaps": [],
                "error": str(e),
            })

        result["execution_time"] = round(time.monotonic() - t0, 2)
        return json.dumps(result)
