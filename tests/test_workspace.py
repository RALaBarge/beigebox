"""
Tests for workspace API endpoints:
  GET    /api/v1/workspace
  DELETE /api/v1/workspace/out/{filename}
  POST   /api/v1/workspace/upload
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ws_cfg(ws_root: Path, max_mb: int = 0) -> dict:
    """Minimal config dict with workspace.path set to an absolute tmp path."""
    return {"workspace": {"path": str(ws_root), "max_mb": max_mb}}


def run(coro):
    return asyncio.run(coro)


def _parse(response) -> dict:
    return json.loads(response.body)


def _make_upload(filename, content: bytes):
    mock = AsyncMock()
    mock.filename = filename
    mock.read = AsyncMock(return_value=content)
    return mock


# ── GET /api/v1/workspace ─────────────────────────────────────────────────────

class TestApiWorkspaceList:
    def test_empty_dirs(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "in").mkdir(parents=True)
        (ws / "out").mkdir(parents=True)
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        assert data["in"] == []
        assert data["out"] == []
        assert data["in_bytes"] == 0
        assert data["out_bytes"] == 0

    def test_lists_in_and_out_files(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "in").mkdir(parents=True)
        (ws / "out").mkdir(parents=True)
        (ws / "in" / "data.csv").write_text("col1,col2\n")
        (ws / "out" / "report.md").write_text("# Result\n")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        assert len(data["in"]) == 1
        assert data["in"][0]["name"] == "data.csv"
        assert len(data["out"]) == 1
        assert data["out"][0]["name"] == "report.md"

    def test_file_entry_has_size_and_modified(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "out").mkdir(parents=True)
        (ws / "out" / "a.txt").write_bytes(b"hello")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        entry = data["out"][0]
        assert entry["size"] == 5
        assert "modified" in entry
        assert entry["modified"].endswith("+00:00")

    def test_gitkeep_excluded(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "in").mkdir(parents=True)
        (ws / "in" / ".gitkeep").write_text("")
        (ws / "in" / "real.txt").write_text("hi")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        names = [e["name"] for e in data["in"]]
        assert ".gitkeep" not in names
        assert "real.txt" in names

    def test_nonexistent_dirs_return_empty(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"  # not created
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        assert data["in"] == []
        assert data["out"] == []

    def test_max_mb_included_in_response(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws, max_mb=200)):
            data = _parse(run(api_workspace()))
        assert data["max_mb"] == 200

    def test_out_bytes_is_sum_of_file_sizes(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "out").mkdir(parents=True)
        (ws / "out" / "a.bin").write_bytes(b"x" * 100)
        (ws / "out" / "b.bin").write_bytes(b"y" * 200)
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        assert data["out_bytes"] == 300

    def test_entries_sorted_by_name(self, tmp_path):
        from beigebox.main import api_workspace
        ws = tmp_path / "workspace"
        (ws / "out").mkdir(parents=True)
        (ws / "out" / "z.txt").write_text("z")
        (ws / "out" / "a.txt").write_text("a")
        (ws / "out" / "m.txt").write_text("m")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace()))
        names = [e["name"] for e in data["out"]]
        assert names == ["a.txt", "m.txt", "z.txt"]


# ── DELETE /api/v1/workspace/out/{filename} ───────────────────────────────────

class TestApiWorkspaceDelete:
    def test_deletes_existing_file(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        (ws / "out").mkdir(parents=True)
        f = ws / "out" / "report.md"
        f.write_text("done")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete("report.md"))
        data = _parse(result)
        assert data["ok"] is True
        assert not f.exists()

    def test_missing_file_returns_404(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        (ws / "out").mkdir(parents=True)
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete("ghost.txt"))
        assert result.status_code == 404
        assert _parse(result)["ok"] is False

    def test_slash_in_filename_blocked(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete("../secret.txt"))
        assert result.status_code == 400
        assert _parse(result)["ok"] is False

    def test_dotdot_alone_blocked(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete(".."))
        assert result.status_code == 400
        assert _parse(result)["ok"] is False

    def test_dotdot_in_name_blocked(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete("file..evil"))
        assert result.status_code == 400
        assert _parse(result)["ok"] is False

    def test_only_deletes_from_out_not_in(self, tmp_path):
        from beigebox.main import api_workspace_delete
        ws = tmp_path / "workspace"
        (ws / "in").mkdir(parents=True)
        (ws / "out").mkdir(parents=True)
        in_file = ws / "in" / "protected.txt"
        in_file.write_text("keep me")
        # out/protected.txt doesn't exist — should 404, not touch in/
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_delete("protected.txt"))
        assert result.status_code == 404
        assert in_file.exists()  # in/ file untouched


# ── POST /api/v1/workspace/upload ────────────────────────────────────────────

class TestApiWorkspaceUpload:
    def test_uploads_file_to_in_dir(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        upload = _make_upload("data.csv", b"col1,col2\n1,2\n")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            result = run(api_workspace_upload(upload))
        data = _parse(result)
        assert data["ok"] is True
        assert data["name"] == "data.csv"
        assert data["size"] == 14
        assert (ws / "in" / "data.csv").read_bytes() == b"col1,col2\n1,2\n"

    def test_creates_in_dir_if_missing(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        upload = _make_upload("new.txt", b"hello")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            run(api_workspace_upload(upload))
        assert (ws / "in").is_dir()

    def test_none_filename_falls_back_to_upload(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        upload = _make_upload(None, b"data")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace_upload(upload)))
        assert data["ok"] is True
        assert data["name"] == "upload"

    def test_directory_component_stripped(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        # A path like "subdir/file.txt" — .name strips the directory part
        upload = _make_upload("subdir/file.txt", b"hi")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace_upload(upload)))
        assert data["ok"] is True
        assert data["name"] == "file.txt"
        assert (ws / "in" / "file.txt").exists()

    def test_dotdot_prefix_filename_blocked(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        # "..evil" — after Path.name extraction: "..evil"; ".." in "..evil" is True
        upload = _make_upload("..evil", b"bad")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace_upload(upload)))
        assert data["ok"] is False

    def test_binary_content_preserved(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        content = bytes(range(256))
        upload = _make_upload("binary.bin", content)
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace_upload(upload)))
        assert data["ok"] is True
        assert (ws / "in" / "binary.bin").read_bytes() == content

    def test_overwrites_existing_file(self, tmp_path):
        from beigebox.main import api_workspace_upload
        ws = tmp_path / "workspace"
        (ws / "in").mkdir(parents=True)
        (ws / "in" / "existing.txt").write_bytes(b"old content")
        upload = _make_upload("existing.txt", b"new content")
        with patch("beigebox.routers.workspace.get_config", return_value=_ws_cfg(ws)):
            data = _parse(run(api_workspace_upload(upload)))
        assert data["ok"] is True
        assert (ws / "in" / "existing.txt").read_bytes() == b"new content"
