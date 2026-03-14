"""
Tests for backend model path resolution.

Tests the smart fallback chain:
1. OLLAMA_DATA env var (if set) → {OLLAMA_DATA}/models
2. MODELS_PATH env var (if set)
3. model_paths list in config (first existing path)
4. models_path single path in config
5. Default: /mnt/storage/models

Run: pytest tests/test_backend_model_paths.py -v
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from beigebox.backends.base import BaseBackend


# ── Test Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def temp_model_dir():
    """Create a temporary directory for testing model paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_backend():
    """Create a concrete backend instance for testing."""
    class TestBackend(BaseBackend):
        async def forward(self, body: dict):
            return MagicMock()
        async def forward_stream(self, body: dict):
            return iter([])
        async def health_check(self) -> bool:
            return True
        async def list_models(self) -> list[str]:
            return []

    return TestBackend(name="test", url="http://localhost:8000")


# ── Tests ──────────────────────────────────────────────────────────────────

def test_model_path_default(mock_backend):
    """
    No env vars, no config → uses default /mnt/storage/models
    """
    # Clear env vars
    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", side_effect=Exception("No config")):
            # Reinitialize to trigger path resolution
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == "/mnt/storage/models"


def test_model_path_ollama_data_env(mock_backend, temp_model_dir):
    """
    OLLAMA_DATA env var set → uses {OLLAMA_DATA}/models (highest priority)
    """
    # Create models directory in temp location
    models_dir = temp_model_dir / "models"
    models_dir.mkdir()

    with patch.dict(os.environ, {"OLLAMA_DATA": str(temp_model_dir)}, clear=False):
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", side_effect=Exception("No config")):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(models_dir)


def test_model_path_models_path_env(mock_backend, temp_model_dir):
    """
    MODELS_PATH env var set → uses it (second priority)
    """
    with patch.dict(os.environ, {"MODELS_PATH": str(temp_model_dir)}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]

        with patch("beigebox.config.get_config", side_effect=Exception("No config")):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(temp_model_dir)


def test_model_path_model_paths_list(mock_backend, temp_model_dir):
    """
    model_paths list in config → uses first existing path (third priority)
    """
    # Create some paths
    path1 = temp_model_dir / "nonexistent"  # doesn't exist
    path2 = temp_model_dir / "exists"
    path2.mkdir()

    config = {
        "backend": {
            "model_paths": [str(path1), str(path2)]
        }
    }

    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            # Should use path2 (first existing)
            assert backend.models_path == str(path2)


def test_model_path_model_paths_fallback(mock_backend, temp_model_dir):
    """
    model_paths list where none exist → uses first in list (fallback)
    """
    path1 = temp_model_dir / "path1"
    path2 = temp_model_dir / "path2"

    config = {
        "backend": {
            "model_paths": [str(path1), str(path2)]
        }
    }

    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            # Even if none exist, use first (user's responsibility)
            assert backend.models_path == str(path1)


def test_model_path_single_models_path(mock_backend, temp_model_dir):
    """
    models_path single path in config → uses it (fourth priority)
    """
    config = {
        "backend": {
            "models_path": str(temp_model_dir)
        }
    }

    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(temp_model_dir)


def test_model_path_priority_order(mock_backend, temp_model_dir):
    """
    When multiple sources are set, correct priority is used:
    OLLAMA_DATA > MODELS_PATH > model_paths > models_path > default
    """
    # Create directories
    ollama_dir = temp_model_dir / "ollama" / "models"
    ollama_dir.mkdir(parents=True)
    models_env_dir = temp_model_dir / "models_env"
    models_env_dir.mkdir()
    config_list_dir = temp_model_dir / "config_list"
    config_list_dir.mkdir()
    config_single_dir = temp_model_dir / "config_single"
    config_single_dir.mkdir()

    config = {
        "backend": {
            "model_paths": [str(config_list_dir)],
            "models_path": str(config_single_dir),
        }
    }

    # OLLAMA_DATA has highest priority
    with patch.dict(
        os.environ,
        {
            "OLLAMA_DATA": str(temp_model_dir / "ollama"),
            "MODELS_PATH": str(models_env_dir),
        },
        clear=False,
    ):
        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(ollama_dir), "OLLAMA_DATA should win"

    # MODELS_PATH is second priority (remove OLLAMA_DATA)
    with patch.dict(
        os.environ,
        {
            "MODELS_PATH": str(models_env_dir),
        },
        clear=False,
    ):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(models_env_dir), "MODELS_PATH should win"

    # model_paths is third priority (remove env vars)
    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(config_list_dir), "model_paths should win"

    # models_path is fourth priority (remove model_paths from config)
    config_no_list = {
        "backend": {
            "models_path": str(config_single_dir),
        }
    }

    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config_no_list):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            assert backend.models_path == str(config_single_dir), "models_path should win"


def test_model_path_with_home_expansion(mock_backend):
    """
    Paths with ${HOME} or ~ are expanded correctly
    """
    # Note: config loading handles ${VAR} expansion, not the backend itself
    # But we should support relative paths gracefully
    config = {
        "backend": {
            "models_path": "~/models"
        }
    }

    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        with patch("beigebox.config.get_config", return_value=config):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            # Should return the path as-is (config loader handles expansion)
            assert backend.models_path == "~/models"


def test_model_path_config_error_fallback(mock_backend):
    """
    If config loading fails, fall back to defaults gracefully
    """
    with patch.dict(os.environ, {}, clear=False):
        if "OLLAMA_DATA" in os.environ:
            del os.environ["OLLAMA_DATA"]
        if "MODELS_PATH" in os.environ:
            del os.environ["MODELS_PATH"]

        # Config loading raises an exception
        with patch("beigebox.config.get_config", side_effect=RuntimeError("DB error")):
            backend = mock_backend.__class__(name="test", url="http://localhost:8000")
            # Should still return default
            assert backend.models_path == "/mnt/storage/models"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
