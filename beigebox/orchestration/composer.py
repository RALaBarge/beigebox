"""
PacketComposer: Distill global state → focused task packets.

The composer performs context slicing to convert noisy global state into
lean, focused packets that minimize hallucination and token waste.

Strategy (incremental):
- Phase 1 (now): Heuristic slicing (last N messages + known facts)
- Phase 2 (week 2): Semantic search (vector similarity to subtask)
- Phase 3 (month 2): LLM-based relevance (small model rates importance)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from beigebox.orchestration.packet import TaskPacket, WorkerType
from beigebox.orchestration.worker_profiles import WorkerProfiles

logger = logging.getLogger(__name__)


class PacketComposer:
    """
    Composes TaskPackets by slicing global state into focused context.

    This is a critical component: if the composer does poorly, the agent fails.
    Start simple (heuristic), evolve to semantic search.
    """

    def __init__(self):
        """Initialize composer with worker profiles."""
        self.worker_profiles = WorkerProfiles()

    def compose(
        self,
        global_state: Dict[str, Any],
        worker: WorkerType,
        objective: str,
        subtask: str,
        max_context_tokens: int = 1000,
    ) -> TaskPacket:
        """
        Compose a focused task packet from global state.

        Args:
            global_state: Full conversation state (messages, facts, artifacts, etc.)
            worker: Target worker type (research|coder|operator|judge)
            objective: High-level goal restatement for this worker
            subtask: Concrete question or task
            max_context_tokens: Approximate token limit for context

        Returns:
            TaskPacket with curated context, constraints, output schema
        """
        # Step 1: Select relevant context (heuristic)
        context = self._slice_context(global_state, worker, subtask)

        # Step 2: Load worker profile
        profile = self.worker_profiles.get_profile(worker)

        # Step 3: Assemble packet
        packet = TaskPacket(
            worker=worker,
            objective=objective,
            question=subtask,
            context=context,
            constraints=profile["constraints"],
            output_schema=profile["output_schema"],
            routing=profile.get("routing", {}),
        )

        logger.debug(
            f"Composed packet {packet.task_id} for {worker.value}: {subtask[:50]}..."
        )
        return packet

    def _slice_context(
        self,
        global_state: Dict[str, Any],
        worker: WorkerType,
        subtask: str,
    ) -> Dict[str, Any]:
        """
        Phase 1: Heuristic context slicing.

        Selects:
        - Last N relevant messages (filtered by simple rules)
        - All known facts (usually small)
        - Prior results (from previous agents)
        - Artifacts (code, documents created so far)

        TODO: Phase 2 upgrade to semantic search on message embeddings
        """
        messages = global_state.get("messages", [])
        facts = global_state.get("facts", [])
        prior_results = global_state.get("subagent_runs", [])
        artifacts = global_state.get("artifacts", [])

        # Simple heuristic: last N messages + all facts
        # In phase 2, filter by semantic relevance to subtask
        recent_dialogue = []
        for msg in messages[-10:]:  # Last 10 messages
            if isinstance(msg, dict):
                content = msg.get("content", "")
            else:
                content = str(msg)

            # Basic relevance check: is message about the subtask?
            if self._is_relevant(content, subtask):
                recent_dialogue.append(content)

        # Truncate to reasonable size
        recent_dialogue = recent_dialogue[-5:]  # Keep last 5 relevant

        return {
            "facts": facts,
            "recent_dialogue": recent_dialogue,
            "prior_results": prior_results[-3:] if prior_results else [],  # Last 3 results
            "artifacts": artifacts[-5:] if artifacts else [],  # Last 5 artifacts
        }

    @staticmethod
    def _is_relevant(message: str, subtask: str) -> bool:
        """
        Simple heuristic: is this message relevant to the subtask?

        Phase 1: Just check if keywords overlap.
        Phase 2: Use embeddings for semantic similarity.
        """
        subtask_words = set(subtask.lower().split()[:5])  # First 5 words
        message_words = set(message.lower().split()[:20])  # First 20 words

        # If 2+ keywords overlap, consider relevant
        overlap = subtask_words & message_words
        return len(overlap) >= 2

    @staticmethod
    def summarize_large_context(data: Any, max_chars: int = 500) -> str:
        """
        Summarize large context (like full DOM trees) into brief form.

        Used for CDP results, file contents, etc. Don't embed full data
        in context; embed summaries.
        """
        s = str(data)
        if len(s) > max_chars:
            return s[: max_chars - 3] + "..."
        return s
