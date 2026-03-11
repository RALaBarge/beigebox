"""
Shared fixtures for all tests.

Provides:
- Mock configurations
- Temporary directories
- Database instances
- Common utilities
"""

import tempfile
import pytest
from pathlib import Path


@pytest.fixture
def tmp_db_dir():
    """Temporary directory for database files"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def tmp_workspace():
    """Temporary workspace directory (in/, out/)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        (workspace / "in").mkdir()
        (workspace / "out").mkdir()
        yield workspace


@pytest.fixture
def fake_config():
    """Minimal valid config for testing"""
    return {
        "server": {"host": "127.0.0.1", "port": 8001, "log_level": "WARNING"},
        "backend": {"url": "http://localhost:11434", "default_model": "test-model", "timeout": 30},
        "storage": {
            "type": "sqlite",
            "path": ":memory:",
            "vector_store_path": ":memory:",
            "vector_backend": "chromadb",
        },
        "embedding": {"model": "nomic-embed-text", "backend": "ollama"},
        "operator": {
            "enabled": True,
            "model": "test-model",
            "max_iterations": 5,
            "timeout": 60,
            "allowed_tools": [],
            "autonomous": {"enabled": False, "max_turns": 5},
        },
        "workspace": {"path": "./workspace", "max_mb": 0},
        "wiretap": {"enabled": False},
        "decision_llm": {"enabled": False},
        "tools": {"enabled": True, "registry": []},
    }


@pytest.fixture
def mock_operator_config(fake_config):
    """Config with operator enabled"""
    config = fake_config.copy()
    config["operator"]["enabled"] = True
    return config


@pytest.fixture
def mock_operator_autonomous_config(fake_config):
    """Config with operator autonomous mode enabled"""
    config = fake_config.copy()
    config["operator"]["enabled"] = True
    config["operator"]["autonomous"]["enabled"] = True
    config["operator"]["autonomous"]["max_turns"] = 3
    return config


# ── Markers ──────────────────────────────────────────────────────────────

def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line("markers", "e2e: end-to-end flow tests")
    config.addinivalue_line("markers", "integration: component integration tests")
    config.addinivalue_line("markers", "unit: unit tests (fast)")
    config.addinivalue_line("markers", "error: error scenario tests")
    config.addinivalue_line("markers", "regression: regression tests")
    config.addinivalue_line("markers", "slow: slow test (>1 second)")
    config.addinivalue_line("markers", "benchmark: performance benchmark")
