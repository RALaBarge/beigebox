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
        "Read or search skills. "
        "Pass a skill name to get full instructions. "
        "Pass 'list' to see all available skill names. "
        "Pass a partial name or keyword to search (e.g. 'pdf', 'slack', 'web'). "
        "Always call this before following a skill's instructions."
    )

    def __init__(self, skills: list[dict]):
        # Index by name for O(1) lookup at tool-call time.
        self._skills = {s["name"]: s for s in skills}

    def run(self, input_str: str) -> str:
        name = input_str.strip()

        # list mode — return all skill names
        if not name or name.lower() == "list":
            return "Available skills:\n" + "\n".join(sorted(self._skills))

        # exact match
        skill = self._skills.get(name)

        # fuzzy fallback — substring search on name
        if not skill:
            matches = [k for k in self._skills if name.lower() in k.lower()]
            if len(matches) == 1:
                skill = self._skills[matches[0]]
            elif matches:
                return f"Multiple skills match {name!r}: {', '.join(sorted(matches))}"
            else:
                return f"No skill found for {name!r}. Call read_skill('list') to see all available skills."

        skill_md_path = Path(skill["path"])
        if not skill_md_path.exists():
            return f"SKILL.md not found at {skill['path']}"

        try:
            content = skill_md_path.read_text(encoding="utf-8")
            result = content

            # List bundled scripts so the caller knows which shell commands
            # are bundled with this skill.
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
