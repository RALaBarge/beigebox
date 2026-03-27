# ✅ COMPLETE — CDP enabled by default. All phases complete: browserless/chrome in docker-compose (cdp profile), CDPTool registered in tool registry, operator tools: navigate, screenshot, dom_snapshot, click, type, scroll, eval, network capture, etc.

# chrome devtools protocol for beigebox

> Extracted from [Chrome DevTools MCP blog post](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session), [HN discussion](https://news.ycombinator.com/item?id=47390817), [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp), and [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill).

**Status**: Design document / TODO  
**Date**: 2026-03-15  
**Priority**: Medium — multiple applications across consulting, operator agent, and UI testing  

---

## What Happened

Chrome DevTools team shipped auto-connect to live browser sessions via their MCP server (Chrome M144+). An agent can now attach to your existing Chrome tabs — logged-in sessions, active debugging, network panel selections — and inspect/interact without launching a separate browser.

The HN thread immediately split into two camps:

1. **MCP is dead** — CLI tools are faster, lighter on tokens, more flexible. MCP bleeds context window even when tools aren't being used. Multiple experienced developers saying they've abandoned MCP entirely in favor of CLI + Playwright/CDP.
2. **MCP is fine** — remote MCP servers are useful, tool search helps, context rot is manageable.

The more interesting development: Paul Irish (Chrome DevTools team) quietly announced a **standalone CLI** in v0.20.0 that avoids MCP entirely. And pasky built `chrome-cdp-skill` — a pure CLI wrapper around CDP WebSocket that holds persistent daemon connections per tab, no Puppeteer, no MCP, handles 100+ tabs reliably.

---

## Why This Matters for BeigeBox

### 1. Validates the Anti-MCP Architecture Decision

BeigeBox explicitly deprioritized MCP support as a misalignment with its middleware identity. The HN consensus from practitioners reinforces this:

> "MCP is very obviously dead, as any of us doing heavy agentic coding know. Why permanently sacrifice that chunk of your context window when you can just use CLI tools which are also faster and more flexible" — mmaunder

> "MCP servers, once configured, bloat up context even when they are not being used. Why would anybody want that? Use agent skills. And say goodbye to MCP." — cheema33

> "all you need is a simple skills.md and maybe a couple examples and codex picks up my custom toolkit and uses it" — nojito

BeigeBox's plugin auto-discovery system + CLI-first operator agent + SKILL.md-style documentation (`2600/` directory) already follows this pattern. This is confirmation, not a pivot.

### 2. Operator Agent Browser Capability

The operator agent currently has sandboxed shell execution (bubblewrap + allowlist + audit logging). Adding CDP-based browser automation extends its capabilities to:

- Navigating web UIs on behalf of the user
- Scraping data from authenticated sessions
- Running Lighthouse/performance audits
- Interacting with web-based tools and dashboards
- Filling forms, clicking through workflows
- Screenshot capture for visual verification

The `chrome-cdp-skill` approach is the right model: pure WebSocket to CDP, no framework dependencies, lightweight daemon per tab, CLI interface that the operator can invoke via its existing shell execution path.

### 3. Web UI Automated Testing

BeigeBox has a web UI (multi-tab, mobile-responsive, vi mode optional). Currently tested manually. CDP enables:

- End-to-end testing of the BeigeBox web UI
- Accessibility validation (take_snapshot returns a11y tree)
- Responsive testing across viewport sizes
- Network request inspection (verify API calls from UI)
- Performance tracing (Core Web Vitals)
- Screenshot-based visual regression

This ties directly into the autoresearch pattern: the web UI config becomes the editable asset, a composite metric (accessibility score + performance score + visual regression diff) becomes the scalar, and the CDP test suite becomes the evaluation harness.

### 4. Consulting Value: Automated Web Audits

For consulting clients, CDP-powered automated audits produce deliverables:

- Performance reports with Core Web Vitals (INP, LCP, CLS)
- Accessibility compliance checks (WCAG)
- Network waterfall analysis (HAR export)
- Responsive testing across device profiles
- Security header analysis
- Console error cataloging

This runs on the client's own infrastructure via BeigeBox's Docker Compose deployment. The audit trail feeds into BeigeBox's wiretap logging.

---

## Technical Approaches (Ranked)

### Approach A: CLI Wrapper Around CDP (RECOMMENDED)

Adapt the `pasky/chrome-cdp-skill` pattern. Pure Node.js, direct WebSocket to CDP, daemon per tab, CLI interface.

**Why**: Fits BeigeBox's operator agent model perfectly — the operator invokes CLI commands via its sandboxed shell. No MCP overhead, no framework dependencies, no context window bloat. The CLI commands are allowlisted in the operator config.

```
# Operator agent allowlist additions
cdp list              # list open tabs
cdp shot <target>     # screenshot → file
cdp snap <target>     # accessibility tree
cdp html <target>     # full HTML or CSS-selector scoped
cdp eval <target> "expr"  # evaluate JS in page context
cdp nav  <target> <url>   # navigate and wait
cdp net  <target>     # network resource timing
cdp click <target> "selector"  # click element
```

**Security model**: The operator's existing bubblewrap sandbox + allowlist already constrains what commands can run. CDP access is an additional allowlisted tool, not an open door. Audit logging captures every CDP command and its target.

**Implementation**: ~200-300 lines of JS (based on pasky's implementation). Single file, no npm install (Node.js 22+ has native WebSocket). Drops into BeigeBox's `tools/` directory.

### Approach B: Playwright via Subprocess

Use Playwright as a higher-level abstraction. More features (auto-wait, selectors, test runner) but heavier dependency.

**Why not first**: Playwright is a large dependency (~100MB+), launches its own browser instances by default (doesn't attach to existing sessions easily), and the operator agent's CLI model is simpler with direct CDP.

**When it makes sense**: If we need headless browser testing in Docker (CI/CD), Playwright's container support is mature. Good for the automated web UI testing use case but overkill for operator agent ad-hoc browser interaction.

### Approach C: Chrome DevTools MCP Server

Use Google's official `chrome-devtools-mcp` package.

**Why not**: Contradicts BeigeBox's anti-MCP architecture. Burns 17k tokens for 26 tool definitions even when not in use. The new CLI in v0.20.0 is interesting but still npm-heavy. And BeigeBox doesn't have MCP infrastructure to connect it to anyway.

---

## Implementation Plan

### Phase 1: CDP CLI Tool for Operator Agent

Add a lightweight CDP CLI tool that the operator agent can invoke through its existing shell execution path.

**Deliverables:**
- [ ] `tools/cdp.mjs` — standalone CDP CLI (~200-300 lines, no deps beyond Node.js 22+)
- [ ] Operator agent allowlist update — add `cdp` commands to allowed command list
- [ ] Audit log integration — CDP commands logged to existing audit trail
- [ ] `2600/cdp-operator-skill.md` — instructions for the operator agent on when/how to use CDP
- [ ] Security review — confirm bubblewrap sandbox constrains CDP access appropriately

**Commands to implement (Phase 1):**

| Command | Description | Operator Use Case |
|---|---|---|
| `cdp list` | List open tabs with targetId + URL | Discover available pages |
| `cdp snap <target>` | Accessibility tree (compact) | Understand page structure |
| `cdp shot <target>` | Screenshot to file | Visual verification |
| `cdp html <target> [selector]` | Get HTML (full or scoped) | Extract page content |
| `cdp eval <target> "expr"` | Evaluate JS in page context | Read page state, extract data |
| `cdp nav <target> <url>` | Navigate to URL | Open specific pages |
| `cdp net <target>` | Network resource timing | Debug API issues |
| `cdp click <target> "selector"` | Click element | Interact with UI |
| `cdp type <target> "text"` | Type at focused element | Fill forms |

**Connection model** (from pasky's approach):
1. Chrome runs with remote debugging enabled (`chrome://inspect/#remote-debugging`)
2. CDP CLI discovers tabs via `http://127.0.0.1:9222/json/list`
3. Spawns a lightweight daemon per tab that holds the WebSocket session open
4. Subsequent commands reuse the daemon (no reconnection, no "Allow debugging" re-prompt)
5. Daemons auto-exit after configurable idle timeout (default 20min)

### Phase 2: Web UI Test Suite

Automated end-to-end tests for BeigeBox's own web UI using the CDP CLI.

**Deliverables:**
- [ ] `tests/e2e/test_webui.py` — E2E test suite using CDP CLI via subprocess
- [ ] `tests/e2e/fixtures/` — test data (conversations, configs) for UI tests
- [ ] Docker Compose integration — headless Chrome service for CI
- [ ] `autoresearch/domains/webui/` — autoresearch loop for web UI optimization (ties into autoresearch doc)

**Test coverage targets:**
- Tab management (create, switch, close)
- Conversation flow (send message, receive response, verify rendering)
- Settings panel (change config, verify persistence)
- Mobile viewport responsive behavior
- Vi mode toggle and keybindings
- Voice interface controls (if voice profile active)
- Generation parameter sliders
- Error states (backend down, model timeout)
- Accessibility baseline (a11y tree assertions)

### Phase 3: Consulting Audit Toolkit

Packaged web audit tool for consulting clients.

**Deliverables:**
- [ ] `tools/web_audit.py` — orchestrates CDP commands to produce a comprehensive audit report
- [ ] Performance report template (Core Web Vitals, resource timing, waterfall)
- [ ] Accessibility report template (a11y tree analysis, WCAG checks)
- [ ] Network analysis template (HAR export, request/response inspection)
- [ ] Responsive test matrix (predefined viewport list, screenshot per viewport)
- [ ] Report output as Markdown → `2600/` or client-facing PDF

---

## CDP Protocol Reference

### Connection

```javascript
// Discover targets
const resp = await fetch('http://127.0.0.1:9222/json/list');
const targets = await resp.json();
// Each target: { id, type, title, url, webSocketDebuggerUrl }

// Connect to a tab
const ws = new WebSocket(target.webSocketDebuggerUrl);
```

### Key CDP Domains

| Domain | What It Does | BeigeBox Use |
|---|---|---|
| `Page` | Navigation, lifecycle events, screenshots | Navigate, capture state |
| `Runtime` | JS evaluation in page context | Extract data, read state |
| `DOM` | DOM tree access, query selectors | Find elements, read content |
| `Network` | Request/response interception, timing | Debug API calls, HAR export |
| `Accessibility` | A11y tree snapshots | Audit compliance |
| `Performance` | Performance traces, metrics | Core Web Vitals |
| `Input` | Mouse/keyboard dispatch | Click, type, interact |
| `Emulation` | Device emulation, viewport, network throttle | Responsive testing |

### Minimal CDP Command Pattern

```javascript
// Send command
ws.send(JSON.stringify({ id: 1, method: 'Page.navigate', params: { url: 'https://example.com' } }));

// Receive response
ws.on('message', (data) => {
  const msg = JSON.parse(data);
  if (msg.id === 1) { /* response to our command */ }
  if (msg.method) { /* event notification */ }
});
```

---

## Security Considerations

### Attack Surface

The HN thread raised legitimate concerns about prompt injection when agents have browser access:

> "You're literally one prompt injection away from someone having unlimited access to all of your everything." — Etheryte

> "Even just stealing the auth cookie is pretty serious in terms of damage it could do." — sofixa

### BeigeBox Mitigations

BeigeBox's layered security model applies directly here:

1. **Bubblewrap sandbox** — the operator agent already runs in a namespace sandbox. CDP commands execute within this sandbox. The agent can't escape to arbitrary system access.

2. **Command allowlist** — only specific `cdp` subcommands are allowlisted. No raw CDP passthrough in Phase 1 (that's the `evalraw` command — deliberately excluded from initial allowlist).

3. **Audit logging** — every CDP command, its target tab, and the response are logged to the append-only audit trail. Reviewable after the fact.

4. **Separate Chrome profile** — for operator agent browser access, use `--user-data-dir=/tmp/beigebox-cdp-profile` (isolated from the user's main Chrome profile). No access to logged-in sessions unless explicitly configured.

5. **No `eval` in default allowlist** — `cdp eval` (arbitrary JS execution in page context) is excluded from the default operator allowlist. Must be explicitly enabled in `config.yaml` with `operator_cdp_eval: true`. This is the most dangerous capability and should be opt-in only.

6. **Tab targeting** — the operator must specify a target by ID. No wildcard access to all tabs. The `cdp list` command only shows tabs in the isolated profile.

7. **Idle timeout** — daemon auto-exits after 20 minutes of inactivity. No persistent open connections lingering.

### Security-Sensitive Config (config.yaml, NOT runtime_config.yaml)

```yaml
operator:
  cdp:
    enabled: false              # must be explicitly enabled
    chrome_profile: /tmp/beigebox-cdp  # isolated by default
    port: 9222
    allowed_commands:
      - list
      - snap
      - shot
      - html
      - nav
      - net
      - click
      - type
    # eval and evalraw deliberately excluded from default
    idle_timeout: 1200          # seconds
```

---

## HN Signal: MCP vs CLI vs Skills

The most actionable pattern from the discussion is the convergence on **agent skills** (SKILL.md files) as the interface between agents and tools. This is exactly what BeigeBox's `2600/` directory and plugin auto-discovery system already do.

Key quotes from the thread:

**On the "skills" pattern replacing MCP:**
> "all you need is a simple skills.md and maybe a couple examples and codex picks up my custom toolkit and uses it" — nojito

**On CLI over MCP:**
> "CLI. Always CLI. Never MCP. Ever." — mmaunder

**On the new DevTools CLI (Paul Irish, DevTools team):**
> "The DevTools MCP project just recently landed a standalone CLI... Great news to all of us keenly aware of MCP's wild token costs." — paulirish

**On MCP token costs:**
> "chrome-devtools MCP currently provides 26 tools that together take up 17k tokens; that's 10% of Opus 4.5's context window" — Amp docs

**On the Playwright + request interception approach:**
> "I use Playwright to intercept all requests and responses and have Claude Code navigate to a website... Then it creates a detailed strongly typed API to interact with any website using the underlying API." — dataviz1000

This last one is interesting for consulting scenarios — using browser automation to reverse-engineer API surfaces of client web applications, then generating typed clients. Not core BeigeBox but a consulting service that runs on BeigeBox infrastructure.

---

## Relationship to Other BeigeBox Work

### Plugin Registry Formalization

The CDP CLI tool becomes a plugin entry in the universal vtable/dispatch pattern. It follows the same interface as other operator tools: CLI command → stdout/stderr → audit log.

```
plugins/
├── operator_tools/
│   ├── shell.py          # existing sandboxed shell
│   ├── cdp.mjs           # new: browser automation via CDP
│   └── ...
```

### Autoresearch Integration

The CDP test suite enables an autoresearch loop for web UI optimization (see `autoresearch-beigebox.md`). The editable asset is the web UI config/CSS, the scalar metric is a composite of accessibility score + performance metrics + visual regression, and the CDP test suite is the evaluation harness.

### Operator Agent Skill Document

The `2600/cdp-operator-skill.md` follows the same pattern as `program.md` in the autoresearch context — it's structured prose that tells the operator agent when and how to use browser automation, what the security constraints are, and what patterns produce good results.

### llama.cpp Sidecar

If the llama.cpp sidecar exposes a web UI (many GGUF servers do), the CDP tool can test that UI too, keeping the same test infrastructure across both BeigeBox's web UI and the sidecar's.

---

## Reference: pasky/chrome-cdp-skill Architecture

The cleanest implementation to learn from. Key design decisions:

1. **No npm install** — uses Node.js 22+ native WebSocket, fetch, and fs. Zero dependencies.
2. **Daemon per tab** — spawns a background process that holds the CDP WebSocket open. First access triggers Chrome's "Allow debugging" modal once; subsequent commands reuse silently.
3. **Target addressing** — tabs are addressed by unique prefix of their targetId (from `/json/list`). Unambiguous, short.
4. **Stdout as interface** — every command writes structured output to stdout. Agents parse this naturally.
5. **Auto-cleanup** — daemons exit after 20 minutes idle. No orphan processes.
6. **~200 lines of JS** — entire implementation in `skills/chrome-cdp/scripts/cdp.mjs`.

This is the implementation template for BeigeBox's `tools/cdp.mjs`.

---

## Reference: Chrome DevTools MCP Tool Inventory (27 tools)

For reference — these are the capabilities available via CDP. BeigeBox's Phase 1 implements the most useful subset as CLI commands.

**Input (8):** click, drag, fill, fill_form, handle_dialog, hover, press_key, upload_file  
**Navigation (7):** close_page, list_pages, navigate_page, navigate_page_history, new_page, select_page, wait_for  
**Snapshot (2):** take_screenshot, take_snapshot  
**Network (2):** list_network_requests, get_network_request  
**Performance (2):** start_performance_trace, get_performance_trace  
**Emulation (3):** emulate_cpu, emulate_network, resize_page  
**Debug (3):** evaluate_script, get_console_messages, list_page_resources  

Phase 1 covers: list, snap(shot), screenshot, html, eval, nav, net, click, type — roughly 60% of the useful surface area in 9 commands.

---

## Next Steps

1. **Phase 1 implementation** — `tools/cdp.mjs` + operator allowlist update. Attach codebase zip in implementation session.
2. **Test against BeigeBox web UI** — verify CDP can drive the existing web UI end-to-end.
3. **Integrate with operator agent** — add CDP commands to operator's tool dispatch.
4. **Build audit toolkit** — package for consulting demos.
5. **Autoresearch tie-in** — connect CDP test suite as the eval harness for web UI optimization loop.
