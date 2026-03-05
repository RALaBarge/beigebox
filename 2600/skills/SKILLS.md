# BeigeBox Agent Skills

Skills live here. The BeigeBox operator agent discovers and hot-reloads them
automatically — drop a folder in, send a message to the operator, it's live.

Format: [agentskills.io](https://agentskills.io) open standard.
Compatible with Claude Code, Cursor, Gemini CLI, Codex, OpenHands, and others.

---

## Directory layout

```
2600/skills/
├── SKILLS.md                        ← this file
├── beigebox-admin/                  ← first-party BeigeBox admin skill
│   ├── SKILL.md
│   └── references/
│       └── api-endpoints.md
└── community/                       ← git submodules (read-only, upstream maintained)
    ├── anthropics-skills/           ← Anthropic official skills (Apache-2.0)
    │   └── skills/
    │       ├── algorithmic-art/
    │       ├── canvas-design/
    │       ├── claude-api/
    │       ├── mcp-builder/
    │       ├── webapp-testing/
    │       └── ... (17 total)
    └── scientific-skills/           ← K-Dense scientific skills (MIT)
        └── scientific-skills/
            ├── alphafold-database/
            ├── chembl-database/
            ├── biopython/
            ├── pytorch-lightning/
            └── ... (170 total)
```

---

## Initial setup (first clone)

The community submodules are not populated by a plain `git clone`. Initialize them:

```bash
# Option A — clone with submodules from the start
git clone --recursive https://github.com/RALaBarge/beigebox.git

# Option B — already cloned, initialize after the fact
git submodule update --init --recursive
```

---

## Keeping community skills up to date

Submodules are pinned to the commit they were added at. To pull the latest
from both upstream skill repos:

```bash
# Pull latest from all submodules + update the pin in BeigeBox
git submodule update --remote --merge
git add 2600/skills/community
git commit -m "Update community skills submodules"
git push
```

To do this automatically on every `git pull` (recommended for your own machine):

```bash
git config submodule.recurse true
```

After that one-time config, `git pull` updates everything.

---

## Adding your own skill

Skills are just a folder with a `SKILL.md` file. Minimal structure:

```
2600/skills/my-skill/
└── SKILL.md
```

`SKILL.md` requires YAML frontmatter:

```yaml
---
name: my-skill
description: What this skill does and when to use it. Be specific — the operator
  reads this to decide whether to activate the skill. Max 1024 chars.
---

# My Skill

Step-by-step instructions here. The operator loads this full file when it decides
the skill is relevant. Keep it under 500 lines; move long reference material to
references/ files and link to them.
```

Full structure with optional extras:

```
2600/skills/my-skill/
├── SKILL.md              ← required
├── scripts/              ← executable scripts the operator can run
│   └── process.py
├── references/           ← detailed docs loaded on demand
│   └── REFERENCE.md
└── assets/               ← templates, schemas, static data
    └── template.yaml
```

Rules for `name`:
- Lowercase letters, numbers, and hyphens only
- No leading/trailing hyphens, no consecutive hyphens (`--`)
- Max 64 characters
- Must match the parent directory name

---

## Hot reload

No restart needed. The operator checks for changes on every message. Edit a
`SKILL.md`, add a new skill folder, or delete one — it takes effect immediately.

---

## Community skill sources

| Source | Skills | License | Notes |
|---|---|---|---|
| [anthropics/skills](https://github.com/anthropics/skills) | 17 | Apache-2.0 | Official Anthropic — creative, dev, enterprise, MCP builder |
| [K-Dense-AI/claude-scientific-skills](https://github.com/K-Dense-AI/claude-scientific-skills) | 170 | MIT | Bioinformatics, drug discovery, ML, 250+ databases |

To add another community repo as a submodule:

```bash
git submodule add https://github.com/org/repo 2600/skills/community/repo-name
git add .gitmodules 2600/skills/community/repo-name
git commit -m "Add community skills: repo-name"
```
