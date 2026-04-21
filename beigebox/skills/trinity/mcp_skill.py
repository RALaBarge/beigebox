"""
Trinity MCP Skill - Expose Trinity audit as MCP endpoint with async polling.

Returns audit_id immediately, user polls for results.
"""

import asyncio
import json
from typing import Dict, Any, Optional
from pathlib import Path

from .pipeline import TrinityPipeline


class TrinityMCPSkill:
    """MCP-compatible Trinity audit skill."""

    def __init__(self):
        self.in_progress_audits: Dict[str, TrinityPipeline] = {}
        self.completed_audits: Dict[str, Dict] = {}

    async def start_audit(
        self,
        repo_path: str,
        models: Optional[Dict[str, str]] = None,
        budget: Optional[Dict[str, int]] = None,
        beigebox_url: str = "http://localhost:8000",
    ) -> Dict[str, Any]:
        """
        Start a Trinity audit asynchronously.

        Returns immediately with audit_id. User polls for results via get_audit_status.

        Args:
            repo_path: Path to code repository
            models: Dict of {surface, deep, specialist, appellate} model keys
            budget: Dict of token budgets per stack
            beigebox_url: URL to BeigeBbox instance

        Returns:
            {"audit_id": "trinity-...", "status": "queued"}
        """
        # Validate repo_path
        repo = Path(repo_path)
        if not repo.exists():
            raise ValueError(f"Repository not found: {repo_path}")

        # Create pipeline
        pipeline = TrinityPipeline(
            repo_path=repo_path,
            models=models,
            budget=budget,
            beigebox_url=beigebox_url,
        )

        audit_id = pipeline.audit_id
        self.in_progress_audits[audit_id] = pipeline

        # Start audit in background
        asyncio.create_task(self._run_audit_background(audit_id))

        return {
            "audit_id": audit_id,
            "status": "queued",
            "message": f"Audit {audit_id} queued. Poll for results.",
        }

    async def _run_audit_background(self, audit_id: str) -> None:
        """Run audit in background, move result to completed when done."""
        pipeline = self.in_progress_audits[audit_id]

        try:
            result = await pipeline.run_full_audit()
            self.completed_audits[audit_id] = result
        except Exception as e:
            self.completed_audits[audit_id] = {
                "audit_id": audit_id,
                "status": "failed",
                "error": str(e),
            }
        finally:
            # Clean up in-progress
            if audit_id in self.in_progress_audits:
                del self.in_progress_audits[audit_id]

    async def get_audit_status(self, audit_id: str) -> Dict[str, Any]:
        """
        Check status of an audit.

        Returns:
            - In progress: {"status": "running", "phase": "Phase 2: Consensus Building", ...}
            - Completed: {"status": "complete", "findings": [...], ...}
            - Not found: {"status": "not_found", "error": "..."}
        """
        # Check completed audits first (includes failed)
        if audit_id in self.completed_audits:
            return self.completed_audits[audit_id]

        # Check in-progress audits
        if audit_id in self.in_progress_audits:
            pipeline = self.in_progress_audits[audit_id]
            return {
                "audit_id": audit_id,
                "status": "running",
                "phase_1_findings": sum(
                    len(f) for f in pipeline.phase_1_results.values()
                ),
                "phase_2_consensus": len(pipeline.phase_2_consensus),
                "phase_3_appellate": len(pipeline.phase_3_appellate),
                "phase_4_verified": len(pipeline.phase_4_verified),
                "audit_log_entries": len(pipeline.audit_log),
            }

        return {
            "audit_id": audit_id,
            "status": "not_found",
            "error": f"Audit {audit_id} not found",
        }

    async def get_audit_result(self, audit_id: str) -> Dict[str, Any]:
        """
        Get full result of completed audit.

        Raises ValueError if audit not found or still running.
        """
        if audit_id in self.completed_audits:
            return self.completed_audits[audit_id]

        if audit_id in self.in_progress_audits:
            raise ValueError(f"Audit {audit_id} is still running")

        raise ValueError(f"Audit {audit_id} not found")

    async def list_audits(self) -> Dict[str, Any]:
        """List all audits (in-progress and completed)."""
        return {
            "in_progress": list(self.in_progress_audits.keys()),
            "completed": list(self.completed_audits.keys()),
            "total": len(self.in_progress_audits) + len(self.completed_audits),
        }


# Global singleton
_trinity_skill = None


def get_trinity_skill() -> TrinityMCPSkill:
    """Get global Trinity skill instance."""
    global _trinity_skill
    if _trinity_skill is None:
        _trinity_skill = TrinityMCPSkill()
    return _trinity_skill
