"""
Tests for system_context.py — hot-reloadable global prompt injection.

Covers:
  - get_system_context: disabled by default, returns text when enabled
  - Hot-reload on file change (mtime-based)
  - inject_system_context: prepend to existing system message vs insert new
  - read_context_file / write_context_file
  - Missing file handled gracefully
"""

import pytest
from pathlib import Path
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfg(path: str, enabled: bool = True) -> dict:
    return {"system_context": {"enabled": enabled, "path": path}}


def _reset_module():
    """Reset module-level hot-reload state between tests."""
    import beigebox.system_context as sc
    sc._context_text = ""
    sc._context_mtime = 0.0
    sc._context_path = None


# ── get_system_context ────────────────────────────────────────────────────────

class TestGetSystemContext:
    def setup_method(self):
        _reset_module()

    def test_disabled_returns_empty(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("you are a helpful assistant")
        cfg = _cfg(str(f), enabled=False)
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import get_system_context
            assert get_system_context(cfg) == ""

    def test_enabled_returns_text(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("you are a helpful assistant")
        cfg = _cfg(str(f), enabled=True)
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import get_system_context
            result = get_system_context(cfg)
        assert result == "you are a helpful assistant"

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = _cfg(str(tmp_path / "nonexistent.md"), enabled=True)
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import get_system_context
            assert get_system_context(cfg) == ""

    def test_runtime_config_overrides_static_enabled(self, tmp_path):
        """runtime_config system_context_enabled=True overrides static enabled=False."""
        f = tmp_path / "sc.md"
        f.write_text("runtime override text")
        cfg = _cfg(str(f), enabled=False)
        with patch("beigebox.config.get_runtime_config",
                   return_value={"system_context_enabled": True}):
            from beigebox.system_context import get_system_context
            result = get_system_context(cfg)
        assert result == "runtime override text"

    def test_hot_reload_on_mtime_change(self, tmp_path):
        """Content is reloaded when file mtime changes."""
        import beigebox.system_context as sc
        f = tmp_path / "sc.md"
        f.write_text("version one")
        cfg = _cfg(str(f), enabled=True)

        with patch("beigebox.config.get_runtime_config", return_value={}):
            r1 = sc.get_system_context(cfg)
            assert r1 == "version one"

            # Simulate file change by updating content and busting mtime cache
            f.write_text("version two")
            sc._context_mtime = 0.0  # force reload

            r2 = sc.get_system_context(cfg)
        assert r2 == "version two"

    def test_no_reload_when_mtime_unchanged(self, tmp_path):
        """When mtime matches the cached value, _context_text is returned unchanged."""
        import beigebox.system_context as sc
        f = tmp_path / "sc.md"
        f.write_text("original")
        cfg = _cfg(str(f), enabled=True)

        with patch("beigebox.config.get_runtime_config", return_value={}):
            # Load once — populates cache with real mtime
            r1 = sc.get_system_context(cfg)
            assert r1 == "original"

            # Manually set cached text to something different
            # while keeping the mtime locked to current file mtime
            sc._context_text = "cached value"
            sc._context_mtime = f.stat().st_mtime  # matches real mtime → no reload

            result = sc.get_system_context(cfg)

        # Returns cached value because mtime hasn't changed
        assert result == "cached value"

    def test_returns_stripped_text(self, tmp_path):
        """Leading/trailing whitespace is stripped from file contents."""
        f = tmp_path / "sc.md"
        f.write_text("  \n  be helpful  \n  ")
        cfg = _cfg(str(f), enabled=True)
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import get_system_context
            assert get_system_context(cfg) == "be helpful"


# ── inject_system_context ─────────────────────────────────────────────────────

class TestInjectSystemContext:
    def setup_method(self):
        _reset_module()

    def _make_inject(self, context_text: str):
        """Patch get_system_context to return a fixed string."""
        import beigebox.system_context as sc
        sc._context_text = context_text
        sc._context_mtime = 999999.0  # won't reload

    def test_no_injection_when_empty_context(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("")
        cfg = _cfg(str(f), enabled=True)
        body = {"messages": [{"role": "user", "content": "hello"}]}
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import inject_system_context
            result = inject_system_context(body, cfg)
        assert result["messages"] == [{"role": "user", "content": "hello"}]

    def test_inserts_new_system_message_when_none_exists(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("global instructions")
        cfg = _cfg(str(f), enabled=True)
        body = {"messages": [{"role": "user", "content": "hello"}]}
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import inject_system_context
            result = inject_system_context(body, cfg)
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "global instructions"
        assert result["messages"][1]["role"] == "user"

    def test_prepends_to_existing_system_message(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("global instructions")
        cfg = _cfg(str(f), enabled=True)
        body = {"messages": [
            {"role": "system", "content": "local instructions"},
            {"role": "user", "content": "hello"},
        ]}
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import inject_system_context
            result = inject_system_context(body, cfg)
        sys_msg = result["messages"][0]["content"]
        assert sys_msg.startswith("global instructions")
        assert "local instructions" in sys_msg

    def test_no_modification_when_disabled(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("global instructions")
        cfg = _cfg(str(f), enabled=False)
        body = {"messages": [{"role": "user", "content": "hello"}]}
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import inject_system_context
            result = inject_system_context(body, cfg)
        assert result["messages"][0]["role"] == "user"

    def test_no_modification_for_empty_messages(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("global instructions")
        cfg = _cfg(str(f), enabled=True)
        body = {"messages": []}
        with patch("beigebox.config.get_runtime_config", return_value={}):
            from beigebox.system_context import inject_system_context
            result = inject_system_context(body, cfg)
        assert result["messages"] == []


# ── read_context_file / write_context_file ────────────────────────────────────

class TestFileIO:
    def setup_method(self):
        _reset_module()

    def test_read_returns_file_contents(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("some context")
        cfg = _cfg(str(f))
        from beigebox.system_context import read_context_file
        assert read_context_file(cfg) == "some context"

    def test_read_missing_file_returns_empty(self, tmp_path):
        cfg = _cfg(str(tmp_path / "nope.md"))
        from beigebox.system_context import read_context_file
        assert read_context_file(cfg) == ""

    def test_write_creates_file(self, tmp_path):
        f = tmp_path / "sc.md"
        cfg = _cfg(str(f))
        from beigebox.system_context import write_context_file, read_context_file
        result = write_context_file(cfg, "new content")
        assert result is True
        assert read_context_file(cfg) == "new content"

    def test_write_busts_mtime_cache(self, tmp_path):
        import beigebox.system_context as sc
        f = tmp_path / "sc.md"
        f.write_text("old")
        cfg = _cfg(str(f))
        sc._context_mtime = 999999.0  # simulate cached state
        from beigebox.system_context import write_context_file
        write_context_file(cfg, "new content")
        assert sc._context_mtime == 0.0

    def test_write_overwrites_existing(self, tmp_path):
        f = tmp_path / "sc.md"
        f.write_text("original")
        cfg = _cfg(str(f))
        from beigebox.system_context import write_context_file, read_context_file
        write_context_file(cfg, "updated")
        assert read_context_file(cfg) == "updated"
