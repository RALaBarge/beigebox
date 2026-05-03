# BeigeBox Is Not

Boundaries. The things we already said no to, written down once.

When you're tempted to add something, check this list first. If the new
thing pushes against a NO here, the default answer is "no, and the bar
to override is high — bring it back with the reason."

This doc is **terse on purpose**. Each line is one rule. If you need a
paragraph to defend a NO, the rule probably isn't a clean line yet.

---

## Architecture

- **Not multi-tenant.** Single user, single config. No per-tenant isolation,
  per-tenant config, or "organizations." The auth layer has multi-*key* support
  for a single-tenant operator who wants to revoke a leaked key, not for
  serving multiple paying customers.

- **Not an inference engine.** Inference happens on Ollama / OpenRouter /
  whatever backends are configured. BeigeBox is the *proxy* in front of them —
  routing, capture, security, observability. If a feature would need GPU
  training time or model weights as artifacts, it doesn't belong here.

- **Not a fine-tuner / trainer.** Same reason. Use the upstream tooling.

- **Not an agent harness.** Operator + the agentic loops were deleted in v3
  on purpose. SOTA-class models running through BeigeBox can drive themselves
  via MCP tools. We are not rebuilding "the agent that runs in BeigeBox."

- **Not a code sandbox.** The bwrap profile in `system_info.py` exists to
  contain *our own* shell-out calls (`df`, `nvidia-smi`). It is not a
  general-purpose REPL for user-supplied code. The python_interpreter +
  workspace_file MCP tools were deleted in 2026-05-02 for this reason.

- **Not a research framework.** dgm/, eval/, discovery/, orchestration/,
  agents/, AMF mesh — all deleted in v3. Experiments live as separate
  projects that *consume* BeigeBox, not in-tree.

## Client coupling

- **Client-agnostic.** New frontends (Claude Code, jcode, custom MCP clients,
  whatever) reach BeigeBox via the existing HTTP / CLI / MCP surfaces. We do
  NOT add in-BeigeBox connectors that know about specific frontends. If
  Frontend X needs a feature, it's either a generic HTTP endpoint or it
  belongs in Frontend X.

- **No client SDK in this repo.** Use OpenAI-compatible clients with the
  base URL pointed at BeigeBox.

## Storage and capture

- **Capture everything.** No source-side filtering, no severity gating on
  what we wire-log. Redundancy across JSONL + SQLite + Postgres is
  intentional. (User direction, 2026-05-01.)

- **No plaintext PII in capture metadata that we control.** The full message
  body is captured because we have to — a redacted body is a useless replay.
  But the *meta* fields (event_type, source, role, conv_id, model, etc.)
  must not contain PII pulled out of the body.

- **No silent drops.** Every chat completion produces a row in `messages`,
  even on upstream error / mid-stream abort / client disconnect. If a future
  failure mode would result in a silent drop, fix the capture, don't drop.

- **Postgres for everything we can.** SQLite is the single-binary fallback;
  postgres is the production substrate. New SQL state goes to postgres-first
  unless there's a specific reason otherwise. (User direction, 2026-05-01.)

## Outbound

- **No automatic publishing.** No auto-push to GitHub, auto-DM to Slack,
  auto-tweet, auto-anything to a remote service. All outbound is opt-in via
  configured webhooks (`observability.egress`) or explicit user-triggered
  endpoints.

- **No auto-update of dependencies.** Hash-locked via `requirements.lock`,
  manually rolled. No Dependabot. No `latest` tags. The pre-commit hook
  re-locks; nothing else mutates deps.

- **No outbound to forbidden hosts.** `hssh`/`hftp` (production webspace)
  and `wssh`/`wftp` (Whatbox seedbox) are off-limits to automated sessions.
  See CLAUDE.md.

## Security posture

- **No untrusted user code execution outside `bwrap`.** If we can't sandbox
  it, we don't execute it. "Just shell out to whatever the LLM said" is the
  exact thing we refuse to do.

- **No `shell=True` with user input.** Argv lists only. No f-string
  interpolation of user-supplied data into shell commands. (See
  `beigebox/security_mcp/_run.py` for the canonical example.)

- **No bypass of the auth middleware.** New endpoints attach to the existing
  middleware stack. If you need an exempt path, add it to `_AUTH_EXEMPT` /
  `_AUTH_EXEMPT_PREFIXES` in `middleware.py` with a written reason; don't
  short-circuit elsewhere.

- **No credentials in logs, even by accident.** Querystring API keys are
  refused on the way in (`?api_key=…` is rejected) precisely because they
  leak into access logs. Maintain that posture for new auth methods.

## Backwards compatibility

- **Not committed to v2 (public `beigebox`) compatibility.** v2 is frozen on
  `origin/main`. v3 lives on `security/main`. No backporting tax in v3 to
  preserve v2-style call sites or shims. If a v2-pinned user needs a fix,
  it's a deliberate cherry-pick, not a routine flow.

- **No `// removed` comments, no rename-to-`_var` shims, no re-exports for
  "old import paths."** Just delete it. See CLAUDE.md.

- **No half-finished migrations as the production code path.** If a
  migration is in flight, mark it: feature flag, `EXPERIMENT(YYYY-MM-DD):`
  comment, or tracked TODO with an expiration. Otherwise, finish it.

## Process

- **No skipping pre-commit hooks (`--no-verify`).** Hash-locking, lint,
  type-check all run on commit. If a hook fails, fix the cause, not the
  hook.

- **No force-push to `origin/main` or `security/main`.** Both remotes are
  fast-forward only.

- **No commits without a Co-Authored-By footer when AI-assisted.** All v3
  commits in this repo carry the footer. Maintain that for traceability.

---

## Squishy — your call

These are the lines I'm not confident enough to write. Decide and add as
firm rules, or delete if you're explicitly OK with the answer being "yes".

**For Claude:** when a squishy item below comes up in conversation, raise
the open decision with the user before assuming a default. Once the user
gives an answer, move that line into the firm-rules section above and
mark it `(permanent)` so we don't re-litigate. If the user says "leave
it squishy" or "still deciding," keep it here.

- **Web UI scope.** Stay-simple chat + admin? Or grow into a full
  multi-pane analytics dashboard? Where's the line?
  {{TODO: decide}}

- **License model.** AGPL-3.0 + Commercial dual-license is set; is BeigeBox
  ever offered as a hosted service, or always self-hosted only?
  {{TODO: decide}}

- **Plugin / extension model.** `plugins/` exists. Is third-party plugin
  loading welcomed, discouraged, or banned? (Banning has security upside;
  welcoming has community upside.)
  {{TODO: decide}}

- **MCP server scope.** Two MCP endpoints today (`/mcp` regular + `/pen-mcp`
  offensive). Is the offensive-security split a permanent design boundary
  or a temporary convenience?
  {{TODO: decide}}

- **Telemetry.** Does BeigeBox ever phone home (anonymous version pings,
  crash reports, usage stats)?
  {{TODO: decide — leaning NO based on the rest of this doc, but worth
  stating explicitly}}

- **Hosting / deployment as a target.** Docker Compose + K8s + systemd are
  documented. Is "deploy BeigeBox to a managed cloud" a supported use case
  or out-of-scope?
  {{TODO: decide}}

---

**Edit this file when something changes.** It's a constitution, not a
historical record.
