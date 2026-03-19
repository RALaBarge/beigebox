"""
Task Packet Orchestration: Multi-Agent Context Distillation

This module implements structured handoffs between orchestrator and subagents,
solving the "context bloat" problem in multi-agent systems.

Core components:
- TaskPacket: Minimal, structured boundary object for agent handoff
- PacketComposer: Distills global state → focused packet
- ResultValidator: Ensures agent output matches contract
- StateMerger: Merges validated results into durable state
- PromptOptimizer: Iterative self-refinement via Champion/Challenger loops

See 2600/task-packet-orchestration.md for architecture details.
"""

from beigebox.orchestration.packet import (
    WorkerType,
    TaskPacket,
    WorkerResult,
)
from beigebox.orchestration.composer import PacketComposer
from beigebox.orchestration.validator import ResultValidator
from beigebox.orchestration.merger import StateMerger
from beigebox.orchestration.optimizer import (
    PromptOptimizer,
    ScoreCard,
    MutationStrategy,
)

__all__ = [
    "WorkerType",
    "TaskPacket",
    "WorkerResult",
    "PacketComposer",
    "ResultValidator",
    "StateMerger",
    "PromptOptimizer",
    "ScoreCard",
    "MutationStrategy",
]
