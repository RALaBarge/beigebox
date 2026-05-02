"""MCP server endpoints + Toolbox UI endpoints.

Extracted from beigebox/main.py (B-6). Includes:
- /mcp + /pen-mcp — JSON-RPC 2.0 MCP server endpoints (regular + security)
- /api/v1/mcp/info — MCP server metadata (resident tools, skill count)
- /api/v1/toolbox/tools — list tools with metadata
- /api/v1/toolbox/tools/{name}/source (GET + POST) — tool source view/edit
- /api/v1/toolbox/skills — list skills
- /api/v1/toolbox/skills/source (GET + POST) — skill source view/edit
- /api/v1/toolbox/validate — dry-run syntax check
- /api/v1/toolbox/tools/new + /skills/new — create new plugin / skill

Helpers:
- _read_mcp_body — size-cap and parse JSON-RPC body
- _toolbox_edits_enabled — config flag
- _toolbox_tools_dir / _plugins_dir / _skills_dir — path resolvers
- _is_valid_tool_name / _is_valid_skill_name — name validation
- _resolve_tool_path / _resolve_skill_path — path-traversal-safe resolution
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from beigebox.config import get_config
from beigebox.routers._shared import _require_admin
from beigebox.state import get_state


logger = logging.getLogger(__name__)


router = APIRouter()


# ── MCP body parsing helper + endpoints ──────────────────────────────────

_MCP_REQUEST_BODY_LIMIT = 1_048_576  # 1 MiB cap before json.loads


async def _read_mcp_body(request: Request) -> tuple[dict | None, JSONResponse | None]:
    """Size-cap and parse a JSON-RPC request body.

    Returns (parsed_body, None) on success, (None, error_response) otherwise.
    Cap enforced on raw bytes BEFORE json.loads so a deeply-nested or
    oversized payload can't exhaust CPU/memory in the parser.
    """
    too_large = JSONResponse(
        {"jsonrpc": "2.0", "id": None,
         "error": {"code": -32600, "message": "Request too large"}},
        status_code=413,
    )
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MCP_REQUEST_BODY_LIMIT:
                return None, too_large
        except ValueError:
            pass
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _MCP_REQUEST_BODY_LIMIT:
            return None, too_large
    try:
        return json.loads(bytes(raw)), None
    except Exception as e:
        logger.debug("MCP parse error: %s", str(e)[:200])
        return None, JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """MCP (Model Context Protocol) server — Streamable HTTP transport.

    Accepts JSON-RPC 2.0 requests and dispatches to BeigeBox's tool registry.
    Supported methods: initialize, tools/list, tools/call.
    """
    _st = get_state()
    if _st.mcp_server is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "MCP server not initialised"}},
            status_code=503,
        )
    body, err = await _read_mcp_body(request)
    if err is not None:
        return err
    result = await _st.mcp_server.handle(body)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result)


@router.post("/pen-mcp")
async def pen_mcp_endpoint(request: Request):
    """Pen/Sec MCP server — separate JSON-RPC endpoint exposing offensive-
    security tool wrappers (nmap, nuclei, sqlmap, ffuf, ...).
    """
    _st = get_state()
    if _st.security_mcp_server is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32603,
                       "message": "Pen/Sec MCP server disabled (set security_mcp.enabled in config)"}},
            status_code=503,
        )
    body, err = await _read_mcp_body(request)
    if err is not None:
        return err
    result = await _st.security_mcp_server.handle(body)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result)


# ── Toolbox helpers (path resolution + name validation) ──────────────────

_TOOLBOX_PROTECTED_TOOLS = {"registry", "plugin_loader", "__init__"}


def _toolbox_edits_enabled() -> bool:
    return bool(get_config().get("toolbox", {}).get("edits_enabled", False))


def _toolbox_tools_dir() -> Path:
    # beigebox/routers/tools.py → beigebox/ → tools/
    return (Path(__file__).parent.parent / "tools").resolve()


def _toolbox_plugins_dir() -> Path:
    cfg_path = get_config().get("tools", {}).get("plugins", {}).get("path") or "./plugins"
    p = Path(cfg_path)
    if p.is_absolute():
        return p.resolve()
    # Relative to repo root: beigebox/routers/tools.py → repo root is two parents up
    return (Path(__file__).parent.parent.parent / p).resolve()


def _toolbox_skills_dir() -> Path:
    override = get_config().get("skills", {}).get("path")
    if override:
        return Path(override).resolve()
    return (Path(__file__).parent.parent / "skills").resolve()


def _is_valid_tool_name(name: str) -> bool:
    """Lowercase letter-first, alnum + underscore, ≤40 chars."""
    if not name or len(name) > 40:
        return False
    if not (name[0].isalpha() and name[0].islower()):
        return False
    return all(c.islower() or c.isdigit() or c == "_" for c in name)


def _is_valid_skill_name(name: str) -> bool:
    """Lowercase letter-first, alnum + underscore + hyphen, ≤40 chars."""
    if not name or len(name) > 40:
        return False
    if not (name[0].isalpha() and name[0].islower()):
        return False
    return all(c.islower() or c.isdigit() or c in "_-" for c in name)


def _resolve_tool_path(name: str) -> Path | None:
    """Resolve a tool source file path; None on invalid/protected/escape."""
    if not name or name.startswith("_") or name in _TOOLBOX_PROTECTED_TOOLS:
        return None
    if not all(c.isalnum() or c == "_" for c in name):
        return None
    plugins_dir = _toolbox_plugins_dir()
    plugin_target = (plugins_dir / f"{name}.py").resolve()
    try:
        plugin_target.relative_to(plugins_dir)
        if plugin_target.exists():
            return plugin_target
    except ValueError:
        pass
    tools_dir = _toolbox_tools_dir()
    target = (tools_dir / f"{name}.py").resolve()
    try:
        target.relative_to(tools_dir)
    except ValueError:
        return None
    return target


def _resolve_skill_path(raw_path: str) -> Path | None:
    """Resolve a SKILL.md path; ensures it stays under the skills dir and is .md."""
    if not raw_path:
        return None
    skills_dir = _toolbox_skills_dir()
    try:
        target = Path(raw_path).resolve()
        target.relative_to(skills_dir)
    except (ValueError, OSError):
        return None
    if target.suffix.lower() != ".md":
        return None
    return target


# ── MCP info + Toolbox endpoints ─────────────────────────────────────────

@router.get("/api/v1/mcp/info")
async def api_mcp_info():
    """MCP server info: endpoint, resident tools, counts."""
    _st = get_state()
    try:
        from beigebox.mcp_server import _DEFAULT_RESIDENT_TOOLS
        resident = sorted(_DEFAULT_RESIDENT_TOOLS)
    except Exception:
        resident = []
    tool_names = _st.tool_registry.list_tools() if _st.tool_registry else []
    skill_count = 0
    try:
        from beigebox.skill_loader import load_skills
        skill_count = len(load_skills(_toolbox_skills_dir()))
    except Exception:
        pass
    return JSONResponse({
        "endpoint": "/mcp",
        "transport": "HTTP POST (JSON-RPC 2.0)",
        "resident_tools": resident,
        "registered_tool_count": len(tool_names),
        "skill_count": skill_count,
        "edits_enabled": _toolbox_edits_enabled(),
    })


@router.get("/api/v1/toolbox/tools")
async def api_toolbox_tools():
    """List tools with metadata for the Toolbox UI."""
    _st = get_state()
    tools_cfg = get_config().get("tools", {}) or {}
    items = []
    if _st.tool_registry:
        for name in _st.tool_registry.list_tools():
            tool = _st.tool_registry.get(name)
            description = getattr(tool, "description", "") if tool else ""
            tags = getattr(tool, "capability_tags", None) if tool else None
            risk = getattr(tool, "capability_risk", None) if tool else None
            tool_cfg = tools_cfg.get(name)
            enabled = tool_cfg.get("enabled") if isinstance(tool_cfg, dict) else None
            path = _resolve_tool_path(name)
            items.append({
                "name": name,
                "description": description or "",
                "enabled": enabled,
                "capability_tags": list(tags) if tags else [],
                "capability_risk": risk or "",
                "source_path": str(path) if path else "",
                "editable": path is not None and path.exists(),
            })
    return JSONResponse({
        "tools": items,
        "edits_enabled": _toolbox_edits_enabled(),
    })


@router.get("/api/v1/toolbox/tools/{name}/source")
async def api_toolbox_tool_source(name: str):
    path = _resolve_tool_path(name)
    if not path:
        return JSONResponse({"error": "invalid or protected tool name"}, status_code=400)
    if not path.exists():
        return JSONResponse({"error": "tool source not found"}, status_code=404)
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)
    return JSONResponse({
        "name": name,
        "path": str(path),
        "content": content,
        "length": len(content),
        "editable": _toolbox_edits_enabled(),
    })


@router.post("/api/v1/toolbox/tools/{name}/source")
async def api_toolbox_tool_save(name: str, request: Request):
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    path = _resolve_tool_path(name)
    if not path:
        return JSONResponse({"error": "invalid or protected tool name"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    try:
        compile(content, str(path), "exec")
    except SyntaxError as e:
        return JSONResponse(
            {"error": f"SyntaxError: {e.msg} at line {e.lineno}"},
            status_code=400,
        )
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "path": str(path),
        "length": len(content),
        "requires_restart": True,
    })


@router.get("/api/v1/toolbox/skills")
async def api_toolbox_skills():
    from beigebox.skill_loader import load_skills
    skills_dir = _toolbox_skills_dir()
    try:
        skills = load_skills(skills_dir)
    except Exception as e:
        logger.warning("load_skills failed: %s", e)
        skills = []
    items = [
        {
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "path": s.get("path", ""),
            "dir": s.get("dir", ""),
        }
        for s in skills
    ]
    return JSONResponse({
        "skills": items,
        "skills_dir": str(skills_dir),
        "edits_enabled": _toolbox_edits_enabled(),
    })


@router.get("/api/v1/toolbox/skills/source")
async def api_toolbox_skill_source(path: str):
    target = _resolve_skill_path(path)
    if not target:
        return JSONResponse({"error": "invalid skill path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "skill source not found"}, status_code=404)
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)
    return JSONResponse({
        "path": str(target),
        "content": content,
        "length": len(content),
        "editable": _toolbox_edits_enabled(),
    })


@router.post("/api/v1/toolbox/validate")
async def api_toolbox_validate(request: Request):
    """Dry-run syntax check for the Toolbox editor. No side effects."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    kind = body.get("kind", "")
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"valid": False, "error": "content must be a string"})
    if kind == "tool":
        try:
            compile(content, "<toolbox-validate>", "exec")
            return JSONResponse({"valid": True})
        except SyntaxError as e:
            return JSONResponse({"valid": False, "error": e.msg or "SyntaxError", "line": e.lineno})
    if kind == "skill":
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end == -1:
                return JSONResponse({
                    "valid": False,
                    "error": "unterminated frontmatter (missing closing '---')",
                    "line": 1,
                })
            import yaml
            try:
                meta = yaml.safe_load(content[3:end].strip()) or {}
            except yaml.YAMLError as e:
                line = getattr(getattr(e, "problem_mark", None), "line", None)
                first = str(e).splitlines()[0] if str(e) else "YAML error"
                return JSONResponse({
                    "valid": False,
                    "error": first,
                    "line": (line + 1) if line is not None else None,
                })
            if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
                return JSONResponse({
                    "valid": False,
                    "error": "frontmatter missing required 'name' or 'description'",
                })
        return JSONResponse({"valid": True})
    return JSONResponse({"valid": True})


@router.post("/api/v1/toolbox/skills/source")
async def api_toolbox_skill_save(request: Request):
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    target = _resolve_skill_path(body.get("path", ""))
    if not target:
        return JSONResponse({"error": "invalid skill path"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "path": str(target),
        "length": len(content),
        "requires_restart": True,
    })


@router.post("/api/v1/toolbox/tools/new")
async def api_toolbox_tool_new(request: Request):
    """Create a new plugin tool stub at plugins/<name>.py."""
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not _is_valid_tool_name(name):
        return JSONResponse(
            {"error": "name must be lowercase letters/digits/underscores, starting with a letter (≤40 chars)"},
            status_code=400,
        )
    if name in _TOOLBOX_PROTECTED_TOOLS:
        return JSONResponse({"error": "reserved name"}, status_code=400)
    _st = get_state()
    existing = set(_st.tool_registry.list_tools()) if _st.tool_registry else set()
    if name in existing:
        return JSONResponse({"error": f"tool '{name}' already registered"}, status_code=409)
    plugins_dir = _toolbox_plugins_dir()
    plugin_target = (plugins_dir / f"{name}.py").resolve()
    try:
        plugin_target.relative_to(plugins_dir)
    except ValueError:
        return JSONResponse({"error": "invalid target path"}, status_code=400)
    if plugin_target.exists():
        return JSONResponse({"error": f"file already exists: {plugin_target.name}"}, status_code=409)
    tools_dir = _toolbox_tools_dir()
    if (tools_dir / f"{name}.py").exists():
        return JSONResponse({"error": f"built-in tool source '{name}.py' exists"}, status_code=409)
    class_name = "".join(p.capitalize() for p in name.split("_") if p) + "Tool"
    stub = (
        f'"""\n{name} — user-added plugin tool.\n\n'
        'Created from the Toolbox UI. Implement .run(input: str) -> str and\n'
        'restart BeigeBox to register this plugin.\n'
        'See plugins/README.md for the full plugin contract.\n'
        '"""\n\n'
        f'PLUGIN_NAME = "{name}"\n\n\n'
        f'class {class_name}:\n'
        f'    description = "TODO: describe what {name} does"\n\n'
        '    def __init__(self):\n'
        '        pass\n\n'
        '    def run(self, input_text: str) -> str:\n'
        f'        return f"{name} received: {{input_text}}"\n'
    )
    plugins_enabled = bool(
        get_config().get("tools", {}).get("plugins", {}).get("enabled", False)
    )
    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_target.write_text(stub, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "name": name,
        "kind": "tool",
        "path": str(plugin_target),
        "length": len(stub),
        "plugins_enabled": plugins_enabled,
        "requires_restart": True,
    })


@router.post("/api/v1/toolbox/skills/new")
async def api_toolbox_skill_new(request: Request):
    """Create a new skill at beigebox/skills/<name>/SKILL.md."""
    if (denied := _require_admin(request)) is not None:
        return denied
    if not _toolbox_edits_enabled():
        return JSONResponse(
            {"error": "toolbox edits disabled — set toolbox.edits_enabled: true in config.yaml"},
            status_code=403,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not _is_valid_skill_name(name):
        return JSONResponse(
            {"error": "name must be lowercase letters/digits/underscores/hyphens, starting with a letter (≤40 chars)"},
            status_code=400,
        )
    skills_dir = _toolbox_skills_dir()
    target_dir = (skills_dir / name).resolve()
    try:
        target_dir.relative_to(skills_dir)
    except ValueError:
        return JSONResponse({"error": "invalid target path"}, status_code=400)
    if target_dir.exists():
        return JSONResponse({"error": f"skill '{name}' already exists"}, status_code=409)
    target = target_dir / "SKILL.md"
    stub = (
        "---\n"
        f"name: {name}\n"
        f"description: TODO — one-line description of what this skill does\n"
        "---\n\n"
        f"# {name}\n\n"
        "TODO: replace this body with instructions the agent should follow when\n"
        "this skill is activated. Skills are read on demand via `read_skill('"
        f"{name}')`.\n"
    )
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(stub, encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"write failed: {e}"}, status_code=500)
    return JSONResponse({
        "ok": True,
        "name": name,
        "kind": "skill",
        "path": str(target),
        "length": len(stub),
        "requires_restart": True,
    })
