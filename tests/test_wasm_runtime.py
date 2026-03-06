"""
Tests for WasmRuntime: reload(), default_module property, disabled-path behaviour,
and the /api/v1/wasm/reload endpoint.

Most tests work without wasmtime installed — we just test the control flow.
Tests that actually execute transforms are skipped when wasmtime is absent or
when no compiled .wasm files are available.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


# ── WasmRuntime unit tests ────────────────────────────────────────────────────

class TestWasmRuntimeDisabled:
    """Behaviour when wasm.enabled=false (no wasmtime init needed)."""

    def _make_runtime(self, extra_cfg=None):
        from beigebox.wasm_runtime import WasmRuntime
        cfg = {"wasm": {"enabled": False, "modules": {}, **(extra_cfg or {})}}
        return WasmRuntime(cfg)

    def test_not_enabled(self):
        rt = self._make_runtime()
        assert rt.enabled is False

    def test_list_modules_empty_when_disabled(self):
        rt = self._make_runtime()
        assert rt.list_modules() == []

    def test_default_module_property_get(self):
        rt = self._make_runtime({"default_module": "opener_strip"})
        assert rt.default_module == "opener_strip"

    def test_default_module_property_set(self):
        rt = self._make_runtime()
        rt.default_module = "pii_redactor"
        assert rt.default_module == "pii_redactor"

    def test_default_module_set_empty_string(self):
        rt = self._make_runtime({"default_module": "opener_strip"})
        rt.default_module = ""
        assert rt.default_module == ""

    @pytest.mark.asyncio
    async def test_transform_text_passthrough_when_disabled(self):
        rt = self._make_runtime()
        result = await rt.transform_text("opener_strip", "Hello world")
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_transform_response_passthrough_when_disabled(self):
        rt = self._make_runtime()
        data = {"choices": [{"message": {"content": "test"}}]}
        result = await rt.transform_response("opener_strip", data)
        assert result == data

    def test_reload_when_disabled_returns_empty(self):
        rt = self._make_runtime()
        with patch("beigebox.wasm_runtime.WasmRuntime._load_modules") as mock_load:
            loaded = rt.reload()
        assert loaded == []
        # engine is None so _load_modules should not be called
        mock_load.assert_not_called()

    def test_reload_clears_loaded_dict(self):
        rt = self._make_runtime()
        # Manually inject a fake loaded entry
        rt._loaded["fake_module"] = object()
        with patch("beigebox.config.get_config", return_value={"wasm": {"enabled": False, "modules": {}}}):
            rt.reload()
        assert rt._loaded == {}

    def test_reload_updates_default_module_from_config(self):
        rt = self._make_runtime()
        new_cfg = {"wasm": {"enabled": False, "modules": {}, "default_module": "markdown_stripper"}}
        with patch("beigebox.config.get_config", return_value=new_cfg):
            rt.reload()
        assert rt.default_module == "markdown_stripper"

    def test_reload_returns_list_of_loaded_names(self):
        rt = self._make_runtime()
        with patch("beigebox.config.get_config", return_value={"wasm": {"enabled": False, "modules": {}}}):
            result = rt.reload()
        assert isinstance(result, list)


class TestWasmRuntimeEnabled:
    """Behaviour when wasm.enabled=true but wasmtime may or may not be installed."""

    @pytest.fixture
    def wasmtime(self):
        return pytest.importorskip("wasmtime", reason="wasmtime not installed")

    def test_enabled_flag_set_when_engine_initialises(self, wasmtime):
        from beigebox.wasm_runtime import WasmRuntime
        cfg = {"wasm": {"enabled": True, "modules": {}}}
        rt = WasmRuntime(cfg)
        assert rt.enabled is True

    def test_missing_wasm_file_does_not_crash(self, wasmtime):
        from beigebox.wasm_runtime import WasmRuntime
        cfg = {"wasm": {"enabled": True, "modules": {
            "nonexistent": {"path": "/does/not/exist.wasm", "enabled": True}
        }}}
        rt = WasmRuntime(cfg)
        assert "nonexistent" not in rt.list_modules()

    def test_reload_calls_load_modules_when_engine_ready(self, wasmtime):
        from beigebox.wasm_runtime import WasmRuntime
        cfg = {"wasm": {"enabled": True, "modules": {}}}
        rt = WasmRuntime(cfg)
        new_cfg = {"wasm": {"enabled": True, "modules": {}}}
        with patch("beigebox.config.get_config", return_value=new_cfg), \
             patch.object(rt, "_load_modules") as mock_load:
            rt.reload()
        mock_load.assert_called_once()


# ── /api/v1/wasm/reload endpoint ─────────────────────────────────────────────

class TestWasmReloadEndpoint:
    """Test the POST /api/v1/wasm/reload endpoint via FastAPI test client."""

    @pytest.fixture
    def client(self, tmp_path):
        pytest.importorskip("chromadb", reason="chromadb not installed")
        from fastapi.testclient import TestClient
        from beigebox import config as cfg_mod
        from unittest.mock import AsyncMock

        cfg_data = {
            "backend": {"url": "http://localhost:11434", "default_model": "llama3.2", "timeout": 120},
            "server": {"host": "0.0.0.0", "port": 8000},
            "embedding": {"model": "nomic-embed-text", "backend_url": "http://localhost:11434"},
            "storage": {"sqlite_path": str(tmp_path / "test.db"), "chroma_path": str(tmp_path / "chroma")},
            "tools": {"enabled": False},
            "decision_llm": {"enabled": False},
            "hooks": {"directory": str(tmp_path / "hooks")},
            "logging": {"level": "WARNING"},
            "wiretap": {"path": str(tmp_path / "wire.jsonl")},
            "routing": {},
            "model_advertising": {"mode": "hidden"},
            "operator": {"model": "llama3.2"},
            "backends_enabled": False,
            "cost_tracking": {"enabled": False},
            "wasm": {"enabled": False, "modules": {}},
        }
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime: {}\n")

        orig_config  = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_cfg  = cfg_mod._runtime_config
        orig_rt_mt   = cfg_mod._runtime_mtime

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime  = 0.0
        cfg_mod._runtime_config = {}

        mock_da = MagicMock()
        mock_da.from_config.return_value = MagicMock(enabled=False, model="")
        mock_da.from_config.return_value.preload = AsyncMock(return_value=None)
        mock_ec = MagicMock(); mock_ec.ready = True

        # Mock WasmRuntime so we control reload()
        mock_wasm = MagicMock()
        mock_wasm.enabled = False
        mock_wasm.list_modules.return_value = []
        mock_wasm.reload.return_value = ["pii_redactor"]
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
             patch("beigebox.main._preload_embedding_model", AsyncMock()):

            MockTR.return_value.list_tools.return_value = []
            MockHM.return_value.list_hooks.return_value = []

            import beigebox.main as main_mod
            main_mod.proxy = mock_proxy

            from beigebox.main import app
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c, mock_wasm

        cfg_mod._config = orig_config
        cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
        cfg_mod._runtime_config = orig_rt_cfg
        cfg_mod._runtime_mtime  = orig_rt_mt
        main_mod.proxy = None

    def test_reload_returns_ok(self, client):
        c, mock_wasm = client
        r = c.post("/api/v1/wasm/reload")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_reload_returns_module_list(self, client):
        c, mock_wasm = client
        r = c.post("/api/v1/wasm/reload")
        assert "modules" in r.json()
        assert r.json()["modules"] == ["pii_redactor"]

    def test_reload_calls_wasm_runtime_reload(self, client):
        c, mock_wasm = client
        c.post("/api/v1/wasm/reload")
        mock_wasm.reload.assert_called()


# ── GET /api/v1/config includes wasm section ─────────────────────────────────

class TestConfigEndpointWasm:
    """Verify the wasm block appears in GET /api/v1/config."""

    @pytest.fixture
    def client(self, tmp_path):
        pytest.importorskip("chromadb", reason="chromadb not installed")
        from fastapi.testclient import TestClient
        from beigebox import config as cfg_mod
        from unittest.mock import AsyncMock

        cfg_data = {
            "backend": {"url": "http://localhost:11434", "default_model": "llama3.2", "timeout": 120},
            "server": {"host": "0.0.0.0", "port": 8000},
            "embedding": {"model": "nomic-embed-text", "backend_url": "http://localhost:11434"},
            "storage": {"sqlite_path": str(tmp_path / "test.db"), "chroma_path": str(tmp_path / "chroma")},
            "tools": {"enabled": False},
            "decision_llm": {"enabled": False},
            "hooks": {"directory": str(tmp_path / "hooks")},
            "logging": {"level": "WARNING"},
            "wiretap": {"path": str(tmp_path / "wire.jsonl")},
            "routing": {},
            "model_advertising": {"mode": "hidden"},
            "operator": {"model": "llama3.2"},
            "backends_enabled": False,
            "cost_tracking": {"enabled": False},
            "wasm": {
                "enabled": False,
                "default_module": "",
                "modules": {
                    "opener_strip": {"path": "./wasm_modules/opener_strip.wasm", "enabled": True, "description": "Strip sycophantic openers"},
                    "pii_redactor": {"path": "./wasm_modules/pii_redactor.wasm", "enabled": False, "description": "Redact PII"},
                },
            },
        }
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime: {}\n")

        orig_config  = cfg_mod._config
        orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
        orig_rt_cfg  = cfg_mod._runtime_config
        orig_rt_mt   = cfg_mod._runtime_mtime

        cfg_mod._config = cfg_data
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime  = 0.0
        cfg_mod._runtime_config = {}

        mock_da = MagicMock()
        mock_da.from_config.return_value = MagicMock(enabled=False, model="")
        mock_da.from_config.return_value.preload = AsyncMock(return_value=None)
        mock_ec = MagicMock(); mock_ec.ready = True
        mock_wasm = MagicMock()
        mock_wasm.enabled = False
        mock_wasm.list_modules.return_value = []
        mock_wasm.default_module = ""
        mock_proxy = MagicMock(); mock_proxy.wasm_runtime = mock_wasm

        with patch("beigebox.main.SQLiteStore"), \
             patch("beigebox.main.VectorStore"), \
             patch("beigebox.main.ToolRegistry") as MockTR, \
             patch("beigebox.main.DecisionAgent", mock_da), \
             patch("beigebox.main.HookManager") as MockHM, \
             patch("beigebox.main.get_embedding_classifier", return_value=mock_ec), \
             patch("beigebox.main.Proxy", return_value=mock_proxy), \
             patch("beigebox.main._preload_embedding_model", AsyncMock()):

            MockTR.return_value.list_tools.return_value = []
            MockHM.return_value.list_hooks.return_value = []

            import beigebox.main as main_mod
            main_mod.proxy = mock_proxy

            from beigebox.main import app
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c

        cfg_mod._config = orig_config
        cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
        cfg_mod._runtime_config = orig_rt_cfg
        cfg_mod._runtime_mtime  = orig_rt_mt
        main_mod.proxy = None

    def test_config_has_wasm_block(self, client):
        r = client.get("/api/v1/config")
        assert r.status_code == 200
        assert "wasm" in r.json()

    def test_wasm_block_has_enabled(self, client):
        data = client.get("/api/v1/config").json()
        assert "enabled" in data["wasm"]

    def test_wasm_block_has_modules_list(self, client):
        data = client.get("/api/v1/config").json()
        assert "modules" in data["wasm"]
        assert isinstance(data["wasm"]["modules"], list)

    def test_wasm_block_has_modules_cfg(self, client):
        data = client.get("/api/v1/config").json()
        assert "modules_cfg" in data["wasm"]
        cfg = data["wasm"]["modules_cfg"]
        assert "opener_strip" in cfg
        assert "pii_redactor" in cfg

    def test_modules_cfg_has_description(self, client):
        data = client.get("/api/v1/config").json()
        assert data["wasm"]["modules_cfg"]["opener_strip"]["description"] == "Strip sycophantic openers"

    def test_modules_cfg_has_enabled_flag(self, client):
        data = client.get("/api/v1/config").json()
        assert data["wasm"]["modules_cfg"]["pii_redactor"]["enabled"] is False

    def test_wasm_block_has_default_module(self, client):
        data = client.get("/api/v1/config").json()
        assert "default_module" in data["wasm"]
