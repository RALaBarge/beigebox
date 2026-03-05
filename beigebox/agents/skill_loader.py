"""
Agent Skills loader — discovers and parses skills from a configured directory.
Follows the agentskills.io open specification.

Discovery: scans a directory for subdirectories containing SKILL.md.
Metadata (name + description) is loaded at startup, kept lightweight.
Full instructions are loaded on demand when the operator activates a skill.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from SKILL.md content."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end].strip()) or {}
    except Exception:
        meta = {}
    return meta, text[end + 4:].strip()


def load_skills(skills_dir: str | Path) -> list[dict]:
    """
    Scan skills_dir for valid skill directories.
    Returns list of skill dicts with: name, description, path, dir, metadata.
    Skips any skill missing required name or description fields.
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []

    skills = []
    for entry in sorted(skills_path.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(content)
            name = meta.get("name", "").strip()
            description = meta.get("description", "").strip()
            if not name or not description:
                logger.warning("Skill in %s missing name or description — skipped", entry.name)
                continue
            skills.append({
                "name": name,
                "description": description,
                "path": str(skill_md.resolve()),
                "dir": str(entry.resolve()),
                "metadata": meta,
            })
            logger.info("Loaded skill: %s", name)
        except Exception as e:
            logger.warning("Failed to load skill from %s: %s", entry, e)

    return skills


def skills_to_xml(skills: list[dict]) -> str:
    """Generate <available_skills> XML block for injection into the system prompt."""
    if not skills:
        return ""
    lines = ["<available_skills>"]
    for s in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{s['name']}</name>")
        lines.append(f"    <description>{s['description']}</description>")
        lines.append(f"    <location>{s['path']}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)
