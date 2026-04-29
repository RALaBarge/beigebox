#!/usr/bin/env bash
# scaffold.sh — create a new skill directory under beigebox/skills/<name>/
# matching the house style. Idempotent-refusing: will not overwrite existing
# skill directories.
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: scaffold.sh [--with-script] [--skills-dir DIR] <skill-name>

Creates beigebox/skills/<skill-name>/SKILL.md with stubbed frontmatter and
the standard section layout.

Options:
  --with-script    Also create scripts/<skill-name>.sh as an executable stub
  --skills-dir DIR Override target directory (default: auto-detect from script
                   location, falling back to ./beigebox/skills/)
  -h, --help       Show this help

Skill names must be kebab-case ([a-z0-9-]+).
EOF
}

with_script=0
skills_dir=""
name=""

while [ $# -gt 0 ]; do
  case "$1" in
    --with-script) with_script=1; shift ;;
    --skills-dir)  skills_dir="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "unknown flag: $1" >&2; usage; exit 2 ;;
    *)  if [ -z "$name" ]; then name="$1"; shift; else echo "unexpected argument: $1" >&2; usage; exit 2; fi ;;
  esac
done

if [ -z "$name" ]; then usage; exit 2; fi
if ! [[ "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "skill name must be kebab-case (got: $name)" >&2
  exit 2
fi

if [ -z "$skills_dir" ]; then
  # script lives at beigebox/skills/make-skill/scripts/scaffold.sh
  # so beigebox/skills/ is two levels up
  here="$(cd "$(dirname "$0")" && pwd)"
  guess="$(cd "$here/../.." && pwd)"
  if [ -d "$guess" ] && [ "$(basename "$guess")" = "skills" ]; then
    skills_dir="$guess"
  else
    skills_dir="./beigebox/skills"
  fi
fi

target="$skills_dir/$name"
if [ -e "$target" ]; then
  echo "refusing to overwrite existing: $target" >&2
  exit 1
fi

mkdir -p "$target"
skill_md="$target/SKILL.md"

cat > "$skill_md" <<EOF
---
name: $name
version: 1
description: Use when the user <trigger phrase 1>, <trigger phrase 2>, or <trigger phrase 3>. <What it does in one sentence>. <Scope: OS, requirements, target shapes>. <Output forms.>
---

# $name

<One-paragraph lede: what this skill does, what it produces, and how it differs from neighbor skills.>

## When to invoke

- User asks "<concrete phrase>"
- User wants <concrete situation>
- You need <concrete situation> before <doing X>

## Usage

\`\`\`bash
# most common form
scripts/$name.sh

# <variant with one-line rationale>
scripts/$name.sh --json
\`\`\`

## Requirements

- <tool> (required; \`apt install <pkg>\` or \`brew install <pkg>\`)

## Behavior notes

- <timeout / fail-fast behavior>
- <known limitations>
EOF

echo "created $skill_md"

if [ "$with_script" -eq 1 ]; then
  mkdir -p "$target/scripts"
  script_path="$target/scripts/$name.sh"
  cat > "$script_path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<USAGE
Usage: $(basename "$0") [--json] [args...]

<one-line description>
USAGE
}

json=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json)    json=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done

# TODO: implement
echo "not yet implemented" >&2
exit 1
EOF
  chmod +x "$script_path"
  echo "created $script_path"
fi
