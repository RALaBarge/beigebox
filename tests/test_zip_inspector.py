"""
Tests for plugins/zip_inspector.py — zip archive inspection.
"""

import zipfile
import pytest
from pathlib import Path
from unittest.mock import patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_zip(tmp_path: Path, files: dict[str, bytes]) -> Path:
    """Create a zip file in tmp_path with given filename→content mapping."""
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return zp


# ── _fmt_size ─────────────────────────────────────────────────────────────────

class TestFmtSize:
    def _fmt(self, n):
        from plugins.zip_inspector import _fmt_size
        return _fmt_size(n)

    def test_bytes(self):
        assert self._fmt(500) == "500 B"

    def test_kilobytes(self):
        result = self._fmt(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = self._fmt(2 * 1024 * 1024)
        assert "MB" in result

    def test_zero_bytes(self):
        assert self._fmt(0) == "0 B"


# ── _build_tree ───────────────────────────────────────────────────────────────

class TestBuildTree:
    def _tree(self, names, sizes=None):
        from plugins.zip_inspector import _build_tree
        return _build_tree(names, sizes or {})

    def test_single_file(self):
        tree = self._tree(["file.txt"])
        assert "file.txt" in tree

    def test_last_entry_uses_corner(self):
        tree = self._tree(["a.txt", "b.txt"])
        assert "└──" in tree

    def test_non_last_uses_branch(self):
        tree = self._tree(["a.txt", "b.txt"])
        assert "├──" in tree

    def test_size_shown_when_available(self):
        tree = self._tree(["a.txt"], {"a.txt": 1024})
        assert "KB" in tree or "B" in tree

    def test_empty_names(self):
        tree = self._tree([])
        assert tree == ""


# ── ZipInspectorTool.run ──────────────────────────────────────────────────────

class TestZipInspectorTool:
    @pytest.fixture(autouse=True)
    def _patch_workspace(self, tmp_path):
        """Redirect _WORKSPACE_IN and _WORKSPACE_OUT to tmp dirs."""
        ws_in  = tmp_path / "in"
        ws_out = tmp_path / "out"
        ws_in.mkdir()
        ws_out.mkdir()
        import plugins.zip_inspector as zi
        with patch.object(zi, "_WORKSPACE_IN",  ws_in), \
             patch.object(zi, "_WORKSPACE_OUT", ws_out):
            self._ws_in  = ws_in
            self._ws_out = ws_out
            yield

    def _tool(self):
        from plugins.zip_inspector import ZipInspectorTool
        return ZipInspectorTool()

    def _zip(self, files: dict[str, str]) -> Path:
        zp = self._ws_in / "test.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return zp

    def test_file_not_found(self):
        result = self._tool().run("missing.zip")
        assert "not found" in result.lower() or "File not found" in result

    def test_not_a_zip(self, tmp_path):
        not_zip = self._ws_in / "text.zip"
        not_zip.write_text("not a zip file")
        result = self._tool().run("text.zip")
        assert "not a valid zip" in result.lower() or "could not read" in result.lower()

    def test_basic_zip_inspection(self):
        self._zip({"hello.txt": "hello world"})
        result = self._tool().run("test.zip")
        assert "hello.txt" in result
        assert "1 files" in result

    def test_text_preview_included(self):
        self._zip({"readme.txt": "this is the content"})
        result = self._tool().run("test.zip")
        assert "this is the content" in result

    def test_binary_file_skipped_in_preview(self):
        self._zip({"data.bin": b"\x00\x01\x02\x03".decode("latin-1")})
        result = self._tool().run("test.zip")
        # Should not crash; binary content may not appear in preview
        assert "data.bin" in result

    def test_multiple_files(self):
        self._zip({"a.txt": "aaa", "b.txt": "bbb", "c.txt": "ccc"})
        result = self._tool().run("test.zip")
        assert "3 files" in result

    def test_saves_report_to_workspace_out(self):
        self._zip({"file.txt": "content"})
        self._tool().run("test.zip")
        out_file = self._ws_out / "test_inspection.txt"
        assert out_file.exists()

    def test_report_saved_message_in_output(self):
        self._zip({"file.txt": "content"})
        result = self._tool().run("test.zip")
        assert "workspace/out" in result

    def test_absolute_path_works(self):
        zp = self._zip({"x.txt": "data"})
        result = self._tool().run(str(zp))
        assert "x.txt" in result

    def test_strips_quotes_from_filename(self):
        self._zip({"q.txt": "quoted"})
        result = self._tool().run("'test.zip'")
        assert "q.txt" in result
