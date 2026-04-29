---
name: make-tool
version: 2
description: Use when the user says "make a tool that …", "write a tool for …", "I need a tool that does …", "add a tool to BeigeBox", or describes a capability the decision LLM should be able to invoke (search, fetch, lookup, parse, automate). Produces a new tool module under `beigebox/tools/<name>.py` plus the registry hook in `beigebox/tools/registry.py` and a `tools.<name>:` config block in `config.yaml`. Tools are Python classes with a `description` attribute and a single `run(self, input_str: str) -> str` entrypoint, returning user-facing strings (errors included) — they never raise to the caller.
---

# make-tool

Authoring guide for new tools in `beigebox/tools/`. Tools are Python classes the decision LLM can route requests to. Reference implementations: `beigebox/tools/calculator.py` (no-deps, default-enabled), `beigebox/tools/connection_tool.py` (registry-injected, JSON input), `beigebox/tools/atlassian.py` (env-driven creds, multi-action). When in doubt, copy their shape.

## When to invoke

- User says "make a tool", "write a tool", "add a tool to BeigeBox", "I want the LLM to be able to do X"
- User describes a capability the agent should reach for routinely (a new external API, a parser, a deterministic helper) rather than re-deriving each turn
- You're about to inline 80 lines of API client code into a prompt — stop, package it as a tool

## Process

1. **Confirm the trigger phrase and input shape.** What does the LLM call it with — a natural-language string, a JSON object, or a structured action? This decides whether `run()` parses JSON or treats the input as raw text.
2. **Decide enablement default.** Pure-stdlib / pure-Python with no external deps → default-enabled. Anything that needs creds, a daemon, a port, or a paid API → default-disabled.
3. **Scaffold.** `scripts/scaffold.sh <tool-name>` writes the module skeleton and a test stub, then **prints** the registry import line and `config.yaml` block to stdout for you to paste — it does not edit `registry.py` or `config.yaml`. Both are manual steps (#4 and #5).
4. **Wire the registry.** Paste the printed import + the conditional `if cfg.get("enabled", <default>): self.tools["<key>"] = <Class>(...)` block into `beigebox/tools/registry.py`. A tool that exists on disk but isn't registered is unreachable.
5. **Add the config block.** Paste the printed `tools.<name>:` section into `config.yaml` and adjust defaults to taste.
6. **Test.** Add a case to `tests/test_tools.py` (or a dedicated `tests/test_<name>_tool.py` for anything substantial). At minimum: instantiation + one happy-path `run()` call.

## Conventions

### Description and version: the tool's metadata

Tools don't use YAML frontmatter (they're Python), but they carry the same metadata in two module-level constants:

- The `description` *class attribute* plays the role of a skill's `description` field — it's what the decision LLM reads when deciding whether to route to this tool. Treat it with the same care.
- The `__version__` *module attribute* (integer, starts at `1`) plays the role of a skill's `version` frontmatter — bump it when the tool's behavior, input shape, or output format changes in a way a caller would notice. Pure refactors don't bump.

```python
class FooTool:
    description = (
        "One-sentence pitch of what it does. "
        "Input shape: <natural language | JSON {\"k\": \"v\"} | action verb>. "
        "Example: {\"tool\": \"foo\", \"input\": \"sample call\"}."
    )
```

- Lead with the verb the LLM should match against ("Search the web", "Evaluate a math expression", "Make authenticated API calls").
- Always include an input-shape example. If the input is JSON, show the JSON. If it's a free-form string, give a phrasing example.
- Keep under ~300 chars. The decision LLM scans many tool descriptions per turn.

### Module shape

```python
"""
<ToolName> — one-line pitch.

Why it exists / what gap it fills.

Examples the decision LLM would route here:
  "<phrasing 1>"
  "<phrasing 2>"
"""
import logging

logger = logging.getLogger(__name__)

__version__ = 1


class FooTool:
    """One-line class summary."""

    description = "..."

    def __init__(self, ...):
        # Whatever the tool needs — config-driven kwargs, injected
        # dependencies (e.g. `registry`, `vector_store`), or nothing.
        # See connection_tool.py (positional registry), atlassian.py
        # (env-driven kwargs), calculator.py (no args) for the spread.
        ...

    def run(self, input_str: str) -> str:
        """Single string in, single string out. Never raise — return error strings."""
        ...
```

Constructor shape is **not prescribed** — real tools span pure no-arg (`calculator`), keyword-only config (`atlassian`), and positional dependency injection (`connection_tool` takes a `registry`). Use whatever shape makes the tool callable from `registry.py`. The only hard rules are at the top (`__version__`, `logger`) and the `run()` contract below.

`from __future__ import annotations` is idiomatic in newer modules but not required — most existing tools omit it.

### `run()` contract

- Signature: `def run(self, <name>: str) -> str`. The parameter name varies (`expression`, `query`, `input_str`, `input_text`) to read naturally inside the method, but it is always exactly one positional string and the return is always a string.
- **Never raise to the caller.** Catch the expected exception classes (`ValueError`, `json.JSONDecodeError`, `KeyError`, `requests.RequestException`, etc.) and return an error string. Unexpected exceptions: catch broad `Exception`, `logger.error(...)`, return `f"Error: {e}"`. The decision LLM will read the string and decide what to do; an uncaught exception kills the agent turn.
- Error strings start with `"Error: "` for hard failures and `"Could not …"` / `"[HTTP 4xx]"` / similar for soft failures. Look at `connection_tool.py` and `calculator.py` for the two flavors.
- Output is for the LLM to read. Keep it terse, structured, and self-describing. Don't dump 10 KB of JSON when 5 lines of summary will do.

### Input parsing

Two house styles:

- **Natural-language input** (`calculator`, `datetime`, `web_search`): parse the raw string. Tolerate junk — strip, lowercase, regex out the meaningful substring. Don't reject input the LLM phrased imperfectly.
- **JSON input** (`connection_tool`, `cdp`, `aura_recon`): `json.loads(input_str)` first, return a clear error string on `JSONDecodeError` that shows the expected shape. Document the shape in `description` *and* in the module docstring.

Pick one and commit; tools that try to accept both end up matching neither well.

### Logging

- `logger = logging.getLogger(__name__)` at module top. Never `print()`. **Required.**
- `logger.info("ToolName initialized", ...)` once in `__init__` — recommended for tools with non-trivial setup (creds, connections, paths). Trivial tools (`calculator`, `connection_tool`) skip it; that's fine.
- `logger.debug(...)` per-call for parameters / outcomes.
- `logger.error(...)` for unexpected exceptions caught in `run()`.
- Don't log secrets — redact tokens, API keys, full request bodies that may contain credentials.

### Registry registration (`beigebox/tools/registry.py`)

Three edits, in order:

1. **Import** at the top with the other tool imports: `from beigebox.tools.foo import FooTool`.
2. **Instantiation block** inside `ToolRegistry.__init__`, in the alphabetical-ish region near similar tools:
   ```python
   # --- Foo (one-line description — disabled/enabled by default) ---
   foo_cfg = tools_cfg.get("foo", {})
   if foo_cfg.get("enabled", <default>):
       self.tools["foo"] = FooTool(
           api_key=foo_cfg.get("api_key", ""),
           timeout=float(foo_cfg.get("timeout", 10)),
       )
       logger.info("Foo tool registered")
   ```
3. **Section header comment** explaining what's special if anything — see how `cdp`, `apex_analyzer`, `atlassian` document their daemon/file/env requirements.

The dict key (`"foo"`) is what the LLM types in `{"tool": "foo", "input": "..."}`. Keep it short and stable.

### Config (`config.yaml`)

Append a block under `tools:` mirroring neighbors:

```yaml
  # ── Foo (one-line pitch) ──────────────────────────────────────────────────
  # Requires: <daemons / env vars / installs>
  # Operator calls: {"tool": "foo", "input": "..."}
  foo:
    enabled: false              # or true if no-deps
    api_key: ${FOO_API_KEY:-}   # env-substitution syntax; never inline real secrets
    timeout: 10                 # seconds
```

- **Credentials live in env vars.** Use `${VAR_NAME:-default}` substitution. Never paste a real key into `config.yaml`. The Atlassian tool reads `ATLASSIAN_*` from env via `~/.beigebox/.env`; follow that pattern.
- **Default `enabled: false` for anything with side effects** (network calls beyond the LLM, file writes outside `/workspace/out/`, paid APIs, sudo).
- Document what the operator JSON looks like in a comment above the block — the tools section is where the LLM-facing contract is most visible.

### Tests

- For trivial tools (`calculator`, `datetime`): add a test case to `tests/test_tools.py`.
- For non-trivial tools: create `tests/test_<name>_tool.py` with at least:
  - `test_<name>_init` — instantiation with default config doesn't raise.
  - `test_<name>_happy_path` — one realistic `run()` call returns a non-error string.
  - `test_<name>_error_returns_string` — bad input returns an error string, does not raise.
- Network-bound tools should mock at the HTTP layer (`responses`, `httpx_mock`) — tests must run offline.

## Anti-patterns

- **Raising from `run()`.** Breaks the agent turn. Always catch and return.
- **Returning structured objects** (dicts, lists). The contract is `str`. JSON-serialize if you must, but prefer summarized prose the LLM can quote back.
- **Embedding credentials in code or `config.yaml`.** Env vars only.
- **Adding a tool that wraps a single shell command** the agent could just run. Tools earn their keep with state, parsing, auth, or rate-limiting — not aliasing.
- **Silent enablement by default for tools with side effects.** A `foo.enabled: true` default that hits a paid API on every agent turn is a footgun. Default-disabled means the operator opted in.
- **Stateful prose in module docstrings.** No "as of Apr 2026", no "we recently added X". Tool docstrings are read by future authors and decay quickly when written like changelog entries.

## Validation

After adding a tool, walk this list:

- [ ] `class <Name>Tool` (PascalCase, suffixed `Tool`)
- [ ] `__version__ = <int>` at module top (start at `1`)
- [ ] `description` class attribute exists, leads with a verb, includes an input-shape example, ≤ ~300 chars
- [ ] `run(self, <name>: str) -> str` — single positional string, returns string
- [ ] `run()` catches all expected exceptions and returns error strings
- [ ] `logger = logging.getLogger(__name__)`, no `print()`
- [ ] Tool name (registry key) doesn't collide with any existing entry in `beigebox/tools/registry.py` or any skill in `beigebox/skills/`
- [ ] Imported in `beigebox/tools/registry.py`
- [ ] Conditional instantiation block in `ToolRegistry.__init__` honors `tools_cfg.get("<name>", {}).get("enabled", <default>)`
- [ ] `config.yaml` has a `tools.<name>:` block with `enabled:` and a header comment
- [ ] Credentials (if any) come from env via `${VAR:-default}` — no raw secrets in config
- [ ] At least one test exists, covers init + happy path + one error path, **runs with no network access** (HTTP-bound tools mock at the transport layer)
- [ ] No stateful prose in the module docstring — no dates, no "as of …", no "we recently added …"
- [ ] None of the anti-patterns above are present
- [ ] Tool's scope fits in one paragraph — if the description needs two, split into multiple tools

## Versioning

This skill is at `version: 2`. Bump the integer whenever the standard's behavior, scaffold output, or validation checklist changes in a way a consumer would notice (new section, removed flag, renamed script, added or relaxed rule). Pure typo fixes don't bump.

Tools versioned as `__version__ = <int>` follow the same rule at the per-tool level: bump when behavior, input shape, or output format changes in a way callers would notice.
