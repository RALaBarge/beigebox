# 2600 — BeigeBox Design Docs

Design documents, session notes, and implementation specs. Each file has a status header on line 1.

**Status legend:**
- `✅ COMPLETE` — Implemented or archived, no action needed
- `⚠️ PARTIAL` — Some work done, remainder noted in the file
- `🔄 IN PROGRESS / FUTURE` — Planned but not started, or execution phase pending
- `🚫 WON'T DO` — Explicitly skipped, decision recorded in the file

---

## 2600/ (root) — Recent docs (March 2026)

| Status | File | Summary |
|--------|------|---------|
| ✅ | [agentic-tool-prompting-beigebox.md](agentic-tool-prompting-beigebox.md) | 13 agentic tool-prompting patterns for operator/harness |
| ✅ | [autoresearch-beigebox.md](autoresearch-beigebox.md) | Autoresearch loop for self-tuning classifier/routing |
| ✅ | [cdp-browser-automation-beigebox.md](cdp-browser-automation-beigebox.md) | CDP browser automation — fully implemented |
| ⚠️ | [CODEBASE-AUDIT.md](CODEBASE-AUDIT.md) | 34 quality issues — quick wins done, strategic refactors deferred |
| ⚠️ | [code-quality-audit-2026-03-16.md](code-quality-audit-2026-03-16.md) | Duplicate of CODEBASE-AUDIT findings, same status |
| 🔄 | [context-optimization-discovery-framework.md](context-optimization-discovery-framework.md) | 15 optimization opportunities — Phase 1 infra done, Phases 2-6 pending |
| ✅ | [MEDIUM-LOW-FIXES.md](MEDIUM-LOW-FIXES.md) | Quick-win fixes applied (utils, constants, except clauses) |
| ✅ | [model-resource-visibility.md](model-resource-visibility.md) | VRAM/model resource visibility — fully implemented |
| ✅ | [no-backends-available-diagnosis.md](no-backends-available-diagnosis.md) | Routing bug diagnosis — fixed (explicit model bypass) |
| ✅ | [output-normalizer-wasm.md](output-normalizer-wasm.md) | WASM output normalizer — implemented |
| ✅ | [request-pipeline-complete.md](request-pipeline-complete.md) | 14-stage pipeline reference doc — matches implementation |
| ⚠️ | [SESSION-SUMMARY.md](SESSION-SUMMARY.md) | Config refactor — P1 done, P2 done, P3 YAML done, P4/P5 skipped |
| ✅ | [staging-auto-ingest-system.md](staging-auto-ingest-system.md) | Auto-ingest system — fully implemented |
| 🔄 | [tap-redesign.md](tap-redesign.md) | Tap redesign — partial, egress hooks done, full spec deferred |
| 🔄 | [task-packet-orchestration.md](task-packet-orchestration.md) | Task Packet multi-agent boundary objects — not yet built |

## 2600/config/ — Config refactoring phases

| Status | File | Summary |
|--------|------|---------|
| ⚠️ | [config/REFACTOR-PHASES-1-2-3.md](config/REFACTOR-PHASES-1-2-3.md) | P1 done, P2 done, P3 YAML done / code skipped |
| 🚫 | [config/phase-4-tools-restructuring.md](config/phase-4-tools-restructuring.md) | Won't do — Option B chosen (skip) |
| 🚫 | [config/phase-5-storage-consolidation.md](config/phase-5-storage-consolidation.md) | Won't do — Option C chosen (skip) |

## 2600/2599/ — Archived docs

All files in `2600/2599/` are archived design docs. Files from before 2026-03-16 are marked **✅ COMPLETE — Implemented and archived**. Files from March 19+ have individual status headers.

### March 2026 notable status

| Status | File | Summary |
|--------|------|---------|
| ✅ | [2599/mcptoollimiting.md](2599/mcptoollimiting.md) | MCP progressive tool disclosure — implemented 2026-03-23 |
| ✅ | [2599/operator-background-execution.md](2599/operator-background-execution.md) | Operator backgrounding — implemented (commit f9450a75) |
| ✅ | [2599/OPERATOR_MODEL_AUDIT.md](2599/OPERATOR_MODEL_AUDIT.md) | Operator model UI — all 6 fixes applied |
| ⚠️ | [2599/agent_fabric_existing_protocols.md](2599/agent_fabric_existing_protocols.md) | mDNS/NATS done; MQTT/AsyncAPI not implemented |
| ⚠️ | [2599/observability-and-systems-todo.md](2599/observability-and-systems-todo.md) | Some items done; orchestration profiles etc. remain |
| ✅ | [2599/1bitImplimentationScript.md](2599/1bitImplimentationScript.md) | BitNet b1.58 — tested and removed |
| ✅ | [2599/multi-turn-research.md](2599/multi-turn-research.md) | Research reference — digested |
| ✅ | [2599/multi-turn-research2.md](2599/multi-turn-research2.md) | Research reference — digested |
| ✅ | [2599/multi-turn-research3.md](2599/multi-turn-research3.md) | Research reference — digested |

---

*Last updated: 2026-03-23*
