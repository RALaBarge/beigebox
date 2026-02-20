"""
Test model advertising / name transformation feature.
Tests both advertise and hidden modes.
"""

import pytest
from beigebox.proxy import Proxy
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.vector_store import VectorStore


@pytest.fixture
def mock_proxy(tmp_path):
    """Create a mock proxy instance for testing."""
    sqlite = SQLiteStore(str(tmp_path / "test.db"))
    vector = VectorStore(
        chroma_path=str(tmp_path / "chroma"),
        embedding_model="nomic-embed-text",
        embedding_url="http://localhost:11434"
    )
    proxy = Proxy(sqlite=sqlite, vector=vector)
    return proxy


def test_model_advertising_hidden_mode(mock_proxy):
    """Test that hidden mode doesn't modify model names."""
    # Set config to hidden mode
    mock_proxy.cfg["model_advertising"] = {
        "mode": "hidden",
        "prefix": "beigebox:"
    }
    
    # Sample response from Ollama
    response = {
        "data": [
            {"name": "llama3.2", "model": "llama3.2"},
            {"name": "gpt-oss-20b", "model": "gpt-oss-20b"},
            {"name": "claude", "model": "claude"}
        ]
    }
    
    result = mock_proxy._transform_model_names(response)
    
    # Should be unchanged
    assert result["data"][0]["name"] == "llama3.2"
    assert result["data"][1]["name"] == "gpt-oss-20b"
    assert result["data"][2]["name"] == "claude"


def test_model_advertising_advertise_mode(mock_proxy):
    """Test that advertise mode prepends prefix to model names."""
    # Set config to advertise mode
    mock_proxy.cfg["model_advertising"] = {
        "mode": "advertise",
        "prefix": "beigebox:"
    }
    
    response = {
        "data": [
            {"name": "llama3.2", "model": "llama3.2"},
            {"name": "gpt-oss-20b", "model": "gpt-oss-20b"},
            {"name": "claude", "model": "claude"}
        ]
    }
    
    result = mock_proxy._transform_model_names(response)
    
    # Should have prefix added
    assert result["data"][0]["name"] == "beigebox:llama3.2"
    assert result["data"][0]["model"] == "beigebox:llama3.2"
    assert result["data"][1]["name"] == "beigebox:gpt-oss-20b"
    assert result["data"][2]["name"] == "beigebox:claude"


def test_model_advertising_custom_prefix(mock_proxy):
    """Test that custom prefixes work."""
    mock_proxy.cfg["model_advertising"] = {
        "mode": "advertise",
        "prefix": "🔗 "  # emoji prefix
    }
    
    response = {
        "data": [
            {"name": "llama3.2", "model": "llama3.2"}
        ]
    }
    
    result = mock_proxy._transform_model_names(response)
    assert result["data"][0]["name"] == "🔗 llama3.2"


def test_model_advertising_defaults(mock_proxy):
    """Test default behavior (hidden mode) when config is missing."""
    # Don't set model_advertising config — should default to hidden
    mock_proxy.cfg.pop("model_advertising", None)
    
    response = {
        "data": [
            {"name": "llama3.2", "model": "llama3.2"}
        ]
    }
    
    result = mock_proxy._transform_model_names(response)
    # Should be unchanged (hidden mode is default)
    assert result["data"][0]["name"] == "llama3.2"


def test_model_advertising_malformed_response(mock_proxy):
    """Test that malformed responses are handled gracefully."""
    mock_proxy.cfg["model_advertising"] = {
        "mode": "advertise",
        "prefix": "beigebox:"
    }
    
    # Response with unexpected structure
    response = {"unexpected_key": "unexpected_value"}
    
    result = mock_proxy._transform_model_names(response)
    # Should return unchanged
    assert result == response


def test_model_advertising_missing_data_key(mock_proxy):
    """Test response that doesn't have 'data' key."""
    mock_proxy.cfg["model_advertising"] = {
        "mode": "advertise",
        "prefix": "beigebox:"
    }
    
    response = {
        "models": [  # Different key name
            {"name": "llama3.2"}
        ]
    }
    
    result = mock_proxy._transform_model_names(response)
    # Should return unchanged if no 'data' key
    assert result == response
