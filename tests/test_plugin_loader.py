"""
Tests for plugin_loader: auto-discovery, z-command alias registration,
PLUGIN_Z_ALIASES override, and suppression with empty dict.
"""
import textwrap
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def plugins_dir(tmp_path):
    return tmp_path / "plugins"


def _write_plugin(plugins_dir: Path, filename: str, source: str):
    plugins_dir.mkdir(exist_ok=True)
    (plugins_dir / filename).write_text(textwrap.dedent(source))


def _load(plugins_dir: Path, tools_cfg: dict | None = None) -> dict:
    from beigebox.tools.plugin_loader import load_plugins
    cfg = tools_cfg or {"plugins": {"enabled": True}}
    return load_plugins(plugins_dir, cfg)


# ── Basic discovery ───────────────────────────────────────────────────────────

class TestPluginDiscovery:
    def test_returns_empty_when_disabled(self, plugins_dir):
        _write_plugin(plugins_dir, "foo.py", """
            PLUGIN_NAME = "foo"
            class FooTool:
                def run(self, q): return "foo"
        """)
        result = _load(plugins_dir, {"plugins": {"enabled": False}})
        assert result == {}

    def test_discovers_tool_by_plugin_name(self, plugins_dir):
        _write_plugin(plugins_dir, "dice.py", """
            PLUGIN_NAME = "dice"
            class DiceTool:
                def run(self, q): return "rolled"
        """)
        result = _load(plugins_dir)
        assert "dice" in result

    def test_auto_names_from_class_name(self, plugins_dir):
        _write_plugin(plugins_dir, "my_thing.py", """
            class MyThingTool:
                def run(self, q): return "thing"
        """)
        result = _load(plugins_dir)
        assert "my_thing" in result

    def test_skips_files_starting_with_underscore(self, plugins_dir):
        _write_plugin(plugins_dir, "_private.py", """
            PLUGIN_NAME = "private"
            class PrivateTool:
                def run(self, q): return "hidden"
        """)
        result = _load(plugins_dir)
        assert "private" not in result

    def test_skips_files_with_no_tool_class(self, plugins_dir):
        _write_plugin(plugins_dir, "util.py", "def helper(): pass\n")
        result = _load(plugins_dir)
        assert result == {}

    def test_respects_per_plugin_enabled_false(self, plugins_dir):
        _write_plugin(plugins_dir, "dice.py", """
            PLUGIN_NAME = "dice"
            class DiceTool:
                def run(self, q): return "rolled"
        """)
        cfg = {"plugins": {"enabled": True, "dice": {"enabled": False}}}
        result = _load(plugins_dir, cfg)
        assert "dice" not in result


# ── Z-command alias registration ─────────────────────────────────────────────

class TestZCommandAliases:
    def _fresh_tool_directives(self):
        """Return a reference to TOOL_DIRECTIVES after resetting imported state."""
        import importlib, beigebox.agents.zcommand as zc
        importlib.reload(zc)
        return zc.TOOL_DIRECTIVES

    def test_plugin_name_auto_registered_as_alias(self, plugins_dir):
        _write_plugin(plugins_dir, "dice.py", """
            PLUGIN_NAME = "dice"
            class DiceTool:
                def run(self, q): return "rolled"
        """)
        td = self._fresh_tool_directives()
        _load(plugins_dir)
        # After load, "dice" should map to "dice" in TOOL_DIRECTIVES
        from beigebox.agents.zcommand import TOOL_DIRECTIVES
        assert "dice" in TOOL_DIRECTIVES
        assert TOOL_DIRECTIVES["dice"] == "dice"

    def test_plugin_z_aliases_overrides_default(self, plugins_dir):
        _write_plugin(plugins_dir, "units.py", """
            PLUGIN_NAME = "units"
            PLUGIN_Z_ALIASES = {"convert": "units", "unit": "units"}
            class UnitsTool:
                def run(self, q): return "converted"
        """)
        import importlib, beigebox.agents.zcommand as zc
        importlib.reload(zc)
        _load(plugins_dir)
        from beigebox.agents.zcommand import TOOL_DIRECTIVES
        assert "convert" in TOOL_DIRECTIVES
        assert "unit" in TOOL_DIRECTIVES
        assert TOOL_DIRECTIVES["convert"] == "units"

    def test_empty_plugin_z_aliases_suppresses_registration(self, plugins_dir):
        _write_plugin(plugins_dir, "silent.py", """
            PLUGIN_NAME = "silent"
            PLUGIN_Z_ALIASES = {}
            class SilentTool:
                def run(self, q): return "silent"
        """)
        import importlib, beigebox.agents.zcommand as zc
        importlib.reload(zc)
        _load(plugins_dir)
        from beigebox.agents.zcommand import TOOL_DIRECTIVES
        assert "silent" not in TOOL_DIRECTIVES

    def test_alias_does_not_overwrite_existing_directive(self, plugins_dir):
        """A plugin named 'search' should NOT clobber the built-in 'search' alias."""
        _write_plugin(plugins_dir, "search.py", """
            PLUGIN_NAME = "search"
            class SearchTool:
                def run(self, q): return "searched"
        """)
        import importlib, beigebox.agents.zcommand as zc
        importlib.reload(zc)
        original_value = zc.TOOL_DIRECTIVES.get("search")
        _load(plugins_dir)
        from beigebox.agents.zcommand import TOOL_DIRECTIVES
        # The original built-in value must be preserved
        assert TOOL_DIRECTIVES.get("search") == original_value

    def test_plugin_tool_runs_after_alias_registration(self, plugins_dir):
        _write_plugin(plugins_dir, "greet.py", """
            PLUGIN_NAME = "greet"
            class GreetTool:
                def run(self, q): return f"Hello, {q}!"
        """)
        result = _load(plugins_dir)
        assert result["greet"].run("world") == "Hello, world!"
