"""
Skill reader tool — loads full Agent Skills instructions on demand.

The operator receives skill metadata (name + description) at startup.
When it decides a skill is relevant, it calls this tool with the skill
name to get the complete SKILL.md content plus a listing of any bundled
scripts and reference files.
"""
from __future__ import annotations

from pathlib import Path


class SkillReaderTool:
    description = (
        "Read a skill's full instructions. "
        "Input: the skill name (from available_skills). "
        "Returns the complete SKILL.md content and lists any bundled scripts "
        "and reference files. Call this before following a skill's instructions."
    )

    def __init__(self, skills: list[dict]):
        # Index by name for O(1) lookup at tool-call time.
        self._skills = {s["name"]: s for s in skills}

    def run(self, input_str: str) -> str:
        name = input_str.strip()
        skill = self._skills.get(name)
        if not skill:
            available = ", ".join(sorted(self._skills)) or "none"
            return f"Unknown skill: {name!r}. Available: {available}"

        skill_md_path = Path(skill["path"])
        if not skill_md_path.exists():
            return f"SKILL.md not found at {skill['path']}"

        try:
            content = skill_md_path.read_text(encoding="utf-8")
            result = content

            # List bundled scripts so the operator knows which shell commands
            # it can run via the python_interpreter or shell tool.
            scripts_dir = skill_md_path.parent / "scripts"
            if scripts_dir.exists():
                scripts = sorted(f.name for f in scripts_dir.iterdir() if f.is_file())
                if scripts:
                    result += f"\n\n---\nScripts available in {scripts_dir}:\n"
                    result += "\n".join(f"  {s}" for s in scripts)

            # List reference files (e.g. example configs, templates).
            refs_dir = skill_md_path.parent / "references"
            if refs_dir.exists():
                refs = sorted(f.name for f in refs_dir.iterdir() if f.is_file())
                if refs:
                    result += f"\n\nReference files in {refs_dir}:\n"
                    result += "\n".join(f"  {r}" for r in refs)

            return result
        except Exception as e:
            return f"Error reading skill {name!r}: {e}"
