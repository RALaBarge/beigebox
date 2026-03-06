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


def skills_fingerprint(skills_dir: str | Path) -> dict[str, float]:
    """
    Return a dict of {skill_md_path: mtime} for all SKILL.md files found
    recursively under skills_dir. Used for hot-reload detection.
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return {}
    result = {}
    for skill_md in skills_path.rglob("SKILL.md"):
        try:
            result[str(skill_md)] = skill_md.stat().st_mtime
        except OSError:
            pass
    return result


def load_skills(skills_dir: str | Path) -> list[dict]:
    """
    Recursively scan skills_dir for SKILL.md files.
    Works with flat layouts (skills/my-skill/SKILL.md) and nested community
    submodule layouts (skills/community/repo/skills/my-skill/SKILL.md).
    Returns list of skill dicts: name, description, path, dir, metadata.
    Skips skills missing required name or description fields.
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []

    skills = []
    for skill_md in sorted(skills_path.rglob("SKILL.md")):
        entry = skill_md.parent
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
    """
    Generate a compact skills list for injection into the system prompt.
    Only includes names — call read_skill(name) to get full instructions.
    Keeps the token footprint small so it doesn't crowd out the context window.
    """
    if not skills:
        return ""
    names = ", ".join(s["name"] for s in skills)
    return (
        f"<available_skills count=\"{len(skills)}\">\n"
        f"{names}\n"
        f"</available_skills>"
    )
