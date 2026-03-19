"""
Worker Profiles: Constraints and output schemas per agent type.

Each worker (research, coder, operator, judge) has:
- Constraints: what it must/must-not do, tool limits
- Output schema: what structure it must return

These are loaded into packets to enforce boundaries and contracts.
"""

from typing import Any, Dict

from beigebox.orchestration.packet import WorkerType


class WorkerProfiles:
    """
    Registry of worker profiles (constraints, tools, output schemas).

    Profiles define:
    - must_do: Required actions
    - must_not_do: Forbidden actions
    - tool_limits: Available tools and call limits
    - output_schema: Required result structure
    """

    def __init__(self):
        """Initialize all worker profiles."""
        self.profiles = self._build_profiles()

    def get_profile(self, worker: WorkerType) -> Dict[str, Any]:
        """Get the profile for a worker type."""
        return self.profiles.get(worker.value, self._default_profile())

    @staticmethod
    def _default_profile() -> Dict[str, Any]:
        """Default profile (fallback for unknown workers)."""
        return {
            "constraints": {
                "must_do": ["Stay within provided task", "Return structured output"],
                "must_not_do": ["Rewrite the task", "Invent missing evidence"],
                "tool_limits": ["max_tool_calls=3"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "enum": ["success", "needs_escalation", "blocked"],
                        "description": "Outcome of the task",
                    },
                    "answer": {"type": "string", "description": "Primary result"},
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence (0-1)",
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Supporting evidence",
                    },
                    "follow_up_needed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tasks for supervisor",
                    },
                    "artifacts_created": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Generated artifacts",
                    },
                },
                "required": ["status", "answer", "confidence"],
            },
            "routing": {
                "return_to": "supervisor",
                "on_fail": "escalate",
                "on_ambiguous": "ask_supervisor",
            },
        }

    def _build_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Build all worker profiles."""
        return {
            WorkerType.RESEARCH.value: self._research_profile(),
            WorkerType.CODER.value: self._coder_profile(),
            WorkerType.OPERATOR.value: self._operator_profile(),
            WorkerType.JUDGE.value: self._judge_profile(),
        }

    @staticmethod
    def _research_profile() -> Dict[str, Any]:
        """Profile for research agent (information gathering, analysis)."""
        return {
            "constraints": {
                "must_do": [
                    "Collect evidence before concluding",
                    "Cite sources or line numbers",
                    "Verify dates and facts",
                ],
                "must_not_do": [
                    "Write code unless explicitly asked",
                    "Speculate beyond provided context",
                    "Invent missing information",
                ],
                "tool_limits": [
                    "web_search: max 3 calls",
                    "read_doc: max 5 calls",
                    "no code execution",
                ],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["success", "needs_escalation", "blocked"]},
                    "answer": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Sources, citations, line numbers",
                    },
                    "sources_checked": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "follow_up_needed": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "answer", "evidence"],
            },
        }

    @staticmethod
    def _coder_profile() -> Dict[str, Any]:
        """Profile for coder agent (code generation, diagnosis)."""
        return {
            "constraints": {
                "must_do": [
                    "Propose concrete code or diagnosis",
                    "Cite line numbers when referencing files",
                    "Explain reasoning",
                ],
                "must_not_do": [
                    "Browse unrelated architecture",
                    "Speculate on missing code",
                    "Make breaking changes without consent",
                ],
                "tool_limits": [
                    "read_file: max 10 calls",
                    "search_code: max 5 calls",
                    "write_file: max 3 calls",
                    "no web access",
                ],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["success", "needs_escalation", "blocked"]},
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "code_changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "change": {"type": "string"},
                            },
                        },
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "follow_up_needed": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "answer"],
            },
        }

    @staticmethod
    def _operator_profile() -> Dict[str, Any]:
        """Profile for operator agent (tool execution, navigation)."""
        return {
            "constraints": {
                "must_do": [
                    "Execute the specified task",
                    "Report results clearly",
                ],
                "must_not_do": [
                    "Call tools not in tool_limits",
                    "Modify system state without consent",
                    "Attempt lateral movement",
                ],
                "tool_limits": [
                    "cdp.*: max 10 calls",
                    "search_code: max 5 calls",
                    "memory: max 3 calls",
                    "no shell access",
                ],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["success", "needs_escalation", "blocked"]},
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "tool_calls_made": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string"},
                                "result": {"type": "string"},
                            },
                        },
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "follow_up_needed": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "answer"],
            },
        }

    @staticmethod
    def _judge_profile() -> Dict[str, Any]:
        """Profile for judge agent (comparison, decision-making)."""
        return {
            "constraints": {
                "must_do": [
                    "Compare competing options fairly",
                    "Cite specific differences",
                    "Make a clear recommendation",
                ],
                "must_not_do": [
                    "Introduce new implementation ideas",
                    "Favor one option without reasoning",
                    "Ignore provided evidence",
                ],
                "tool_limits": [
                    "no tools (analysis only)",
                ],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["success", "needs_escalation", "blocked"]},
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                    "comparison": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "option": {"type": "string"},
                                "pros": {"type": "array", "items": {"type": "string"}},
                                "cons": {"type": "array", "items": {"type": "string"}},
                                "score": {"type": "number"},
                            },
                        },
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "follow_up_needed": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "answer", "comparison"],
            },
        }
