"""
Comprehensive tests for task packet orchestration.

Tests cover:
- TaskPacket creation, serialization, deserialization
- PacketComposer context slicing
- ResultValidator schema validation
- StateMerger state normalization
- Integration: compose → execute → validate → merge workflow
- Edge cases and error handling
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from beigebox.orchestration.packet import (
    TaskPacket,
    WorkerResult,
    WorkerType,
)
from beigebox.orchestration.composer import PacketComposer
from beigebox.orchestration.validator import ResultValidator
from beigebox.orchestration.merger import StateMerger


# ─────────────────────────────────────────────────────────────────────────────
# TaskPacket Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTaskPacket:
    """Test TaskPacket creation and serialization."""

    def test_packet_creation_minimal(self):
        """Packet can be created with minimal fields."""
        packet = TaskPacket(
            worker=WorkerType.RESEARCH,
            objective="Find the answer",
            question="What is the answer?",
        )
        assert packet.worker == WorkerType.RESEARCH
        assert packet.objective == "Find the answer"
        assert packet.question == "What is the answer?"
        assert packet.task_id is not None
        assert packet.created_at is not None

    def test_packet_to_dict(self):
        """Packet serializes to dict."""
        packet = TaskPacket(
            worker=WorkerType.CODER,
            objective="Write code",
            question="How do I write code?",
            context={"facts": ["fact1"]},
        )
        d = packet.to_dict()
        assert d["worker"] == "coder"
        assert d["objective"] == "Write code"
        assert d["context"]["facts"] == ["fact1"]

    def test_packet_from_dict(self):
        """Packet reconstructs from dict."""
        original = TaskPacket(
            worker=WorkerType.OPERATOR,
            objective="Execute task",
            question="Do the thing",
        )
        d = original.to_dict()
        restored = TaskPacket.from_dict(d)
        assert restored.task_id == original.task_id
        assert restored.worker == original.worker
        assert restored.objective == original.objective


class TestWorkerResult:
    """Test WorkerResult creation and serialization."""

    def test_result_creation_minimal(self):
        """Result can be created with minimal fields."""
        result = WorkerResult(
            status="success",
            answer="The answer is 42",
        )
        assert result.status == "success"
        assert result.answer == "The answer is 42"
        assert result.confidence == 0.5  # default

    def test_result_with_all_fields(self):
        """Result can be created with all fields."""
        result = WorkerResult(
            status="success",
            answer="Success!",
            confidence=0.95,
            evidence=["evidence1", "evidence2"],
            follow_up_needed=["follow up 1"],
            artifacts_created=["file.py"],
        )
        assert result.confidence == 0.95
        assert len(result.evidence) == 2
        assert result.artifacts_created == ["file.py"]

    def test_result_to_dict(self):
        """Result serializes to dict."""
        result = WorkerResult(
            status="needs_escalation",
            answer="Need help",
            confidence=0.3,
        )
        d = result.to_dict()
        assert d["status"] == "needs_escalation"
        assert d["confidence"] == 0.3


# ─────────────────────────────────────────────────────────────────────────────
# PacketComposer Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPacketComposer:
    """Test context composition and slicing."""

    @pytest.fixture
    def composer(self):
        return PacketComposer()

    @pytest.fixture
    def global_state(self):
        """Fixture: sample global state with history."""
        return {
            "messages": [
                {"content": "User: find the auth code"},
                {"content": "Assistant: searching..."},
                {"content": "User: found it in auth.py line 45"},
                {"content": "Assistant: ok noted"},
                {"content": "User: now check security"},
            ],
            "facts": [
                "Authentication is in auth.py",
                "User has repo access",
            ],
            "subagent_runs": [
                {"task_id": "run-1", "worker": "research", "result": "Found file"},
            ],
            "artifacts": ["auth_analysis.md"],
        }

    def test_composer_compose_basic(self, composer, global_state):
        """Composer creates packet from state."""
        packet = composer.compose(
            global_state,
            WorkerType.CODER,
            "Check security",
            "Review auth.py for vulnerabilities",
        )
        assert packet.worker == WorkerType.CODER
        assert packet.objective == "Check security"
        assert packet.question == "Review auth.py for vulnerabilities"
        assert len(packet.context["facts"]) > 0
        assert len(packet.constraints["must_do"]) > 0

    def test_composer_context_slicing_heuristic(self, composer, global_state):
        """Composer slices context with heuristic."""
        packet = composer.compose(
            global_state,
            WorkerType.RESEARCH,
            "Find docs",
            "Where are the design documents?",
        )
        context = packet.context

        # Should include facts
        assert "facts" in context
        assert len(context["facts"]) > 0

        # Should include recent dialogue (filtered)
        assert "recent_dialogue" in context
        # Shouldn't include all messages, only relevant ones
        assert len(context["recent_dialogue"]) <= 5

    def test_composer_context_excludes_unrelated(self, composer):
        """Composer excludes unrelated history."""
        state = {
            "messages": [
                {"content": "Talk about weather"},
                {"content": "More weather chat"},
                {"content": "Now: find the bug in code"},
                {"content": "The bug is in line 42"},
            ],
            "facts": [],
        }

        packet = composer.compose(
            state,
            WorkerType.CODER,
            "Debug",
            "Why does the code fail?",
        )

        # Should include relevant ("bug", "code", "fail")
        # Should exclude weather chat
        dialogue = packet.context["recent_dialogue"]
        dialogue_text = " ".join(dialogue).lower()
        assert "bug" in dialogue_text or "code" in dialogue_text or "fail" in dialogue_text

    def test_composer_worker_profile_loaded(self, composer, global_state):
        """Composer loads correct worker profile."""
        packet_research = composer.compose(
            global_state,
            WorkerType.RESEARCH,
            "Research",
            "Find info",
        )
        packet_coder = composer.compose(
            global_state,
            WorkerType.CODER,
            "Code",
            "Write code",
        )

        # Different workers have different constraints
        research_must_do = packet_research.constraints["must_do"]
        coder_must_do = packet_coder.constraints["must_do"]

        assert any("evidence" in s.lower() for s in research_must_do)
        assert any("code" in s.lower() for s in coder_must_do)

    def test_summarize_large_context(self):
        """Large context is summarized to avoid token bloat."""
        large_html = "<div>" * 1000
        summary = PacketComposer.summarize_large_context(large_html, max_chars=100)
        assert len(summary) <= 103  # 100 + "..."


# ─────────────────────────────────────────────────────────────────────────────
# ResultValidator Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResultValidator:
    """Test result validation."""

    @pytest.fixture
    def validator(self):
        return ResultValidator()

    @pytest.fixture
    def packet(self):
        return TaskPacket(
            worker=WorkerType.CODER,
            objective="Write code",
            question="How?",
        )

    def test_validator_accepts_valid_result(self, validator, packet):
        """Validator accepts valid result."""
        raw = {
            "status": "success",
            "answer": "Here's the code",
            "confidence": 0.95,
            "evidence": ["line 42"],
            "follow_up_needed": [],
            "artifacts_created": ["code.py"],
        }
        is_valid, result, errors = validator.validate(raw, packet)
        assert is_valid
        assert result.status == "success"
        assert result.confidence == 0.95
        assert len(errors) == 0

    def test_validator_rejects_missing_required_field(self, validator, packet):
        """Validator rejects missing required fields."""
        raw = {
            "status": "success",
            # Missing "answer" and "confidence"
        }
        is_valid, result, errors = validator.validate(raw, packet)
        assert not is_valid
        assert result is None
        assert len(errors) > 0

    def test_validator_rejects_invalid_status(self, validator, packet):
        """Validator rejects invalid status."""
        raw = {
            "status": "maybe",  # Invalid!
            "answer": "No",
            "confidence": 0.5,
        }
        is_valid, result, errors = validator.validate(raw, packet)
        assert not is_valid
        assert any("status" in e.lower() for e in errors)

    def test_validator_rejects_invalid_confidence(self, validator, packet):
        """Validator rejects confidence outside [0, 1]."""
        raw = {
            "status": "success",
            "answer": "Yes",
            "confidence": 1.5,  # Invalid! > 1
        }
        is_valid, result, errors = validator.validate(raw, packet)
        assert not is_valid
        assert any("confidence" in e.lower() for e in errors)

    def test_validator_parses_json_string(self, validator, packet):
        """Validator parses JSON string responses."""
        json_str = json.dumps({
            "status": "success",
            "answer": "Works!",
            "confidence": 0.8,
            "evidence": [],
            "follow_up_needed": [],
            "artifacts_created": [],
        })
        is_valid, result, errors = validator.validate(json_str, packet)
        assert is_valid
        assert result.answer == "Works!"

    def test_validator_rejects_invalid_json(self, validator, packet):
        """Validator rejects invalid JSON."""
        raw_json = "not valid json"
        is_valid, result, errors = validator.validate(raw_json, packet)
        assert not is_valid
        assert any("json" in e.lower() for e in errors)

    def test_validator_retry_prompt(self, validator, packet):
        """Validator can generate retry prompt."""
        invalid_raw = {"status": "invalid"}
        errors = ["Invalid status", "Missing answer"]

        retry_prompt = validator.build_retry_prompt(packet, invalid_raw, errors)
        assert "invalid" in retry_prompt.lower()
        assert "error" in retry_prompt.lower()
        assert "resubmit" in retry_prompt.lower()


# ─────────────────────────────────────────────────────────────────────────────
# StateMerger Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStateMerger:
    """Test state merging and normalization."""

    @pytest.fixture
    def merger(self):
        return StateMerger(confidence_threshold=0.7)

    @pytest.fixture
    def global_state(self):
        return {
            "execution_log": [],
            "facts": [],
            "artifacts": [],
            "backlog": [],
        }

    def test_merger_logs_execution(self, merger, global_state):
        """Merger logs all execution."""
        packet = TaskPacket(
            worker=WorkerType.RESEARCH,
            objective="Find it",
            question="Where?",
        )
        result = WorkerResult(
            status="success",
            answer="Found it!",
            confidence=0.5,
        )

        merger.merge(global_state, packet, result)

        assert len(global_state["execution_log"]) == 1
        log_entry = global_state["execution_log"][0]
        assert log_entry["task_id"] == packet.task_id
        assert log_entry["worker"] == "research"
        assert log_entry["status"] == "success"

    def test_merger_stores_high_confidence_facts(self, merger, global_state):
        """Merger stores high-confidence results as facts."""
        packet = TaskPacket(worker=WorkerType.RESEARCH, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="High confidence fact",
            confidence=0.95,  # >= 0.7 threshold
        )

        merger.merge(global_state, packet, result)

        assert "High confidence fact" in global_state["facts"]

    def test_merger_ignores_low_confidence_facts(self, merger, global_state):
        """Merger ignores low-confidence results."""
        packet = TaskPacket(worker=WorkerType.RESEARCH, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="Uncertain fact",
            confidence=0.3,  # < 0.7 threshold
        )

        merger.merge(global_state, packet, result)

        # Should be logged but NOT stored as fact
        assert len(global_state["execution_log"]) == 1
        assert "Uncertain fact" not in global_state["facts"]

    def test_merger_adds_follow_ups_to_backlog(self, merger, global_state):
        """Merger queues follow-up work."""
        packet = TaskPacket(worker=WorkerType.JUDGE, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="Done",
            confidence=0.9,
            follow_up_needed=["Check docs", "Verify code"],
        )

        merger.merge(global_state, packet, result)

        assert "Check docs" in global_state["backlog"]
        assert "Verify code" in global_state["backlog"]

    def test_merger_stores_artifacts(self, merger, global_state):
        """Merger stores generated artifacts."""
        packet = TaskPacket(worker=WorkerType.CODER, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="Code written",
            confidence=0.9,
            artifacts_created=["solution.py", "tests.py"],
        )

        merger.merge(global_state, packet, result)

        assert "solution.py" in global_state["artifacts"]
        assert "tests.py" in global_state["artifacts"]

    def test_merger_deduplicates_artifacts(self, merger, global_state):
        """Merger doesn't duplicate artifacts."""
        global_state["artifacts"] = ["existing.py"]

        packet = TaskPacket(worker=WorkerType.CODER, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="More code",
            confidence=0.9,
            artifacts_created=["existing.py", "new.py"],
        )

        merger.merge(global_state, packet, result)

        assert global_state["artifacts"].count("existing.py") == 1
        assert "new.py" in global_state["artifacts"]

    def test_merger_execution_summary(self, merger, global_state):
        """Merger can summarize execution state."""
        # Add some execution
        for i in range(3):
            packet = TaskPacket(worker=WorkerType.RESEARCH, objective="", question="")
            result = WorkerResult(
                status="success",
                answer=f"Fact {i}",
                confidence=0.8,
            )
            merger.merge(global_state, packet, result)

        summary = StateMerger.get_execution_summary(global_state)
        assert summary["total_tasks"] == 3
        assert summary["successful"] == 3
        assert summary["facts_stored"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestOrchestrationIntegration:
    """Test the full compose → validate → merge workflow."""

    def test_full_workflow_success(self):
        """Full workflow: compose → execute → validate → merge."""
        # Setup
        composer = PacketComposer()
        validator = ResultValidator()
        merger = StateMerger()

        global_state = {
            "messages": [{"content": "User: write code"}],
            "facts": [],
            "execution_log": [],
            "artifacts": [],
            "backlog": [],
        }

        # Step 1: Compose packet
        packet = composer.compose(
            global_state,
            WorkerType.CODER,
            "Write code",
            "Write a hello world function",
        )
        assert packet.question == "Write a hello world function"

        # Step 2: Simulate agent execution (mock)
        agent_response = {
            "status": "success",
            "answer": "def hello(): print('hello')",
            "confidence": 0.95,
            "evidence": ["standard python"],
            "follow_up_needed": [],
            "artifacts_created": ["hello.py"],
        }

        # Step 3: Validate
        is_valid, result, errors = validator.validate(agent_response, packet)
        assert is_valid
        assert result.confidence == 0.95

        # Step 4: Merge
        merger.merge(global_state, packet, result)

        # Verify state updated
        assert len(global_state["execution_log"]) == 1
        assert len(global_state["facts"]) == 1
        assert "def hello()" in global_state["facts"][0]  # Check substring
        assert "hello.py" in global_state["artifacts"]

    def test_workflow_with_invalid_response_retry(self):
        """Workflow handles invalid response with retry."""
        composer = PacketComposer()
        validator = ResultValidator()

        packet = TaskPacket(worker=WorkerType.RESEARCH, objective="", question="")

        # First response is invalid
        invalid_response = {"status": "invalid"}
        is_valid1, _, errors1 = validator.validate(invalid_response, packet)
        assert not is_valid1

        # Generate retry prompt
        retry_prompt = validator.build_retry_prompt(packet, invalid_response, errors1)
        assert "error" in retry_prompt.lower()

        # Second response is valid
        valid_response = {
            "status": "success",
            "answer": "Found it",
            "confidence": 0.8,
            "evidence": [],
            "follow_up_needed": [],
            "artifacts_created": [],
        }
        is_valid2, result2, errors2 = validator.validate(valid_response, packet)
        assert is_valid2
        assert result2.answer == "Found it"


# ─────────────────────────────────────────────────────────────────────────────
# Edge Cases and Error Handling
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_state_composition(self):
        """Composer handles empty state gracefully."""
        composer = PacketComposer()
        empty_state = {}

        packet = composer.compose(empty_state, WorkerType.OPERATOR, "Goal", "Task")
        assert packet.context["facts"] == []
        assert packet.context["recent_dialogue"] == []

    def test_very_long_state_composition(self):
        """Composer handles very long history gracefully."""
        composer = PacketComposer()

        # 1000 messages in history
        state = {
            "messages": [
                {"content": f"Turn {i}"}
                for i in range(1000)
            ],
            "facts": [],
        }

        packet = composer.compose(state, WorkerType.RESEARCH, "Goal", "Task")
        # Should not include all 1000 messages
        assert len(packet.context["recent_dialogue"]) < 1000
        assert len(packet.context["recent_dialogue"]) <= 5  # Limited to last 5 relevant

    def test_unicode_in_results(self):
        """Results handle unicode correctly."""
        merger = StateMerger()
        state = {"execution_log": [], "facts": [], "artifacts": [], "backlog": []}

        packet = TaskPacket(worker=WorkerType.RESEARCH, objective="", question="")
        result = WorkerResult(
            status="success",
            answer="Found: 你好世界 🌍 Привет мир",
            confidence=0.9,
        )

        merger.merge(state, packet, result)
        assert "你好世界" in state["facts"][0]
        assert "Привет" in state["facts"][0]
