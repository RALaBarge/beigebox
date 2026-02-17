"""
Tests for the decision agent.
Tests parsing and fallback logic without requiring a live LLM.
"""

import pytest
import json
from beigebox.agents.decision import DecisionAgent, Decision


def test_disabled_agent_returns_default():
    """Disabled agent always returns fallback decision."""
    agent = DecisionAgent()
    assert agent.enabled is False


def test_parse_clean_json():
    """Agent parses clean JSON response."""
    agent = DecisionAgent(
        model="test",
        backend_url="http://localhost:11434",
        routes={
            "default": {"model": "mistral-nemo:12b"},
            "code": {"model": "qwen2.5-coder:14b"},
        },
        default_model="mistral-nemo:12b",
    )

    text = json.dumps({
        "model": "code",
        "needs_search": False,
        "needs_rag": False,
        "tools": [],
        "reasoning": "This is a coding question",
    })

    decision = agent._parse_response(text)
    assert decision.model == "qwen2.5-coder:14b"
    assert decision.needs_search is False
    assert decision.reasoning == "This is a coding question"


def test_parse_json_with_fences():
    """Agent handles markdown-fenced JSON."""
    agent = DecisionAgent(
        model="test",
        backend_url="http://localhost:11434",
        routes={"default": {"model": "test-model"}},
        default_model="test-model",
    )

    text = '```json\n{"model": "default", "needs_search": true, "needs_rag": false, "tools": [], "reasoning": "test"}\n```'

    decision = agent._parse_response(text)
    assert decision.needs_search is True
    assert decision.model == "test-model"


def test_resolve_model_from_route():
    """Route names resolve to model strings."""
    agent = DecisionAgent(
        model="test",
        backend_url="http://localhost:11434",
        routes={
            "code": {"model": "qwen2.5-coder:14b"},
            "large": {"model": "qwen3:32b"},
        },
        default_model="default-model",
    )

    assert agent._resolve_model("code") == "qwen2.5-coder:14b"
    assert agent._resolve_model("large") == "qwen3:32b"
    assert agent._resolve_model("unknown") == "default-model"
    # Direct model strings pass through
    assert agent._resolve_model("custom:7b") == "custom:7b"


def test_tools_filtered_to_available():
    """Only available tools are included in decision."""
    agent = DecisionAgent(
        model="test",
        backend_url="http://localhost:11434",
        available_tools=["web_search"],
        default_model="test",
    )

    text = json.dumps({
        "model": "default",
        "needs_search": False,
        "needs_rag": False,
        "tools": ["web_search", "nonexistent_tool"],
        "reasoning": "test",
    })

    decision = agent._parse_response(text)
    assert decision.tools == ["web_search"]


def test_parse_malformed_json_raises():
    """Malformed JSON raises (caught by decide() which returns fallback)."""
    agent = DecisionAgent(
        model="test",
        backend_url="http://localhost:11434",
        default_model="test",
    )

    with pytest.raises(json.JSONDecodeError):
        agent._parse_response("not json at all")
