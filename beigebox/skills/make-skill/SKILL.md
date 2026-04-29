---
name: make-skill
version: 2
description: Use when the user says "make a skill that …", "write a skill for …", "I need a skill that does …", "package this as a skill", or "automate this as a skill" — i.e. wants to turn a recurring task into a new entry under `beigebox/skills/<name>/`. Produces a skill directory in the house style of `services-inventory` and `host-audit`: YAML frontmatter with a `Use when …` description, a `When to invoke` trigger list, a `Usage` block, and (when the skill does deterministic work) a `scripts/` dir with an executable. Run `scripts/scaffold.sh <name>` to stub the layout.
---

# make-skill

Authoring guide for new skills in this repo. Skills live at `beigebox/skills/<name>/` and are loaded by name. The reference implementations are `beigebox/skills/services-inventory/` and `beigebox/skills/host-audit/` — when in doubt, copy their shape.

## When to invoke

- User says "make a skill", "write a skill", "scaffold a skill", "add a skill that…"
- User describes a recurring task ("every time someone asks X, run Y") that should be packaged as a skill rather than re-derived each conversation
- You're about to write the same multi-step procedure into a SKILL.md by hand — stop and use `scripts/scaffold.sh` first

## Deriving the trigger phrases

The `description` field is a transcript of *user utterances*, not a summary of the skill. Compare the two real skills' descriptions to the kind of thing a user actually types: `services-inventory` lists `what services/containers/VMs are running` and `how to connect to something`, `host-audit` lists `audit / profile / fingerprint one or more machines`.

Concrete recipe:

1. Ask the user (or imagine) the 3–5 phrasings they'd most likely use to surface this need. Write them down verbatim.
2. Pull out the **verbs** (`audit`, `profile`, `inventory`, `survey`) and the **nouns** (`hosts`, `containers`, `services`, `endpoints`).
3. Stitch them into the first sentence after `Use when`. Synonyms with `/` (`audit / profile / fingerprint`) widen recall without bloating the description.
4. Add scope/constraint facts the selector should weigh: target OS, required tools, target shapes, output formats. These let the agent skip the skill cleanly when they don't apply.

## Process

1. **Confirm the trigger phrase.** What words/contexts should make the agent pick this skill? This becomes the `description` field. If you can't list 3+ concrete triggers, the skill isn't focused enough — split it.
2. **Decide if it needs a script.** If the work is deterministic (probing, formatting, validating), put it in `scripts/` and have SKILL.md call it. If it's pure guidance for the model, SKILL.md alone is fine.
3. **Scaffold.** `scripts/scaffold.sh <skill-name>` creates the directory, stubs SKILL.md with the right frontmatter, and (with `--with-script`) drops an executable stub.
4. **Fill in.** Use the template below.
5. **Sanity-check** against the rules in *Conventions* before declaring done.

## Conventions

### Frontmatter (required)

```yaml
---
name: <kebab-case-name>             # matches the directory name
version: 1                           # integer; bump on each shipped revision
description: Use when <trigger>. <One sentence on what it does>. <Constraints/scope.>
---
```

- The `description` is **the only thing the model sees at skill-selection time.** Make every word earn its place.
- Lead with `Use when` — this is the house pattern in `services-inventory` and `host-audit`. Don't write "This skill helps with…".
- Pack concrete triggers (verbs + nouns the user is likely to say) into the first sentence. The selector matches on these.
- Note hard scope/constraint facts that would make the agent skip the skill if they don't apply (OS, required tools, target shape).
- Keep under ~1024 chars. If you need more, the skill is doing too much.
- `version` is a plain integer starting at `1`. Bump it whenever the skill's behavior, scaffold output, or guidance changes in a way a consumer would notice (new section, removed flag, renamed script, added or relaxed rule, new anti-pattern). Pure typo fixes don't bump.

### Body sections (in this order)

1. **`# <name>`** — the H1 matches the `name` field exactly.
2. **Lede paragraph** — 1–3 sentences. What the skill does and how it differs from neighbors. If it has a script, name it here (`Runs scripts/foo.sh ...`).
3. **`## When to invoke`** — bullet list of trigger situations. These overlap with the `description` but can be longer/more concrete since the model is already reading the body.
4. **`## Usage`** — annotated bash code block(s). Show the most common form first, then variants (flags, remote vs local, output formats). Comments on the line above each command explain *why you'd reach for this form*.
5. **`## Requirements`** — tools that must exist for the skill to run. Be specific (`jq`, `ssh`, `sshpass`).
6. **`## Behavior notes` / `## Safety & limits`** — timeouts, things it skips, sudo boundaries, gotchas.

Add domain-specific sections only when the skill has them — `services-inventory` adds `## Output fields`, `host-audit` adds `## Snapshots & schema` and `## Target forms`. Don't preallocate placeholders for sections you don't need.

Don't pad with sections that don't apply. `services-inventory` skips "Snapshots"; `host-audit` skips "Output fields" because it has its own schema doc. Match content to need.

### Scripts

- Live in `scripts/`. Executable bit set. Shebang lines.
- Bash for probing/aggregation; Python only when the logic genuinely needs it.
- Each probe step should have a timeout. A dead host or missing CLI must fail fast and be skipped silently — partial output is more useful than no output.
- Output two formats when the skill produces structured data: a default human form and `--json` for piping. `host-audit` adds a third (`--format claude`) for paste-into-CLAUDE.md sections; that's a useful pattern when the data is meant to be reviewed and pasted.
- If the script accepts targets, support `local`, SSH alias, `user@host`, and (when relevant) `user:password@host` via `sshpass`. Label form (`label=TARGET`) is helpful for multi-host runs.

### Sidecar files

- `schema.json` — when the skill writes snapshots or structured output, document the shape. Version it (`schema_version: "1"`).
- `*.example` — sample config files (e.g. `targets.example`, `hosts.example`). Real configs go under `~/.config/beigebox/` or `$XDG_STATE_HOME/beigebox/`.
- Don't write a `REFERENCE.md` unless the SKILL.md genuinely tops 150 lines and has a clearly separable advanced section. Most skills don't need one.

## Template

```md
---
name: <kebab-case-name>
version: 1
description: Use when the user <trigger phrase 1, trigger phrase 2, trigger phrase 3>. <What it does in one sentence>. <Scope: OS, requirements, target shapes>. <Output forms.>
---

# <kebab-case-name>

`scripts/<kebab-case-name>.sh` — <one-sentence pitch>. <How this skill differs from any neighbor skill that sounds similar>.

## When to invoke

- User asks "<concrete phrase 1>"
- User wants <concrete situation 2>
- You need <concrete situation 3> before <doing X>

## Usage

```bash
# most common form
scripts/<kebab-case-name>.sh

# json output (use this when piping / reasoning over fields)
scripts/<kebab-case-name>.sh --json

# <other variants with one-line rationale each>
```

## Requirements

- `<tool>` (required; `apt install <pkg>` or `brew install <pkg>`)
- <other prerequisites>

## Behavior notes

- <timeout / fail-fast behavior>
- <silent skips>
- <known limitations>
```

**Script naming.** Default to a single entrypoint at `scripts/<skill-name>.sh` (matches `inventory.sh`, `audit.sh`). Skills with multiple scripts can use whatever names make sense (`scripts/collect.sh`, `scripts/format.sh`) — list each one in the Usage block.

## Anti-patterns

- **Generic descriptions.** "Helps with files." gives the selector no signal. Compare to `host-audit`'s description, which lists OS support, target forms, and output formats.
- **Section padding.** Don't add a `## Quick start` / `## Workflows` / `## Advanced features` skeleton just because some external template prescribes it. Use the sections this skill actually needs.
- **Inline 200-line bash heredocs.** If a SKILL.md contains the script, move it to `scripts/`. SKILL.md describes; scripts execute.
- **Stateful prose.** No "as of Apr 2026", no "we recently added X". Skills are read by future-you and decay quickly when written like changelog entries.
- **Wrapping built-ins for the sake of it.** If the answer is `docker ps`, just teach the model to run `docker ps`. A skill earns its keep by adding aggregation, normalization, or remote dispatch.

## Validation

After drafting, walk this list:

- [ ] `name` (frontmatter) === directory name === H1 in body
- [ ] `version` (frontmatter) is an integer ≥ 1
- [ ] `description` starts with "Use when" and lists ≥ 3 concrete trigger phrases
- [ ] `description` ≤ ~1024 chars
- [ ] Skill name doesn't collide with any existing entry in `beigebox/skills/` or any tool registry key in `beigebox/tools/registry.py`
- [ ] Skill scope fits in one paragraph — if the lede needs two paragraphs to explain what the skill does, split it
- [ ] At least one `## Usage` example is the *common* case, not an edge case
- [ ] Every script invoked from SKILL.md exists in `scripts/` and is executable
- [ ] `Requirements` lists every binary the script calls (`jq`, `ssh`, etc.)
- [ ] No date stamps or "recently"-style language in the body
- [ ] None of the anti-patterns above are present
- [ ] Skill answers a question that wasn't already answered by another skill in `beigebox/skills/`
