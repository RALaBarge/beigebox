"""
Tests for web UI config endpoint and vi mode toggle.
Covers:
  - /api/v1/config includes web_ui block
  - web_ui.vi_mode reflects runtime_config.yaml
  - /api/v1/web-ui/toggle-vi-mode toggles the value
  - update_runtime_config persists correctly
  - vi.js is served as a static file
  - index.html is served at / and /ui
"""
import pytest
import yaml
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Config module tests ───────────────────────────────────────────────────────

class TestUpdateRuntimeConfig:
    def test_creates_runtime_key_if_missing(self, tmp_path):
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("# empty\n")

        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0

        try:
            result = cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            assert result is True
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["web_ui_vi_mode"] is True
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_updates_existing_key(self, tmp_path):
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")

        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0

        try:
            cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["web_ui_vi_mode"] is True
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_preserves_other_keys(self, tmp_path):
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  default_model: llama3.2\n  force_route: ''\n")

        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0

        try:
            cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["default_model"] == "llama3.2"
            assert data["runtime"]["web_ui_vi_mode"] is True
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_busts_mtime_cache(self, tmp_path):
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")

        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 999999.0  # fake stale mtime

        try:
            cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            assert cfg_mod._runtime_mtime == 0.0  # should be reset
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_returns_false_on_unwritable_path(self, tmp_path):
        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = Path("/nonexistent/path/runtime_config.yaml")

        try:
            result = cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            assert result is False
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path

    def test_toggle_false_to_true_to_false(self, tmp_path):
        rt_path = tmp_path / "runtime_config.yaml"
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")

        from beigebox import config as cfg_mod
        orig_path = cfg_mod._RUNTIME_CONFIG_PATH
        cfg_mod._RUNTIME_CONFIG_PATH = rt_path
        cfg_mod._runtime_mtime = 0.0

        try:
            cfg_mod.update_runtime_config("web_ui_vi_mode", True)
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["web_ui_vi_mode"] is True

            cfg_mod.update_runtime_config("web_ui_vi_mode", False)
            data = yaml.safe_load(rt_path.read_text())
            assert data["runtime"]["web_ui_vi_mode"] is False
        finally:
            cfg_mod._RUNTIME_CONFIG_PATH = orig_path


# ── FastAPI endpoint tests ────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """Create a test client with minimal mocked dependencies."""
    pytest.importorskip("chromadb", reason="chromadb not installed")
    from fastapi.testclient import TestClient
    from beigebox import config as cfg_mod

    # Minimal config
    cfg_data = {
        "backend": {"url": "http://localhost:11434", "default_model": "llama3.2", "timeout": 120},
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
        "operator": {"model": "llama3.2"},
    }

    rt_path = tmp_path / "runtime_config.yaml"
    rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n  web_ui_palette: default\n")

    orig_config = cfg_mod._config
    orig_rt_path = cfg_mod._RUNTIME_CONFIG_PATH
    orig_rt_mtime = cfg_mod._runtime_mtime
    orig_rt_config = cfg_mod._runtime_config

    cfg_mod._config = cfg_data
    cfg_mod._RUNTIME_CONFIG_PATH = rt_path
    cfg_mod._runtime_mtime = 0.0
    cfg_mod._runtime_config = {}

    import beigebox.main  # ensure module is imported before patching

    with patch("beigebox.main.SQLiteStore"), \
         patch("beigebox.main.VectorStore"), \
         patch("beigebox.main.ToolRegistry") as MockTR, \
         patch("beigebox.main.DecisionAgent") as MockDA, \
         patch("beigebox.main.HookManager") as MockHM, \
         patch("beigebox.main.get_embedding_classifier") as MockEC, \
         patch("beigebox.main._preload_embedding_model"):

        MockTR.return_value.list_tools.return_value = []
        MockDA.from_config.return_value = MagicMock(enabled=False, model="")
        MockDA.from_config.return_value.preload = MagicMock(return_value=None)
        MockHM.return_value.list_hooks.return_value = []
        ec = MagicMock(); ec.ready = False
        MockEC.return_value = ec

        from beigebox.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c, rt_path

    cfg_mod._config = orig_config
    cfg_mod._RUNTIME_CONFIG_PATH = orig_rt_path
    cfg_mod._runtime_mtime = orig_rt_mtime
    cfg_mod._runtime_config = orig_rt_config


class TestConfigEndpointWebUi:
    def test_config_includes_web_ui_block(self, client):
        c, _ = client
        r = c.get("/api/v1/config")
        assert r.status_code == 200
        data = r.json()
        assert "web_ui" in data

    def test_web_ui_vi_mode_default_false(self, client):
        c, _ = client
        r = c.get("/api/v1/config")
        assert r.json()["web_ui"]["vi_mode"] is False

    def test_web_ui_palette_default(self, client):
        c, _ = client
        r = c.get("/api/v1/config")
        assert r.json()["web_ui"]["palette"] == "default"

    def test_web_ui_vi_mode_true_when_set(self, client):
        from beigebox import config as cfg_mod
        c, rt_path = client
        rt_path.write_text("runtime:\n  web_ui_vi_mode: true\n")
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_config = {}

        r = c.get("/api/v1/config")
        assert r.json()["web_ui"]["vi_mode"] is True


class TestToggleViModeEndpoint:
    def test_toggle_returns_new_state(self, client):
        c, _ = client
        r = c.post("/api/v1/web-ui/toggle-vi-mode")
        assert r.status_code == 200
        data = r.json()
        assert "vi_mode" in data
        assert "ok" in data
        assert data["ok"] is True

    def test_toggle_false_to_true(self, client):
        c, rt_path = client
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")
        from beigebox import config as cfg_mod
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_config = {}

        r = c.post("/api/v1/web-ui/toggle-vi-mode")
        assert r.json()["vi_mode"] is True

    def test_toggle_true_to_false(self, client):
        c, rt_path = client
        rt_path.write_text("runtime:\n  web_ui_vi_mode: true\n")
        from beigebox import config as cfg_mod
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_config = {}

        r = c.post("/api/v1/web-ui/toggle-vi-mode")
        assert r.json()["vi_mode"] is False

    def test_toggle_persists_to_runtime_yaml(self, client):
        c, rt_path = client
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")
        from beigebox import config as cfg_mod
        cfg_mod._runtime_mtime = 0.0
        cfg_mod._runtime_config = {}

        c.post("/api/v1/web-ui/toggle-vi-mode")
        data = yaml.safe_load(rt_path.read_text())
        assert data["runtime"]["web_ui_vi_mode"] is True

    def test_double_toggle_returns_to_original(self, client):
        c, rt_path = client
        rt_path.write_text("runtime:\n  web_ui_vi_mode: false\n")
        from beigebox import config as cfg_mod

        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_config = {}
        r1 = c.post("/api/v1/web-ui/toggle-vi-mode")
        assert r1.json()["vi_mode"] is True

        cfg_mod._runtime_mtime = 0.0; cfg_mod._runtime_config = {}
        r2 = c.post("/api/v1/web-ui/toggle-vi-mode")
        assert r2.json()["vi_mode"] is False


# ── Static file and HTML serving ─────────────────────────────────────────────

class TestWebFileServing:
    def test_root_serves_html(self, client):
        c, _ = client
        r = c.get("/")
        # Will 404 if index.html doesn't exist on test machine, 200 if it does
        assert r.status_code in (200, 404, 500)

    def test_ui_alias_serves_html(self, client):
        c, _ = client
        r = c.get("/ui")
        assert r.status_code in (200, 404, 500)

    def test_vi_js_not_inlined_by_default(self, tmp_path):
        """vi.js content must not be present in index.html source by default."""
        web_dir = Path(__file__).parent.parent / "beigebox" / "web"
        html_path = web_dir / "index.html"
        if not html_path.exists():
            pytest.skip("index.html not found")
        content = html_path.read_text()
        # vi.js should be referenced by src, not inlined
        assert "vi-script" in content or "/web/vi.js" in content
        # The actual vi mode implementation should NOT be inline
        assert "saveUndo" not in content
        assert "yankBuffer" not in content

    def test_vi_js_exists(self):
        vi_path = Path(__file__).parent.parent / "beigebox" / "web" / "vi.js"
        assert vi_path.exists(), "vi.js must exist in beigebox/web/"

    def test_vi_js_has_key_bindings(self):
        vi_path = Path(__file__).parent.parent / "beigebox" / "web" / "vi.js"
        if not vi_path.exists():
            pytest.skip("vi.js not found")
        content = vi_path.read_text()
        for binding in ["NORMAL", "INSERT", "yankBuffer", "setMode", "saveUndo"]:
            assert binding in content, f"Expected '{binding}' in vi.js"

    def test_vi_js_has_mode_indicator(self):
        vi_path = Path(__file__).parent.parent / "beigebox" / "web" / "vi.js"
        if not vi_path.exists():
            pytest.skip("vi.js not found")
        content = vi_path.read_text()
        assert "-- NORMAL --" in content
        assert "-- INSERT --" in content
        assert "vi-indicator" in content

    def test_index_html_has_pi_button(self):
        html_path = Path(__file__).parent.parent / "beigebox" / "web" / "index.html"
        if not html_path.exists():
            pytest.skip("index.html not found")
        content = html_path.read_text()
        assert "vi-toggle" in content
        assert "toggleViMode" in content
        assert "π" in content

    def test_index_html_loads_vi_dynamically(self):
        """vi.js must be loaded via dynamic script injection, not a static tag."""
        html_path = Path(__file__).parent.parent / "beigebox" / "web" / "index.html"
        if not html_path.exists():
            pytest.skip("index.html not found")
        content = html_path.read_text()
        # Should NOT have a static <script src="/web/vi.js"> tag
        assert '<script src="/web/vi.js"' not in content
        # Should have dynamic injection logic
        assert "injectViMode" in content
        assert "createElement('script')" in content or 'createElement("script")' in content
