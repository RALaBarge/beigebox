"""
Tests for orchestration tools: PlanManagerTool, ResearchAgentTool,
ParallelResearchTool, EvidenceSynthesisTool.

Covers unit tests (file I/O, validation), integration tests (mocked LLM calls),
and an E2E workflow test (decompose -> research -> synthesize -> plan update).
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws_out():
    """Temporary workspace/out/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "out"
        out.mkdir()
        yield out


@pytest.fixture
def plan_tool(ws_out):
    from beigebox.tools.plan_manager import PlanManagerTool
    return PlanManagerTool(workspace_out=ws_out)


@pytest.fixture
def research_tool(ws_out):
    from beigebox.tools.research_agent import ResearchAgentTool
    return ResearchAgentTool(workspace_out=ws_out)


@pytest.fixture
def parallel_tool(ws_out):
    from beigebox.tools.parallel_research import ParallelResearchTool
    return ParallelResearchTool(workspace_out=ws_out)


@pytest.fixture
def synthesis_tool(ws_out):
    from beigebox.tools.evidence_synthesis import EvidenceSynthesisTool
    return EvidenceSynthesisTool(workspace_out=ws_out)


# Mock LLM response that looks like real research output
_MOCK_RESEARCH_RESPONSE = json.dumps({
    "findings": "## Key Findings\n\nRAG poisoning involves injecting malicious content into retrieval corpora.\n\n1. **Data poisoning**: Attackers insert adversarial documents.\n2. **Prompt injection via retrieval**: Retrieved context contains hidden instructions.\n3. **Embedding manipulation**: Crafted inputs that cluster near target queries.",
    "sources": ["OWASP LLM Top 10 (2024)", "Greshake et al. 2023 - Indirect Prompt Injection"],
    "confidence": 0.82,
    "next_questions": ["What defenses exist against embedding manipulation?", "How to detect poisoned documents at ingest time?"]
})

_MOCK_SYNTHESIS_RESPONSE = json.dumps({
    "synthesis": "## Cross-Cutting Analysis\n\nBoth RAG poisoning and MCP injection share a common pattern: trust boundary violations in the data plane.",
    "patterns": [
        "Trust boundary violations in data ingestion pipelines",
        "Indirect prompt injection as a recurring attack vector",
        "Defense-in-depth approaches needed at multiple layers"
    ],
    "recommendations": [
        "Implement input sanitization at retrieval boundaries",
        "Add anomaly detection on embedding similarity scores",
        "Deploy canary documents to detect poisoning attempts"
    ],
    "confidence": 0.75,
    "contradictions": ["Some sources claim embedding-level defenses are sufficient while others argue for content-level filtering"],
    "evidence_gaps": ["Real-world attack frequency data is lacking", "No benchmarks for defense effectiveness"]
})


# ---------------------------------------------------------------------------
# PlanManagerTool — Unit Tests
# ---------------------------------------------------------------------------

class TestPlanManagerUnit:

    @pytest.mark.unit
    def test_create_plan(self, plan_tool, ws_out):
        result = plan_tool.run('{"action":"create","content":"# My Plan\\n- Step 1"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["action"] == "create"
        assert (ws_out / "plan.md").exists()
        assert "# My Plan" in (ws_out / "plan.md").read_text()

    @pytest.mark.unit
    def test_read_empty_plan(self, plan_tool):
        result = plan_tool.run('{"action":"read"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["content"] == ""
        assert "No plan exists" in parsed.get("message", "")

    @pytest.mark.unit
    def test_read_existing_plan(self, plan_tool, ws_out):
        (ws_out / "plan.md").write_text("# Existing Plan\n- Step 1\n")
        result = plan_tool.run('{"action":"read"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert "Existing Plan" in parsed["content"]
        assert parsed["modified_at"] is not None

    @pytest.mark.unit
    def test_update_plan(self, plan_tool, ws_out):
        plan_tool.run('{"action":"create","content":"# V1"}')
        result = plan_tool.run('{"action":"update","content":"# V2\\nUpdated."}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert "V2" in (ws_out / "plan.md").read_text()
        assert "V1" not in (ws_out / "plan.md").read_text()

    @pytest.mark.unit
    def test_append_plan(self, plan_tool, ws_out):
        plan_tool.run('{"action":"create","content":"# Plan\\n- Step 1"}')
        result = plan_tool.run('{"action":"append","content":"\\n- Step 2"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        content = (ws_out / "plan.md").read_text()
        assert "Step 1" in content
        assert "Step 2" in content

    @pytest.mark.unit
    def test_append_to_nonexistent(self, plan_tool, ws_out):
        """Append to a plan that doesn't exist yet — should create it."""
        result = plan_tool.run('{"action":"append","content":"# New Plan"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert (ws_out / "plan.md").exists()

    @pytest.mark.unit
    def test_invalid_json(self, plan_tool):
        result = plan_tool.run("not json")
        assert "Error" in result

    @pytest.mark.unit
    def test_missing_content_create(self, plan_tool):
        result = plan_tool.run('{"action":"create"}')
        assert "Error" in result

    @pytest.mark.unit
    def test_unknown_action(self, plan_tool):
        result = plan_tool.run('{"action":"delete"}')
        assert "Error" in result

    @pytest.mark.unit
    def test_infer_action_from_content(self, plan_tool, ws_out):
        """If action is omitted but content is present, infer create/update."""
        result = plan_tool.run('{"content":"# Auto Plan"}')
        parsed = json.loads(result)
        assert parsed["status"] == "success"
        assert parsed["action"] == "create"

    @pytest.mark.unit
    def test_infer_read_when_no_fields(self, plan_tool):
        """If no action and no content, infer read."""
        result = plan_tool.run('{}')
        parsed = json.loads(result)
        assert parsed["action"] == "read"


# ---------------------------------------------------------------------------
# ResearchAgentTool — Unit + Integration Tests
# ---------------------------------------------------------------------------

class TestResearchAgentUnit:

    @pytest.mark.unit
    def test_invalid_json(self, research_tool):
        result = research_tool.run("not json")
        assert "Error" in result

    @pytest.mark.unit
    def test_missing_topic(self, research_tool):
        result = research_tool.run('{"research_questions":["What?"]}')
        assert "Error" in result

    @pytest.mark.unit
    def test_missing_questions(self, research_tool):
        result = research_tool.run('{"topic":"AI safety"}')
        assert "Error" in result

    @pytest.mark.unit
    def test_empty_questions(self, research_tool):
        result = research_tool.run('{"topic":"AI safety","research_questions":[]}')
        assert "Error" in result

    @pytest.mark.unit
    def test_parse_findings_valid_json(self, research_tool):
        raw = json.dumps({"findings": "test", "sources": ["s1"], "confidence": 0.9, "next_questions": []})
        result = research_tool._parse_findings(raw, "test topic")
        assert result["topic"] == "test topic"
        assert result["confidence"] == 0.9
        assert result["findings"] == "test"

    @pytest.mark.unit
    def test_parse_findings_plain_text(self, research_tool):
        """When LLM returns plain text instead of JSON, fallback works."""
        result = research_tool._parse_findings("Just some plain text findings.", "topic")
        assert result["findings"] == "Just some plain text findings."
        assert result["confidence"] == 0.3

    @pytest.mark.unit
    def test_parse_findings_markdown_fenced(self, research_tool):
        """Strip markdown code fences around JSON."""
        raw = '```json\n{"findings":"test","sources":[],"confidence":0.8,"next_questions":[]}\n```'
        result = research_tool._parse_findings(raw, "topic")
        assert result["confidence"] == 0.8


class TestResearchAgentIntegration:

    @pytest.mark.integration
    def test_research_with_mock_llm(self, research_tool, ws_out):
        with patch.object(research_tool, '_chat', return_value=_MOCK_RESEARCH_RESPONSE):
            result = research_tool.run(json.dumps({
                "topic": "RAG poisoning attacks",
                "research_questions": ["What are the main attack vectors?"],
                "depth": "medium",
            }))

        parsed = json.loads(result)
        assert parsed["topic"] == "RAG poisoning attacks"
        assert parsed["confidence"] == 0.82
        assert len(parsed["sources"]) == 2
        assert "execution_time" in parsed
        assert "saved_to" in parsed

        # Verify file was saved
        saved = Path(parsed["saved_to"])
        assert saved.exists()
        content = saved.read_text()
        assert "RAG poisoning" in content

    @pytest.mark.integration
    def test_research_depth_quick(self, research_tool):
        """Quick depth should still work (fewer turns)."""
        with patch.object(research_tool, '_chat', return_value=_MOCK_RESEARCH_RESPONSE):
            result = research_tool.run(json.dumps({
                "topic": "Quick topic",
                "research_questions": ["Overview?"],
                "depth": "quick",
            }))
        parsed = json.loads(result)
        assert parsed["status"] if "status" in parsed else parsed["confidence"] > 0

    @pytest.mark.integration
    def test_research_llm_failure(self, research_tool):
        """Graceful degradation when LLM is unreachable."""
        with patch.object(research_tool, '_chat', side_effect=Exception("Connection refused")):
            result = research_tool.run(json.dumps({
                "topic": "Failing topic",
                "research_questions": ["Will this fail?"],
            }))
        parsed = json.loads(result)
        assert parsed["confidence"] == 0.0
        assert "failed" in parsed["findings"].lower() or "error" in parsed.get("error", "").lower()

    @pytest.mark.integration
    def test_research_async_execute(self, research_tool):
        """Test the async execute() path."""
        with patch.object(research_tool, '_execute_research', return_value={
            "topic": "async test",
            "findings": "async works",
            "sources": [],
            "confidence": 0.9,
            "next_questions": [],
        }):
            result = asyncio.run(research_tool.execute(
                topic="async test",
                research_questions=["Does async work?"],
            ))
        assert result["topic"] == "async test"


# ---------------------------------------------------------------------------
# ParallelResearchTool — Integration Tests
# ---------------------------------------------------------------------------

class TestParallelResearchIntegration:

    @pytest.mark.integration
    def test_invalid_json(self, parallel_tool):
        result = parallel_tool.run("not json")
        assert "Error" in result

    @pytest.mark.integration
    def test_empty_tasks(self, parallel_tool):
        result = parallel_tool.run('{"tasks":[]}')
        assert "Error" in result

    @pytest.mark.integration
    def test_missing_topic_in_task(self, parallel_tool):
        result = parallel_tool.run('{"tasks":[{"research_questions":["what?"]}]}')
        assert "Error" in result

    @pytest.mark.integration
    def test_parallel_with_mock_llm(self, parallel_tool, ws_out):
        mock_result = {
            "topic": "test",
            "findings": "test findings",
            "sources": ["src1"],
            "confidence": 0.8,
            "next_questions": [],
        }

        async def mock_execute(topic, research_questions, max_turns=10, depth="medium"):
            return {**mock_result, "topic": topic}

        with patch.object(parallel_tool._research_tool, 'execute', side_effect=mock_execute):
            result = parallel_tool.run(json.dumps({
                "tasks": [
                    {"topic": "Topic A", "research_questions": ["Q1?"]},
                    {"topic": "Topic B", "research_questions": ["Q2?"]},
                ],
                "max_workers": 2,
            }))

        parsed = json.loads(result)
        assert parsed["status"] == "complete"
        assert parsed["completed"] == 2
        assert parsed["failed"] == 0
        assert "Topic A" in parsed["results"]
        assert "Topic B" in parsed["results"]
        assert parsed["execution_time"] >= 0

        # Verify aggregate file was saved
        agg_file = ws_out / "parallel_research_results.md"
        assert agg_file.exists()

    @pytest.mark.integration
    def test_parallel_partial_failure(self, parallel_tool):
        """One task fails, others succeed — partial_failure status."""
        call_count = 0

        async def mock_execute(topic, research_questions, max_turns=10, depth="medium"):
            nonlocal call_count
            call_count += 1
            if topic == "Failing":
                raise Exception("Simulated failure")
            return {
                "topic": topic,
                "findings": "ok",
                "sources": [],
                "confidence": 0.7,
                "next_questions": [],
            }

        with patch.object(parallel_tool._research_tool, 'execute', side_effect=mock_execute):
            result = parallel_tool.run(json.dumps({
                "tasks": [
                    {"topic": "Good", "research_questions": ["Q?"]},
                    {"topic": "Failing", "research_questions": ["Q?"]},
                ],
            }))

        parsed = json.loads(result)
        assert parsed["status"] == "partial_failure"
        assert parsed["completed"] == 1
        assert parsed["failed"] == 1
        assert "Failing" in parsed["errors"]

    @pytest.mark.integration
    def test_max_workers_clamped(self, parallel_tool):
        """max_workers should be clamped to [1, 8]."""
        # This just validates the clamping doesn't crash — no LLM calls
        with patch.object(parallel_tool._research_tool, 'execute',
                          return_value={"topic": "t", "findings": "", "sources": [], "confidence": 0.5, "next_questions": []}) as mock:
            # Wrap as async
            async def async_mock(*args, **kwargs):
                return mock.return_value
            mock.side_effect = async_mock

            result = parallel_tool.run(json.dumps({
                "tasks": [{"topic": "T", "research_questions": ["Q?"]}],
                "max_workers": 100,  # Should be clamped to 8
            }))
        parsed = json.loads(result)
        assert parsed["status"] == "complete"


# ---------------------------------------------------------------------------
# EvidenceSynthesisTool — Integration Tests
# ---------------------------------------------------------------------------

class TestEvidenceSynthesisIntegration:

    @pytest.mark.integration
    def test_invalid_json(self, synthesis_tool):
        result = synthesis_tool.run("not json")
        assert "Error" in result

    @pytest.mark.integration
    def test_missing_findings(self, synthesis_tool):
        result = synthesis_tool.run('{"synthesis_question":"What?"}')
        assert "Error" in result

    @pytest.mark.integration
    def test_missing_question(self, synthesis_tool):
        result = synthesis_tool.run('{"findings_list":[{"topic":"t","findings":"f","sources":[]}]}')
        assert "Error" in result

    @pytest.mark.integration
    def test_synthesis_with_mock_llm(self, synthesis_tool, ws_out):
        with patch.object(synthesis_tool, '_chat', return_value=_MOCK_SYNTHESIS_RESPONSE):
            result = synthesis_tool.run(json.dumps({
                "findings_list": [
                    {"topic": "RAG poisoning", "findings": "Attack vectors include...", "sources": ["OWASP"]},
                    {"topic": "MCP injection", "findings": "MCP protocol allows...", "sources": ["Paper X"]},
                ],
                "synthesis_question": "What are the key security recommendations?",
                "output_format": "analysis",
            }))

        parsed = json.loads(result)
        assert parsed["confidence"] == 0.75
        assert len(parsed["patterns"]) == 3
        assert len(parsed["recommendations"]) == 3
        assert len(parsed["contradictions"]) == 1
        assert len(parsed["evidence_gaps"]) == 2
        assert "execution_time" in parsed
        assert "saved_to" in parsed

        # Verify file was saved
        saved = Path(parsed["saved_to"])
        assert saved.exists()
        content = saved.read_text()
        assert "Evidence Synthesis" in content

    @pytest.mark.integration
    def test_synthesis_summary_format(self, synthesis_tool):
        with patch.object(synthesis_tool, '_chat', return_value=_MOCK_SYNTHESIS_RESPONSE):
            result = synthesis_tool.run(json.dumps({
                "findings_list": [{"topic": "T", "findings": "F", "sources": []}],
                "synthesis_question": "Summary?",
                "output_format": "summary",
            }))
        parsed = json.loads(result)
        assert parsed["confidence"] > 0

    @pytest.mark.integration
    def test_synthesis_llm_failure(self, synthesis_tool):
        with patch.object(synthesis_tool, '_chat', side_effect=Exception("Connection refused")):
            result = synthesis_tool.run(json.dumps({
                "findings_list": [{"topic": "T", "findings": "F", "sources": []}],
                "synthesis_question": "What?",
            }))
        parsed = json.loads(result)
        assert parsed["confidence"] == 0.0
        assert "failed" in parsed["synthesis"].lower() or "error" in parsed.get("error", "").lower()

    @pytest.mark.integration
    def test_synthesis_plain_text_fallback(self, synthesis_tool):
        """When LLM returns prose instead of JSON, fallback works."""
        with patch.object(synthesis_tool, '_chat', return_value="Just some analysis text."):
            result = synthesis_tool.run(json.dumps({
                "findings_list": [{"topic": "T", "findings": "F", "sources": []}],
                "synthesis_question": "Analyze?",
            }))
        parsed = json.loads(result)
        assert parsed["synthesis"] == "Just some analysis text."
        assert parsed["confidence"] == 0.3


# ---------------------------------------------------------------------------
# E2E Workflow Test
# ---------------------------------------------------------------------------

class TestOrchestrationE2E:

    @pytest.mark.e2e
    def test_full_orchestration_workflow(self, ws_out):
        """
        Simulate the full Operator orchestration flow:
        1. Create plan (decompose task)
        2. Run parallel research on subtopics
        3. Synthesize findings
        4. Update plan with results
        """
        from beigebox.tools.plan_manager import PlanManagerTool
        from beigebox.tools.research_agent import ResearchAgentTool
        from beigebox.tools.parallel_research import ParallelResearchTool
        from beigebox.tools.evidence_synthesis import EvidenceSynthesisTool

        plan = PlanManagerTool(workspace_out=ws_out)
        research = ResearchAgentTool(workspace_out=ws_out)
        parallel = ParallelResearchTool(workspace_out=ws_out)
        synthesis = EvidenceSynthesisTool(workspace_out=ws_out)

        # Step 1: Create research plan
        r1 = plan.run(json.dumps({
            "action": "create",
            "content": (
                "# AI Security Research Plan\n\n"
                "## Phase 1: Parallel Research\n"
                "- [ ] RAG poisoning attack vectors\n"
                "- [ ] MCP injection patterns\n\n"
                "## Phase 2: Synthesis\n"
                "- [ ] Cross-topic analysis\n"
                "- [ ] Strategic recommendations\n"
            ),
        }))
        assert json.loads(r1)["status"] == "success"

        # Step 2: Run parallel research (mocked)
        mock_result_a = {
            "topic": "RAG poisoning",
            "findings": "RAG poisoning attacks involve injecting adversarial documents.",
            "sources": ["OWASP LLM Top 10"],
            "confidence": 0.8,
            "next_questions": [],
        }
        mock_result_b = {
            "topic": "MCP injection",
            "findings": "MCP protocol can be exploited via crafted tool responses.",
            "sources": ["MCP Security Audit 2025"],
            "confidence": 0.75,
            "next_questions": [],
        }

        async def mock_execute(topic, research_questions, max_turns=10, depth="medium"):
            if "RAG" in topic:
                return mock_result_a
            return mock_result_b

        with patch.object(parallel._research_tool, 'execute', side_effect=mock_execute):
            r2 = parallel.run(json.dumps({
                "tasks": [
                    {"topic": "RAG poisoning", "research_questions": ["Attack vectors?"]},
                    {"topic": "MCP injection", "research_questions": ["Injection patterns?"]},
                ],
                "max_workers": 2,
            }))
        parallel_result = json.loads(r2)
        assert parallel_result["status"] == "complete"
        assert parallel_result["completed"] == 2

        # Step 3: Synthesize findings
        findings_for_synthesis = [
            mock_result_a,
            mock_result_b,
        ]
        with patch.object(synthesis, '_chat', return_value=_MOCK_SYNTHESIS_RESPONSE):
            r3 = synthesis.run(json.dumps({
                "findings_list": findings_for_synthesis,
                "synthesis_question": "What are the key AI security recommendations?",
                "output_format": "recommendations",
            }))
        synth_result = json.loads(r3)
        assert synth_result["confidence"] > 0
        assert len(synth_result["recommendations"]) > 0

        # Step 4: Update plan with results
        r4 = plan.run(json.dumps({
            "action": "update",
            "content": (
                "# AI Security Research Plan\n\n"
                "## Phase 1: Parallel Research [COMPLETE]\n"
                "- [x] RAG poisoning attack vectors (confidence: 80%)\n"
                "- [x] MCP injection patterns (confidence: 75%)\n\n"
                "## Phase 2: Synthesis [COMPLETE]\n"
                "- [x] Cross-topic analysis\n"
                "- [x] Strategic recommendations (3 generated)\n\n"
                "## Key Findings\n"
                "- Trust boundary violations are the common thread\n"
                "- Defense-in-depth needed at multiple layers\n"
            ),
        }))
        assert json.loads(r4)["status"] == "success"

        # Verify final plan content
        r5 = plan.run('{"action":"read"}')
        final_plan = json.loads(r5)
        assert "COMPLETE" in final_plan["content"]
        assert "Trust boundary" in final_plan["content"]

        # Verify all workspace artifacts exist
        assert (ws_out / "plan.md").exists()
        assert (ws_out / "parallel_research_results.md").exists()
        assert (ws_out / "synthesis_result.md").exists()
