"""
TaskPacket schemas: The boundary object between orchestration and reasoning.

This module defines:
- WorkerType: Enumeration of available agent types (research, coder, operator, judge)
- TaskPacket: Minimal, structured handoff to a subagent
- WorkerResult: Structured output contract from agents

Key principle: Everything the agent needs is in the packet. Nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime


class WorkerType(str, Enum):
    """Available subagent types in BeigeBox orchestration."""

    RESEARCH = "research"      # Information gathering, analysis
    CODER = "coder"            # Code generation, diagnosis
    OPERATOR = "operator"      # Tool execution, navigation
    JUDGE = "judge"            # Comparison, decision-making


@dataclass
class TaskPacket:
    """
    Minimal, structured handoff to a subagent.

    This is the boundary object between orchestrator (global state) and
    subagent (reasoning). It contains everything the agent needs and
    explicitly excludes everything it doesn't.

    Attributes:
        task_id: UUID for replay, debugging, and audit trail
        worker: Which agent type this packet is for (research|coder|operator|judge)
        objective: What the agent must accomplish (worker-specific restatement of goal)
        question: The concrete task or question

        context: Curated context (not full history)
            - facts: Known facts relevant to the task
            - recent_dialogue: Last N relevant conversation turns
            - prior_results: Results from previous agent calls
            - artifacts: Generated code, documents, files

        constraints: Boundaries for the agent
            - must_do: Required actions or outputs
            - must_not_do: Forbidden actions
            - tool_limits: Which tools available and call limits

        output_schema: Required structure for result (dict or Pydantic schema)

        routing: Instructions for handling result
            - return_to: Who handles the result (supervisor, etc.)
            - on_fail: Behavior if agent returns status=blocked (escalate, retry, etc.)
            - on_ambiguous: Behavior if confidence is low (ask_supervisor, escalate, etc.)
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    worker: WorkerType = WorkerType.OPERATOR
    objective: str = ""
    question: str = ""

    # Curated context (not full conversation history)
    context: Dict[str, Any] = field(default_factory=dict)

    # Boundaries
    constraints: Dict[str, Any] = field(default_factory=dict)

    # Output contract
    output_schema: Dict[str, Any] = field(default_factory=dict)

    # Routing rules
    routing: Dict[str, str] = field(
        default_factory=lambda: {
            "return_to": "supervisor",
            "on_fail": "escalate",
            "on_ambiguous": "ask_supervisor",
        }
    )

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert packet to dict for serialization."""
        return {
            "task_id": self.task_id,
            "worker": self.worker.value,
            "objective": self.objective,
            "question": self.question,
            "context": self.context,
            "constraints": self.constraints,
            "output_schema": self.output_schema,
            "routing": self.routing,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskPacket:
        """Reconstruct packet from dict (for replay)."""
        if isinstance(data.get("worker"), str):
            data["worker"] = WorkerType(data["worker"])
        return cls(**data)


@dataclass
class WorkerResult:
    """
    Structured result from an agent.

    This enforces the output contract defined in TaskPacket.output_schema.
    All agent results must validate against this schema before merging
    into global state.

    Attributes:
        status: Outcome (success=completed task, needs_escalation=ask supervisor,
                blocked=cannot proceed)
        answer: The primary result (string, code, decision, etc.)
        confidence: How confident the agent is (0.0-1.0). Used to decide
                    whether to merge result into durable facts.
        evidence: List of supporting evidence or reasoning steps
        follow_up_needed: Tasks or questions for supervisor to handle
        artifacts_created: Any files, code, or documents created by agent
    """

    status: Literal["success", "needs_escalation", "blocked"] = "success"
    answer: str = ""
    confidence: float = 0.5
    evidence: List[str] = field(default_factory=list)
    follow_up_needed: List[str] = field(default_factory=list)
    artifacts_created: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dict."""
        return {
            "status": self.status,
            "answer": self.answer,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "follow_up_needed": self.follow_up_needed,
            "artifacts_created": self.artifacts_created,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerResult:
        """Reconstruct result from dict."""
        return cls(**data)
