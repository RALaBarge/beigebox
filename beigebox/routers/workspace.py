"""Workspace, conversation ops, and document-transform endpoints.

Extracted from beigebox/main.py (B-4). Includes:
- Conversation operations: replay, fork
- Workspace file management: list, mounts (add/delete), out file delete,
  upload, PDF transform
- UI toggle: web-ui vi-mode

The ``_index_document`` helper (PDF parse + chunk + embed in a thread)
lives in ``_shared.py`` since it's also used by the toolbox tooling
in routers/tools.py (B-6).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from beigebox.config import get_config, get_runtime_config, update_runtime_config
from beigebox.routers._shared import _index_document
from beigebox.state import get_state


logger = logging.getLogger(__name__)


router = APIRouter()


# ── Conversation ops ─────────────────────────────────────────────────────

@router.get("/api/v1/conversation/{conv_id}/replay")
async def api_conversation_replay(conv_id: str):
    """Reconstruct a conversation with full routing context."""
    cfg = get_config()
    rt = get_runtime_config()
    if "conversation_replay_enabled" in rt:
        replay_enabled = rt.get("conversation_replay_enabled")
    else:
        replay_enabled = cfg.get("conversation_replay", {}).get("enabled", False)
    if not replay_enabled:
        return JSONResponse({
            "enabled": False,
            "message": "Conversation replay is disabled. Enable it in Config tab or set conversation_replay.enabled: true in config.yaml.",
        })
    _st = get_state()
    if not _st.conversations:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    from beigebox.replay import ConversationReplayer
    wire_path = cfg.get("wiretap", {}).get("path", "./data/wire.jsonl")
    replayer = ConversationReplayer(_st.conversations, wiretap_path=wire_path)
    result = replayer.replay(conv_id)
    return JSONResponse(result)


@router.post("/api/v1/conversation/{conv_id}/fork")
async def api_conversation_fork(conv_id: str, request: Request):
    """Fork a conversation into a new one.

    Body (JSON, all optional):
        branch_at  int — 0-based message index to branch at (inclusive).
                         Omit to copy the full conversation.
    """
    _st = get_state()
    if not _st.conversations:
        return JSONResponse({"error": "Storage not initialized"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    branch_at = body.get("branch_at")
    if branch_at is not None:
        try:
            branch_at = int(branch_at)
        except (ValueError, TypeError):
            return JSONResponse({"error": "branch_at must be an integer"}, status_code=400)

    new_conv_id = uuid4().hex

    try:
        copied = _st.conversations.fork_conversation(
            source_conv_id=conv_id,
            new_conv_id=new_conv_id,
            branch_at=branch_at,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    if copied == 0:
        return JSONResponse(
            {"error": f"Conversation '{conv_id}' not found or empty"},
            status_code=404,
        )

    return JSONResponse({
        "new_conversation_id": new_conv_id,
        "messages_copied": copied,
        "source_conversation": conv_id,
        "branch_at": branch_at,
    })


# ── Workspace file management ────────────────────────────────────────────

@router.get("/api/v1/workspace")
async def api_workspace():
    """List files in workspace/in and workspace/out with sizes and timestamps."""
    cfg = get_config()
    ws_cfg = cfg.get("workspace", {})
    ws_path_raw = ws_cfg.get("path", "./workspace")
    max_mb = ws_cfg.get("max_mb", 0)

    app_root = Path(__file__).parent.parent.parent
    ws_path = (app_root / ws_path_raw).resolve()

    def scan_dir(dirpath: Path) -> tuple[list[dict], int]:
        entries = []
        total = 0
        if not dirpath.exists():
            return entries, total
        for entry in os.scandir(dirpath):
            if entry.name == ".gitkeep":
                continue
            try:
                stat = entry.stat()
            except (FileNotFoundError, OSError):
                continue
            entries.append({
                "name": entry.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "is_link": entry.is_symlink(),
            })
            total += stat.st_size
        entries.sort(key=lambda e: e["name"])
        return entries, total

    in_files, in_bytes = scan_dir(ws_path / "in")
    out_files, out_bytes = scan_dir(ws_path / "out")

    def scan_mounts(dirpath: Path) -> list[dict]:
        entries = []
        if not dirpath.exists():
            return entries
        for entry in os.scandir(dirpath):
            if entry.name == ".gitkeep":
                continue
            is_link = entry.is_symlink()
            target = str(os.readlink(entry.path)) if is_link else None
            broken = is_link and not Path(entry.path).exists()
            entries.append({
                "name": entry.name,
                "is_link": is_link,
                "target": target,
                "broken": broken,
                "is_dir": entry.is_dir(),
            })
        entries.sort(key=lambda e: e["name"])
        return entries

    mounts = scan_mounts(ws_path / "mounts")

    return JSONResponse({
        "in": in_files,
        "out": out_files,
        "mounts": mounts,
        "in_bytes": in_bytes,
        "out_bytes": out_bytes,
        "max_mb": max_mb,
    })


@router.post("/api/v1/workspace/mounts")
async def api_workspace_mounts_add(request: Request):
    """Create a symlink in workspace/mounts/ pointing to a host path."""
    body = await request.json()
    name = body.get("name", "").strip()
    target = body.get("target", "").strip()

    if not name or not target:
        return JSONResponse({"ok": False, "error": "name and target required"}, status_code=400)
    if "/" in name or ".." in name:
        return JSONResponse({"ok": False, "error": "Invalid name"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent.parent
    mounts_dir = (app_root / ws_path_raw / "mounts").resolve()
    link_path = mounts_dir / name

    if link_path.exists() or link_path.is_symlink():
        return JSONResponse({"ok": False, "error": f"'{name}' already exists"}, status_code=409)

    try:
        os.symlink(target, link_path)
    except OSError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    broken = not link_path.exists()
    return JSONResponse({"ok": True, "name": name, "target": target, "broken": broken})


@router.delete("/api/v1/workspace/mounts/{name}")
async def api_workspace_mounts_delete(name: str):
    """Remove a symlink from workspace/mounts/."""
    if "/" in name or ".." in name:
        return JSONResponse({"ok": False, "error": "Invalid name"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent.parent
    mounts_dir = (app_root / ws_path_raw / "mounts").resolve()
    link_path = mounts_dir / name

    if not link_path.is_symlink() and not link_path.exists():
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    if not link_path.is_symlink():
        return JSONResponse({"ok": False, "error": "Not a symlink — remove manually"}, status_code=400)

    link_path.unlink()
    return JSONResponse({"ok": True})


@router.delete("/api/v1/workspace/out/{filename}")
async def api_workspace_delete(filename: str):
    """Delete a file from workspace/out/. Guards against path traversal."""
    if "/" in filename or ".." in filename:
        return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent.parent
    target = (app_root / ws_path_raw / "out" / filename).resolve()

    out_dir = (app_root / ws_path_raw / "out").resolve()
    if not str(target).startswith(str(out_dir) + os.sep) and target != out_dir:
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)

    if not target.exists():
        return JSONResponse({"ok": False, "error": "File not found"}, status_code=404)

    target.unlink()
    return JSONResponse({"ok": True})


@router.post("/api/v1/workspace/upload")
async def api_workspace_upload(file: UploadFile):
    """Upload a file to workspace/in/. Guards against path traversal."""
    filename = Path(file.filename or "upload").name
    if not filename or ".." in filename:
        return JSONResponse({"ok": False, "error": "Invalid filename"}, status_code=400)

    cfg = get_config()
    ws_path_raw = cfg.get("workspace", {}).get("path", "./workspace")
    app_root = Path(__file__).parent.parent.parent
    in_dir = (app_root / ws_path_raw / "in").resolve()
    in_dir.mkdir(parents=True, exist_ok=True)

    target = (in_dir / filename).resolve()
    if not str(target).startswith(str(in_dir) + os.sep):
        return JSONResponse({"ok": False, "error": "Invalid path"}, status_code=400)

    try:
        content = await file.read()
        target.write_bytes(content)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # Chunk and embed in a background thread — fast upload response even
    # for large PDFs. File is on disk; failed indexing doesn't lose it.
    _st = get_state()
    if _st.vector_store and _st.blob_store:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _index_document, target, _st.vector_store, _st.blob_store)

    return JSONResponse({"ok": True, "name": filename, "size": len(content)})


@router.post("/api/v1/transform/pdf")
async def api_transform_pdf(file: UploadFile):
    """Accept a PDF upload and return text/markdown via the pdf_oxide WASM module."""
    _st = get_state()
    if not _st.proxy:
        return JSONResponse({"ok": False, "error": "proxy not initialized"}, status_code=503)

    filename = Path(file.filename or "upload.pdf").name
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)

    text = await _st.proxy.wasm_runtime.transform_input("pdf_oxide", raw)
    if not text:
        return JSONResponse(
            {"ok": False, "error": "pdf_oxide WASM module not loaded or returned empty"},
            status_code=422,
        )

    return JSONResponse({"ok": True, "text": text, "chars": len(text), "filename": filename})


# ── UI toggle ─────────────────────────────────────────────────────────────

@router.post("/api/v1/web-ui/toggle-vi-mode")
async def toggle_vi_mode():
    """Toggle vi mode in runtime_config.yaml. Returns new state."""
    rt = get_runtime_config()
    current = rt.get("web_ui_vi_mode", False)
    new_val = not current
    ok = update_runtime_config("web_ui_vi_mode", new_val)
    return JSONResponse({"vi_mode": new_val, "ok": ok})
