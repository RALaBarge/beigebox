Here’s a clean, medium/high-level summary ready for implementation:

---

### 📦 What It Is
A token-efficient code-editing protocol that replaces line numbers and hash-based search blocks with **pre-generated single-token English word anchors**, backed by a lightweight state manager and **Myers Diff reconciliation**. Designed to make AI agent edits cheaper, more reliable, and immune to cascading invalidation.

---

### ⚙️ How It Works (Workflow)
1. **Inject**: When an agent reads a file, the backend prefixes each line with a unique `Anchor§` (e.g., `Moderator§def foo():`).
2. **Prompt**: Agents are instructed to reference `start_anchor` / `end_anchor` and output **only the replacement code**.
3. **Validate**: Backend confirms both anchors exist in the current file state (simple string match).
4. **Apply & Reconcile**: After the edit, Myers Diff compares old vs new lines. Unchanged lines keep their anchors; changed/inserted lines get fresh ones from the pool.
5. **Loop**: The newly anchored file feeds into the next agent step or validation pass.

---

### 🔄 Anchor Lifecycle & Persistence Rule
- **Persists if**: The normalized line text remains identical (whitespace/tab normalization allowed).
- **Resets if**: Any code change, comment edit, or logic refactor touches that line.
- **Why strict?**: Prevents silent drift. Anchors track *structural identity*, not semantic equivalence. Deterministic > fuzzy.

---

### 🎯 Why It Fits `garlicpress`
- **Token savings**: Cuts fix-emission output to O(R) → ~60–75% cost reduction in edit-heavy loops.
- **Multi-agent stability**: Consensus reviewers and fix generators reference the exact same regions, even after parallel edits.
- **State-aligned**: Maps cleanly to your existing finding/iteration state tracking.
- **Language-agnostic**: Works across Python, TS, Haskell, etc. No AST or parser dependencies.
- **Protocol-agnostic**: Can wrap any agent prompt format without changing core analysis logic.

---

### 🛠 Implementation Checklist
1. **Anchor Pool**: Import/generate ~1,700 single-token, tokenizer-safe English words.
2. **State Manager**: In-memory map `{file_path → {line_index → anchor}}`, scoped per task/run.
3. **Injector**: Hook into your file-read path to prepend `Anchor§` before sending to agents.
4. **Prompt Update**: Instruct agents to use `start_anchor`/`end_anchor` + replacement-only output.
5. **Validator**: Simple string match + fallback error with current anchors if stale.
6. **Reconciler**: Use `difflib.SequenceMatcher` (or a Myers impl) post-edit to reassign anchors only to changed lines.
7. **Fallback**: When single-token pool exhausts, auto-combine to 2-token anchors (e.g., `Moderator/Qualifier`).

---

### ⚖️ Boundaries & Tradeoffs
- **Stateful**: Requires lightweight tracking, but fits naturally into your existing workflow state.
- **Structural, not semantic**: Won’t preserve anchors across behavior-preserving refactors. That’s a separate layer (e.g., finding IDs, AST matching).
- **Best for**: Iterative fix generation, consensus validation, and CI-style edit loops.
- **Overhead**: Negligible. Diff + normalization runs in milliseconds on typical source files.

---

### ✅ Final Verdict
It’s a pragmatic, production-ready optimization for agentic code editing. Low complexity, high ROI on token costs and edit reliability, and cleanly separable from your core analysis pipeline. Drop the injector + state map into your read path, update prompts, wire in diff-based reconciliation, and you’ll see immediate gains in cost and stability.

Ready to implement. If you hit a snag on the diff reconciliation or prompt templating, just ping and I’ll drop a precise snippet.


https://dirac.run/posts/hash-anchors-myers-diff-single-token

---

## TODO: explore expanding the multi-model code-review fanout pattern

The 2026-04-29 session ran a per-module fanout review across the codebase
through Grok 4.20, then handed Trinity Large Thinking Grok's reduce as a
followup turn so it could update its view, then handed Qwen 3.5 Plus both
prior reviews + followup, then DeepSeek v4 Pro all four. Each reviewer
produced its own per-module + reduce, plus (after the first) a followup
that explicitly conceded/defended in light of priors.

Outputs landed on Desktop (`{model}_codebase_review.json`,
`{model}_review_summary.txt`, `{model}_followup_to_*.md`).

Things worth investigating before committing to building this out as a
first-class feature:

- **Convergence tracking.** Across N reviewers, which findings are agreed-on
  unanimously vs. flagged by one outlier? Unanimous findings are likely real
  bugs; outliers are either model-specific blind spots or genuine catches
  others missed. A simple deduper + tag aggregator would surface this.
- **Validation against ground truth.** When a finding leads to a fix in a
  later commit, mark it true-positive. Over time, build a per-model accuracy
  scoreboard for "actually-a-bug" vs "stylistic noise." Could feed back into
  routing weights.
- **Cross-model debate, not just sequential followups.** A round-robin where
  each model gets to respond to specific concrete claims by name ("Grok
  asserts X; defend or concede"). May surface sharper disagreements than
  the current "pile of synthesized opinions" approach.
- **Domain panels.** Security panel (Grok + DeepSeek + a security-tuned
  model), arch panel, perf panel — each scored on a different rubric and
  reduced separately. Avoids one panel's opinions diluting another's.
- **Cost vs. signal floor.** This run cost ~$2-3 across four models for one
  codebase snapshot. Worth investigating whether a 2-model panel gets
  ~80% of the signal at 30% of the cost.
- **CI integration.** On PR, run the panel only on changed modules + their
  one-hop dependencies. Surface convergent concerns as PR comments;
  outlier concerns as a side-channel "may be worth a look" thread.
- **Findings DB.** Pipe the per-finding output (file, line, severity, model,
  rationale) into a structured store (Postgres `findings` table?) instead
  of leaving it as Markdown on the desktop. Enables longitudinal queries
  ("are we accumulating or shedding findings in `agents/`?").
- **Self-reviewing the reviewer.** Have one of the panel models meta-review
  the panel output for false positives / overstatement. Trinity's followup
  already did some of this organically; making it explicit could cut noise.

The fanout skill (`beigebox/skills/fanout/`) already does the per-item
parallelism + reduce. The followup turn is currently a one-off Python
script (`/tmp/trinity_followup.py` style). A `panel` skill that orchestrates
N reviewers + sequential followups + convergence reporting would be the
natural next abstraction.

Open question: are there *downsides* to making this routine? Risks:
amplifying a shared model bias (every Anthropic-trained model agrees on a
wrong thing), creating review fatigue, false sense of rigor. Worth a small
adversarial pass before building it out.
