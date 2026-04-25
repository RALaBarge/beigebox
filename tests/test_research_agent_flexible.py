"""
Tests for ResearchAgentFlexibleTool — backend-agnostic research agent.

Covers:
  - Backend spec parsing
  - Adapter construction and selection
  - Auth handling (env vars, config)
  - Fallback on unavailable backends
  - Research loop with mocked adapters
  - Structured output consistency across backends
  - Integration: multiple backends produce identical output structure
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from beigebox.tools.research_agent_flexible import (
    ADAPTER_REGISTRY,
    AnthropicAdapter,
    BackendAdapter,
    BeigeBoxRouterAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    ResearchAgentFlexibleTool,
    build_adapter,
    parse_backend_spec,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

_FAKE_CONFIG = {
    "backend": {"url": "http://localhost:11434", "default_model": "test-model"},
    "models": {"default": "test-model", "profiles": {"agentic": "test-agentic"}},
    "embedding": {},
    "operator": {"timeout": 60},
}

_GOOD_RESEARCH_JSON = json.dumps({
    "findings": "Test findings about RAG poisoning.",
    "sources": ["source-1", "source-2"],
    "confidence": 0.85,
    "next_questions": ["What about defense?"],
})


@pytest.fixture
def tmp_out(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


@pytest.fixture
def mock_config():
    with patch("beigebox.tools.research_agent_flexible.get_config", return_value=_FAKE_CONFIG):
        with patch("beigebox.tools.research_agent_flexible.get_runtime_config", return_value={}):
            yield


# ═══════════════════════════════════════════════════════════════════════════
#  1. Backend spec parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestParseBackendSpec:
    def test_openrouter_with_model(self):
        assert parse_backend_spec("openrouter:arcee/trinity") == ("openrouter", "arcee/trinity")

    def test_ollama_with_model(self):
        assert parse_backend_spec("ollama:neural-chat") == ("ollama", "neural-chat")

    def test_openai_with_model(self):
        assert parse_backend_spec("openai:gpt-4") == ("openai", "gpt-4")

    def test_anthropic_with_model(self):
        assert parse_backend_spec("anthropic:claude-opus") == ("anthropic", "claude-opus")

    def test_empty_string(self):
        assert parse_backend_spec("") == ("", "")

    def test_model_only_no_provider(self):
        """A bare model name (no recognized provider prefix) returns empty provider."""
        assert parse_backend_spec("neural-chat") == ("", "neural-chat")

    def test_unknown_provider_treated_as_model(self):
        """Unknown provider prefix is not split — entire string is model."""
        assert parse_backend_spec("foobar:some-model") == ("", "foobar:some-model")

    def test_ollama_model_with_tag(self):
        """Ollama tags contain colons (e.g. qwen3:32b). Only first colon splits."""
        assert parse_backend_spec("ollama:qwen3:32b") == ("ollama", "qwen3:32b")


# ═══════════════════════════════════════════════════════════════════════════
#  2. Adapter construction
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildAdapter:
    def test_ollama_adapter(self):
        adapter = build_adapter("ollama")
        assert isinstance(adapter, OllamaAdapter)

    def test_openrouter_adapter(self):
        adapter = build_adapter("openrouter")
        assert isinstance(adapter, OpenRouterAdapter)

    def test_openai_adapter(self):
        adapter = build_adapter("openai")
        assert isinstance(adapter, OpenAIAdapter)

    def test_anthropic_adapter(self):
        adapter = build_adapter("anthropic")
        assert isinstance(adapter, AnthropicAdapter)

    def test_beigebox_adapter(self):
        router = MagicMock()
        adapter = build_adapter("beigebox", router=router)
        assert isinstance(adapter, BeigeBoxRouterAdapter)

    def test_unknown_provider_falls_back_to_beigebox(self):
        adapter = build_adapter("unknown_provider")
        assert isinstance(adapter, BeigeBoxRouterAdapter)

    def test_registry_has_all_providers(self):
        expected = {"ollama", "openrouter", "openai", "anthropic", "beigebox"}
        assert set(ADAPTER_REGISTRY.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════════
#  3. Auth handling
# ═══════════════════════════════════════════════════════════════════════════

class TestAuthHandling:
    def test_openrouter_uses_env_key(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-or-key"}):
            adapter = OpenRouterAdapter()
            assert adapter.api_key == "test-or-key"

    def test_openrouter_explicit_key_overrides_env(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key"}):
            adapter = OpenRouterAdapter(api_key="explicit-key")
            assert adapter.api_key == "explicit-key"

    def test_openai_uses_env_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-oai-key"}):
            adapter = OpenAIAdapter()
            assert adapter.api_key == "test-oai-key"

    def test_anthropic_uses_env_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-ant-key"}):
            adapter = AnthropicAdapter()
            assert adapter.api_key == "test-ant-key"

    def test_anthropic_health_false_without_key(self):
        adapter = AnthropicAdapter(api_key="")
        result = asyncio.get_event_loop().run_until_complete(adapter.health())
        assert result is False

    def test_anthropic_health_true_with_key(self):
        adapter = AnthropicAdapter(api_key="some-key")
        result = asyncio.get_event_loop().run_until_complete(adapter.health())
        assert result is True

    def test_openrouter_health_false_without_key(self):
        # Clear env var so the adapter's env-fallback doesn't pick up a real key
        # from the developer shell.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            adapter = OpenRouterAdapter(api_key="")
            result = asyncio.get_event_loop().run_until_complete(adapter.health())
            assert result is False

    def test_openai_health_false_without_key(self):
        adapter = OpenAIAdapter(api_key="")
        result = asyncio.get_event_loop().run_until_complete(adapter.health())
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════
#  4. Adapter health + fallback
# ═══════════════════════════════════════════════════════════════════════════

class TestAdapterFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_adapter_unhealthy(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)

        # Make openrouter adapter unhealthy
        with patch.object(OpenRouterAdapter, "health", new_callable=AsyncMock, return_value=False):
            adapter, model = await tool._try_adapter_with_fallback("openrouter:some-model")
            # Should fall back to OllamaAdapter
            assert isinstance(adapter, OllamaAdapter)
            assert model == "test-agentic"  # default from config

    @pytest.mark.asyncio
    async def test_uses_requested_adapter_when_healthy(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)

        with patch.object(OpenRouterAdapter, "health", new_callable=AsyncMock, return_value=True):
            adapter, model = await tool._try_adapter_with_fallback("openrouter:arcee/trinity")
            assert isinstance(adapter, OpenRouterAdapter)
            assert model == "arcee/trinity"

    @pytest.mark.asyncio
    async def test_empty_spec_uses_default(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)

        with patch.object(OllamaAdapter, "health", new_callable=AsyncMock, return_value=True):
            adapter, model = await tool._try_adapter_with_fallback("")
            assert isinstance(adapter, OllamaAdapter)
            assert model == "test-agentic"


# ═══════════════════════════════════════════════════════════════════════════
#  5. Research loop with mocked backends
# ═══════════════════════════════════════════════════════════════════════════

def _make_mock_adapter(response_text: str = _GOOD_RESEARCH_JSON, healthy: bool = True):
    """Create a mock adapter that returns canned responses."""
    adapter = AsyncMock(spec=BackendAdapter)
    adapter.provider = "mock"
    adapter.chat = AsyncMock(return_value=response_text)
    adapter.health = AsyncMock(return_value=healthy)
    return adapter


class TestResearchLoop:
    @pytest.mark.asyncio
    async def test_basic_research_returns_structured_output(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            result = await tool.execute(
                topic="RAG poisoning",
                research_questions=["What are the vectors?"],
                backend="ollama:test-model",
            )

        assert result["topic"] == "RAG poisoning"
        assert "findings" in result
        assert "sources" in result
        assert "confidence" in result
        assert "next_questions" in result
        assert "backend_used" in result
        assert "saved_to" in result
        assert result["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_research_saves_markdown_file(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            result = await tool.execute(
                topic="test topic",
                research_questions=["Q1"],
                backend="ollama:test-model",
            )

        saved = Path(result["saved_to"])
        assert saved.exists()
        content = saved.read_text()
        assert "# Research: test topic" in content
        assert "Backend: mock:test-model" in content

    @pytest.mark.asyncio
    async def test_research_iterates_on_low_confidence(self, tmp_out, mock_config):
        """Low-confidence first response triggers follow-up turns."""
        low_conf = json.dumps({
            "findings": "Partial findings.",
            "sources": [],
            "confidence": 0.3,
            "next_questions": ["Need more info?"],
        })
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()
        # First call returns low confidence, second returns high
        mock_adapter.chat = AsyncMock(side_effect=[low_conf, _GOOD_RESEARCH_JSON])

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            result = await tool.execute(
                topic="deep topic",
                research_questions=["Q1"],
                max_turns=10,
                depth="medium",
                backend="ollama:test-model",
            )

        # Should have called chat twice (iterative refinement)
        assert mock_adapter.chat.call_count == 2
        assert result["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_research_handles_llm_failure(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()
        mock_adapter.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            result = await tool.execute(
                topic="failing topic",
                research_questions=["Q1"],
                backend="ollama:test-model",
            )

        assert result["confidence"] == 0.0
        assert "failed" in result["findings"].lower()

    @pytest.mark.asyncio
    async def test_quick_depth_fewer_turns(self, tmp_out, mock_config):
        """Quick depth should use 0.5x multiplier = fewer turns."""
        low_conf = json.dumps({
            "findings": "Quick.", "sources": [], "confidence": 0.2,
            "next_questions": ["More?"],
        })
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()
        # Return low confidence every time — we count how many calls
        mock_adapter.chat = AsyncMock(return_value=low_conf)

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            await tool.execute(
                topic="quick topic",
                research_questions=["Q1"],
                max_turns=10,
                depth="quick",
                backend="ollama:test-model",
            )

        # quick: 10 * 0.5 = 5 effective turns
        assert mock_adapter.chat.call_count == 5


# ═══════════════════════════════════════════════════════════════════════════
#  6. run() synchronous interface
# ═══════════════════════════════════════════════════════════════════════════

class TestRunInterface:
    def test_run_invalid_json(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        result = tool.run("not json")
        assert "Error" in result

    def test_run_missing_topic(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        result = tool.run('{"research_questions":["Q1"]}')
        assert "topic" in result.lower()

    def test_run_missing_questions(self, tmp_out, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        result = tool.run('{"topic":"test"}')
        assert "research_questions" in result.lower()

    def test_run_invalid_depth_defaults_to_medium(self, tmp_out, mock_config):
        """Invalid depth value should silently default to medium."""
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            result_str = tool.run(json.dumps({
                "topic": "test",
                "research_questions": ["Q1"],
                "depth": "invalid_depth",
            }))

        result = json.loads(result_str)
        assert result["topic"] == "test"
        assert "execution_time" in result


# ═══════════════════════════════════════════════════════════════════════════
#  7. Integration: consistent output across backends
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossBackendConsistency:
    """Verify the output structure is identical regardless of which backend is used."""

    _REQUIRED_KEYS = {"topic", "findings", "sources", "confidence", "next_questions", "backend_used", "saved_to"}

    @pytest.mark.asyncio
    async def _run_with_backend(self, tmp_out, backend_spec: str, mock_config):
        tool = ResearchAgentFlexibleTool(workspace_out=tmp_out)
        mock_adapter = _make_mock_adapter()

        with patch.object(tool, "_try_adapter_with_fallback",
                          new_callable=AsyncMock,
                          return_value=(mock_adapter, "test-model")):
            return await tool.execute(
                topic="Cross-backend test",
                research_questions=["Is the output consistent?"],
                backend=backend_spec,
            )

    @pytest.mark.asyncio
    async def test_ollama_output_structure(self, tmp_out, mock_config):
        result = await self._run_with_backend(tmp_out, "ollama:test-model", mock_config)
        assert self._REQUIRED_KEYS.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_openrouter_output_structure(self, tmp_out, mock_config):
        result = await self._run_with_backend(tmp_out, "openrouter:arcee/trinity", mock_config)
        assert self._REQUIRED_KEYS.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_openai_output_structure(self, tmp_out, mock_config):
        result = await self._run_with_backend(tmp_out, "openai:gpt-4", mock_config)
        assert self._REQUIRED_KEYS.issubset(result.keys())

    @pytest.mark.asyncio
    async def test_anthropic_output_structure(self, tmp_out, mock_config):
        result = await self._run_with_backend(tmp_out, "anthropic:claude-opus", mock_config)
        assert self._REQUIRED_KEYS.issubset(result.keys())


# ═══════════════════════════════════════════════════════════════════════════
#  8. Anthropic adapter format translation
# ═══════════════════════════════════════════════════════════════════════════

class TestAnthropicFormatTranslation:
    """Anthropic uses a different API shape — verify the adapter translates correctly."""

    @pytest.mark.asyncio
    async def test_system_message_extracted(self):
        """System messages should be pulled out of messages list and sent as 'system' field."""
        adapter = AnthropicAdapter(api_key="test-key")

        captured_payload = {}

        async def mock_post(url, headers=None, json=None):
            captured_payload.update(json or {})
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={
                "content": [{"type": "text", "text": "response"}],
            })
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = mock_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await adapter.chat(
                messages=[
                    {"role": "system", "content": "You are a researcher."},
                    {"role": "user", "content": "Tell me about RAG."},
                ],
                model="claude-opus",
            )

        assert result == "response"
        assert "system" in captured_payload
        assert "You are a researcher." in captured_payload["system"]
        # Verify system message was removed from messages list
        for msg in captured_payload["messages"]:
            assert msg["role"] != "system"


# ═══════════════════════════════════════════════════════════════════════════
#  9. BeigeBox router adapter
# ═══════════════════════════════════════════════════════════════════════════

class TestBeigeBoxRouterAdapter:
    @pytest.mark.asyncio
    async def test_forwards_to_router(self):
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = "Router response"
        mock_router.forward = AsyncMock(return_value=mock_response)

        adapter = BeigeBoxRouterAdapter(router=mock_router, force_backend="ollama-local")
        result = await adapter.chat(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )

        assert result == "Router response"
        call_body = mock_router.forward.call_args[0][0]
        assert call_body["_bb_force_backend"] == "ollama-local"

    @pytest.mark.asyncio
    async def test_raises_on_router_error(self):
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.error = "Backend down"
        mock_router.forward = AsyncMock(return_value=mock_response)

        adapter = BeigeBoxRouterAdapter(router=mock_router)
        with pytest.raises(RuntimeError, match="Router returned error"):
            await adapter.chat(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )

    @pytest.mark.asyncio
    async def test_raises_without_router(self):
        adapter = BeigeBoxRouterAdapter(router=None)
        with pytest.raises(RuntimeError, match="router not available"):
            await adapter.chat(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )

    @pytest.mark.asyncio
    async def test_health_true_with_router(self):
        adapter = BeigeBoxRouterAdapter(router=MagicMock())
        assert await adapter.health() is True

    @pytest.mark.asyncio
    async def test_health_false_without_router(self):
        adapter = BeigeBoxRouterAdapter(router=None)
        assert await adapter.health() is False


# ═══════════════════════════════════════════════════════════════════════════
#  10. Findings parsing edge cases
# ═══════════════════════════════════════════════════════════════════════════

class TestFindingsParsing:
    def _parse(self, raw, topic="test"):
        tool = ResearchAgentFlexibleTool.__new__(ResearchAgentFlexibleTool)
        return tool._parse_findings(raw, topic)

    def test_valid_json(self):
        result = self._parse(_GOOD_RESEARCH_JSON)
        assert result["confidence"] == 0.85
        assert len(result["sources"]) == 2

    def test_markdown_fenced_json(self):
        fenced = f"```json\n{_GOOD_RESEARCH_JSON}\n```"
        result = self._parse(fenced)
        assert result["confidence"] == 0.85

    def test_plain_text_fallback(self):
        result = self._parse("Just some plain text findings.")
        assert result["confidence"] == 0.3
        assert result["findings"] == "Just some plain text findings."
        assert result["sources"] == []

    def test_empty_string(self):
        result = self._parse("")
        assert result["confidence"] == 0.3
