"""
StateMerger: Normalize validated results into durable global state.

The merger is where state hygiene matters. It:
- Logs all execution for audit and replay
- Separates provisional from accepted facts (confidence-based)
- Manages backlog of follow-up work
- Stores artifacts without duplication

This is where multi-agent composition stays sane or becomes sludge.
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from datetime import datetime

from beigebox.orchestration.packet import TaskPacket, WorkerResult

logger = logging.getLogger(__name__)


class StateMerger:
    """
    Merge validated agent results into global state cleanly.

    Principles:
    - Log everything (audit trail, replay capability)
    - Separate provisional from accepted facts
    - Only store high-confidence results as facts
    - Track follow-up work in backlog
    - Avoid state bloat (archive old runs if needed)
    """

    def __init__(self, confidence_threshold: float = 0.7):
        """
        Initialize merger.

        Args:
            confidence_threshold: Only store facts with confidence >= this
        """
        self.confidence_threshold = confidence_threshold

    def merge(
        self,
        global_state: Dict[str, Any],
        packet: TaskPacket,
        result: WorkerResult,
    ) -> None:
        """
        Merge validated result into global state.

        Args:
            global_state: Session state to update (modified in place)
            packet: Original TaskPacket
            result: Validated WorkerResult
        """
        # Step 1: Log execution (always, regardless of confidence)
        self._log_execution(global_state, packet, result)

        # Step 2: Update facts (only if success and high confidence)
        if result.status == "success" and result.confidence >= self.confidence_threshold:
            self._store_fact(global_state, result)

        # Step 3: Queue follow-ups
        if result.follow_up_needed:
            self._add_to_backlog(global_state, result.follow_up_needed)

        # Step 4: Store artifacts
        if result.artifacts_created:
            self._store_artifacts(global_state, result.artifacts_created)

        logger.debug(
            f"Merged result {packet.task_id}: status={result.status}, "
            f"stored_as_fact={result.confidence >= self.confidence_threshold}"
        )

    @staticmethod
    def _log_execution(
        global_state: Dict[str, Any],
        packet: TaskPacket,
        result: WorkerResult,
    ) -> None:
        """
        Log execution for audit trail, debugging, and replay.

        This is ALWAYS done, regardless of confidence or validity.
        """
        global_state.setdefault("execution_log", []).append({
            "timestamp": datetime.now().isoformat(),
            "task_id": packet.task_id,
            "worker": packet.worker.value,
            "objective": packet.objective,
            "question": packet.question,
            "status": result.status,
            "confidence": result.confidence,
            "answer_preview": result.answer[:100] if result.answer else "",
            "artifacts_count": len(result.artifacts_created),
        })

    @staticmethod
    def _store_fact(global_state: Dict[str, Any], result: WorkerResult) -> None:
        """
        Store high-confidence result as a durable fact.

        Facts are ground truth that subsequent agents can rely on.
        Only store if confidence >= threshold.
        """
        global_state.setdefault("facts", []).append(result.answer)
        logger.debug(f"Stored fact: {result.answer[:50]}...")

    @staticmethod
    def _add_to_backlog(global_state: Dict[str, Any], follow_ups: list[str]) -> None:
        """
        Queue follow-up tasks for supervisor to handle.

        These become new TaskPackets in the next orchestration cycle.
        """
        global_state.setdefault("backlog", []).extend(follow_ups)
        logger.debug(f"Added {len(follow_ups)} follow-ups to backlog")

    @staticmethod
    def _store_artifacts(
        global_state: Dict[str, Any],
        artifacts: list[str],
    ) -> None:
        """
        Store generated artifacts (code, documents, etc.).

        Avoid duplicates: only store if not already present.
        """
        existing = set(global_state.get("artifacts", []))
        new_artifacts = [a for a in artifacts if a not in existing]

        if new_artifacts:
            global_state.setdefault("artifacts", []).extend(new_artifacts)
            logger.debug(f"Stored {len(new_artifacts)} artifacts")

    @staticmethod
    def get_execution_summary(global_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a summary of execution state for diagnostics.

        Useful for understanding what happened so far in a session.
        """
        execution_log = global_state.get("execution_log", [])
        return {
            "total_tasks": len(execution_log),
            "successful": sum(1 for e in execution_log if e["status"] == "success"),
            "blocked": sum(1 for e in execution_log if e["status"] == "blocked"),
            "needs_escalation": sum(
                1 for e in execution_log if e["status"] == "needs_escalation"
            ),
            "avg_confidence": (
                sum(e["confidence"] for e in execution_log) / len(execution_log)
                if execution_log
                else 0
            ),
            "facts_stored": len(global_state.get("facts", [])),
            "artifacts_created": len(global_state.get("artifacts", [])),
            "backlog_size": len(global_state.get("backlog", [])),
        }
