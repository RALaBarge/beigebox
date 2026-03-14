"""
Tests for operator model configuration and runtime config changes.

Covers:
  - operator_model in runtime config allowed keys
  - GET /api/v1/config returns operator model (respecting runtime override)
  - POST /api/v1/config accepts operator_model param
  - Operator class reads runtime config model
  - Fallback chain: override → runtime → static config → default_model
  - HarnessOrchestrator reads runtime config model
"""
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


class TestOperatorModelRuntimeConfig:
    """Test operator model in runtime config read/write."""

    def test_update_operator_model_creates_runtime_key(self, tmp_path):
        """update_runtime_config should create operator_model if missing."""
        from beigebox import config as cfg_mod

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")

        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0

        try:
            result = cfg_mod.update_runtime_config("operator_model", "qwen3:7b")
            assert result is True
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["operator_model"] == "qwen3:7b"
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_operator_model_persists_across_reads(self, tmp_path):
        """operator_model should be persistent in runtime_config.yaml."""
        from beigebox import config as cfg_mod

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: llama3.2:7b\n")

        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            rt = cfg_mod.get_runtime_config()
            assert rt.get("operator_model") == "llama3.2:7b"
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_operator_model_preserved_on_other_updates(self, tmp_path):
        """Updating other keys shouldn't lose operator_model."""
        from beigebox import config as cfg_mod

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: custom_model\n  web_ui_vi_mode: false\n")

        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0

        try:
            cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["operator_model"] == "custom_model"
            assert data["runtime"]["web_ui_vi_mode"] is True
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Endpoint Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client_with_operator(tmp_path):
    """Create a test client with operator config."""
    pytest.importorskip("chromadb", reason="chromadb not installed")
    from fastapi.testclient import TestClient
    from beigebox import config as cfg_mod

    cfg_data = {
        "backend": {"url": "http://localhost:11434", "default_model": "llama2", "timeout": 120},
        "server": {"host": "0.0.0.0", "port": 8000},
        "embedding": {"model": "nomic-embed-text", "backend_url": "http://localhost:11434"},
        "storage": {"sqlite_path": str(tmp_path / "test.db"), "chroma_path": str(tmp_path / "chroma"), "log_conversations": True},
        "tools": {"enabled": False},
        "decision_llm": {"enabled": False},
        "hooks": {"directory": str(tmp_path / "hooks")},
        "logging": {"level": "WARNING"},
        "wiretap": {"path": str(tmp_path / "wire.jsonl")},
        "routing": {},
        "model_advertising": {"mode": "hidden"},
        "operator": {
            "enabled": True,
            "model": "static_operator_model",  # Static config model
            "max_iterations": 10,
            "shell": {"enabled": False},
        },
        "backends_enabled": False,
        "cost_tracking": {"enabled": False},
        "wasm": {"enabled": False, "modules": {}},
    }

    rt_path = tmp_path / "runtime_config.yaml"
    rt_path.write_text("runtime:\n  operator_enabled: true\n")  # No operator_model yet

    orig_config = cfg_mod._config
    orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
    orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
    orig_rt_config = cfg_mod._runtime_config

    cfg_mod._config = cfg_data
    cfg_mod._RUNTIME_CONFIG_PATH = rt_path
    cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
    cfg_mod._runtime_config = {}

    import beigebox.main

    mock_preload = AsyncMock(return_value=None)
    mock_da = MagicMock()
    mock_da.from_config.return_value = MagicMock(enabled=False, model="")
    mock_da.from_config.return_value.preload = AsyncMock(return_value=None)

    mock_ec = MagicMock()
    mock_ec.ready = True

    mock_wasm = MagicMock()
    mock_wasm.enabled = False
    mock_wasm.list_modules.return_value = []
    mock_wasm.default_module = ""
    mock_proxy = MagicMock()
    mock_proxy.wasm_runtime = mock_wasm

    with patch("beigebox.main.SQLiteStore"), \
         patch("beigebox.main.VectorStore"), \
         patch("beigebox.main.ToolRegistry") as MockTR, \
         patch("beigebox.main.DecisionAgent", mock_da), \
         patch("beigebox.main.HookManager") as MockHM, \
         patch("beigebox.main.get_embedding_classifier", return_value=mock_ec), \
         patch("beigebox.main.Proxy", return_value=mock_proxy), \
         patch("beigebox.main._preload_embedding_model", mock_preload):

        MockTR.return_value.list_tools.return_value = []
        MockHM.return_value.list_hooks.return_value = []

        import beigebox.main as main_mod
        main_mod.proxy = mock_proxy

        from beigebox.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, rt_path, cfg_mod

        main_mod.proxy = None

    cfg_mod._config = orig_config
    cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
    cfg_mod._runtime_mtime = orig_rt_mtime
    cfg_mod._runtime_mtime_last_checked = orig_rt_mtime_checked
    cfg_mod._runtime_config = orig_rt_config


class TestConfigEndpointOperatorModel:
    """Test /api/v1/config operator model handling."""

    def test_config_returns_operator_model_from_static_config(self, client_with_operator):
        """GET /api/v1/config should include operator.model from static config."""
        c, _, _ = client_with_operator
        r = c.get("/api/v1/config")
        assert r.status_code == 200
        data = r.json()
        assert "operator" in data
        assert data["operator"]["model"] == "static_operator_model"

    def test_config_returns_operator_model_from_runtime_config(self, client_with_operator):
        """GET /api/v1/config should return runtime config operator_model if set."""
        c, rt_path, cfg_mod = client_with_operator
        # Set runtime config
        rt_path.write_text("runtime:\n  operator_model: runtime_override_model\n")
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        r = c.get("/api/v1/config")
        assert r.status_code == 200
        data = r.json()
        assert data["operator"]["model"] == "runtime_override_model"

    def test_config_runtime_model_takes_precedence(self, client_with_operator):
        """Runtime config operator_model should override static config."""
        c, rt_path, cfg_mod = client_with_operator
        rt_path.write_text("runtime:\n  operator_model: runtime_wins\n")
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        r = c.get("/api/v1/config")
        data = r.json()
        assert data["operator"]["model"] == "runtime_wins"
        assert data["operator"]["model"] != "static_operator_model"


class TestConfigPostOperatorModel:
    """Test POST /api/v1/config with operator_model parameter."""

    def test_post_config_accepts_operator_model(self, client_with_operator):
        """POST /api/v1/config should accept operator_model param."""
        c, rt_path, cfg_mod = client_with_operator
        r = c.post("/api/v1/config", json={"operator_model": "new_model"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "operator_model" in data.get("saved", [])

    def test_post_config_operator_model_persists(self, client_with_operator):
        """operator_model set via POST should persist to runtime_config.yaml."""
        c, rt_path, cfg_mod = client_with_operator
        c.post("/api/v1/config", json={"operator_model": "persisted_model"})

        rt_data = yaml.safe_load(rt_path.read_text())
        assert rt_data["runtime"]["operator_model"] == "persisted_model"

    def test_post_config_operator_model_reflected_in_get(self, client_with_operator):
        """After POST, GET /api/v1/config should return new operator_model."""
        c, rt_path, cfg_mod = client_with_operator
        c.post("/api/v1/config", json={"operator_model": "new_post_model"})

        # Force cache invalidation
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        r = c.get("/api/v1/config")
        assert r.json()["operator"]["model"] == "new_post_model"

    def test_post_config_multiple_keys_including_operator(self, client_with_operator):
        """POST should handle multiple keys including operator_model."""
        c, rt_path, cfg_mod = client_with_operator
        r = c.post("/api/v1/config", json={
            "operator_model": "multi_key_model",
            "web_ui_vi_mode": True,
        })
        assert r.status_code == 200
        saved = r.json().get("saved", [])
        assert "operator_model" in saved
        assert "web_ui_vi_mode" in saved


# ─────────────────────────────────────────────────────────────────────────────
# Operator Class Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOperatorModelResolution:
    """Test Operator.__init__ model resolution chain."""

    def test_operator_uses_override_model(self, tmp_path):
        """Operator should use model_override param first."""
        from beigebox import config as cfg_mod
        from beigebox.agents.operator import Operator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"default_model": "default_model"},
            "workspace": {"path": "./workspace"},
        }

        orig_cfg = cfg_mod._config
        cfg_mod._config = cfg_data

        try:
            op = Operator(model_override="override_model")
            assert op._model == "override_model"
        finally:
            cfg_mod._config = orig_cfg

    def test_operator_uses_runtime_config_model(self, tmp_path):
        """Operator should use runtime_config operator_model second."""
        from beigebox import config as cfg_mod
        from beigebox.agents.operator import Operator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"default_model": "default_model"},
            "workspace": {"path": "./workspace"},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: runtime_model\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            op = Operator()
            assert op._model == "runtime_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config

    def test_operator_uses_static_config_model_fallback(self, tmp_path):
        """Operator should use static config model if no runtime override."""
        from beigebox import config as cfg_mod
        from beigebox.agents.operator import Operator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"default_model": "default_model"},
            "workspace": {"path": "./workspace"},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            op = Operator()
            assert op._model == "static_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config

    def test_operator_uses_default_model_final_fallback(self, tmp_path):
        """Operator should fall back to backend.default_model if nothing else set."""
        from beigebox import config as cfg_mod
        from beigebox.agents.operator import Operator

        cfg_data = {
            "operator": {},  # Empty operator config
            "backend": {"default_model": "default_model"},
            "workspace": {"path": "./workspace"},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            op = Operator()
            assert op._model == "default_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config

    def test_operator_model_chain_priority(self, tmp_path):
        """Test full priority chain: override > runtime > static > default."""
        from beigebox import config as cfg_mod
        from beigebox.agents.operator import Operator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"default_model": "default_model"},
            "workspace": {"path": "./workspace"},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: runtime_model\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            # Override wins
            op1 = Operator(model_override="override_model")
            assert op1._model == "override_model"

            # Runtime wins when no override
            op2 = Operator()
            assert op2._model == "runtime_model"

            # Static wins when no runtime
            rt_path.write_text("runtime:\n")
            cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
            cfg_mod._runtime_config = {}
            op3 = Operator()
            assert op3._model == "static_model"

            # Default wins when nothing else set
            cfg_data["operator"] = {}
            cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
            cfg_mod._runtime_config = {}
            op4 = Operator()
            assert op4._model == "default_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config


# ─────────────────────────────────────────────────────────────────────────────
# HarnessOrchestrator Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHarnessOrchestratorModelResolution:
    """Test HarnessOrchestrator model resolution chain."""

    def test_harness_uses_runtime_config_model(self, tmp_path):
        """HarnessOrchestrator should use runtime_config operator_model."""
        from beigebox import config as cfg_mod
        from beigebox.agents.harness_orchestrator import HarnessOrchestrator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"url": "http://localhost:11434", "default_model": "default_model"},
            "harness": {"retry": {}, "stagger": {}, "timeouts": {}},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: harness_runtime_model\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            harness = HarnessOrchestrator()
            assert harness.model == "harness_runtime_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config

    def test_harness_respects_model_param_override(self, tmp_path):
        """HarnessOrchestrator model param should take priority."""
        from beigebox import config as cfg_mod
        from beigebox.agents.harness_orchestrator import HarnessOrchestrator

        cfg_data = {
            "operator": {"model": "static_model"},
            "backend": {"url": "http://localhost:11434", "default_model": "default_model"},
            "harness": {"retry": {}, "stagger": {}, "timeouts": {}},
        }

        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  operator_model: runtime_model\n")

        orig_cfg = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_mtime = cfg_mod._runtime_mtime; orig_rt_mtime_checked = cfg_mod._runtime_mtime_last_checked
        orig_rt_config = cfg_mod._runtime_config

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_mtime_last_checked = 0.0
        cfg_mod._runtime_config = {}

        try:
            harness = HarnessOrchestrator(model="param_override_model")
            assert harness.model == "param_override_model"
        finally:
            cfg_mod._config = orig_cfg
            cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
            cfg_mod._runtime_mtime = orig_rt_mtime
            cfg_mod._runtime_config = orig_rt_config
