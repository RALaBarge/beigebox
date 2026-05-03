"""MCP server bootstrap (skill loader + main MCP + optional Pen/Sec MCP).

Two MCP servers can run in parallel:

  - ``mcp_server`` — primary, mounted at POST ``/mcp``. Always built.
    Loads skills from disk so resources/list + resources/read work.
  - ``security_mcp_server`` — separate registry of offensive-security
    tool wrappers (nmap, nuclei, sqlmap, ffuf, …), mounted at POST
    ``/pen-mcp``. Disabled by default; enable in config under
    ``security_mcp.enabled``.

The skill loader runs here (not in storage or tools) because ``McpServer``
is the only consumer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from beigebox.mcp_server import McpServer

logger = logging.getLogger(__name__)


@dataclass
class McpBundle:
    mcp_server: McpServer
    security_mcp_server: McpServer | None


def build_mcp(cfg: dict, tool_registry) -> McpBundle:
    # Load skills for MCP resources/list + resources/read. Default path is
    # the bundled ``beigebox/skills/`` directory (project_skills_path memo).
    from beigebox.skill_loader import load_skills as _load_skills
    skills_path = cfg.get("skills", {}).get("path") or str(
        Path(__file__).resolve().parent.parent / "skills"
    )
    mcp_skills = _load_skills(skills_path)

    mcp_server = McpServer(tool_registry, skills=mcp_skills)
    logger.info("MCP server: enabled (POST /mcp)")

    # Pen/Sec MCP — separate endpoint exposing offensive-security tool
    # wrappers (nmap, nuclei, sqlmap, ffuf, …). Disabled by default; enable
    # in config.yaml:
    #   security_mcp:
    #     enabled: true
    security_mcp_server: McpServer | None = None
    sec_mcp_cfg = cfg.get("security_mcp", {})
    if sec_mcp_cfg.get("enabled"):
        from beigebox.security_mcp import build_default_registry as _build_sec_registry
        sec_registry = _build_sec_registry()
        # Empty set => expose every registered tool (no progressive disclosure).
        # Right call here: small, focused surface — list them all up front.
        # server_label="pen-mcp" tags every tool_call wire event so /mcp vs
        # /pen-mcp are distinguishable in the Tap event log.
        security_mcp_server = McpServer(
            sec_registry, resident_tools=set(), server_label="pen-mcp"
        )
        logger.info(
            "Pen/Sec MCP server: enabled (POST /pen-mcp) — %d wrappers loaded",
            len(sec_registry.list_tools()),
        )
    else:
        logger.info(
            "Pen/Sec MCP server: disabled "
            "(set security_mcp.enabled: true to enable)"
        )

    return McpBundle(
        mcp_server=mcp_server,
        security_mcp_server=security_mcp_server,
    )


__all__ = ["McpBundle", "build_mcp"]
