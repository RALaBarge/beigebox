# ✅ COMPLETE — Reference document. 13 agentic tool-prompting patterns noted and available for operator/harness improvements. No further action required.

# agentic tool use: prompting patterns for reliable tool calls

> Compiled from current best practices across OpenAI function calling, Anthropic tool use, BrowserBox integration design, and practitioner patterns from the autoresearch/CDP analysis sessions.

**Status**: Design document / TODO  
**Date**: 2026-03-15  
**Priority**: High — directly affects operator agent reliability, BrowserBox integration, and consulting demos  

---

## The Problem

An agent that can't reliably pick the right tool with the right arguments is useless. The difference between "works in demos" and "works overnight unattended" (autoresearch-style) comes down to how tools are presented to the model. The schema is the easy part. The prompting around the schema determines success or failure.

This document covers three layers:
1. **Tool schema design** — how to structure the JSON objects the model sees
2. **System prompt patterns** — how to wrap schemas with workflow knowledge
3. **Integration architecture** — how BeigeBox should wire tool schemas to models, with BrowserBox as the primary case study

---

## Layer 1: Tool Schema Design

### The Standard: OpenAI Function Calling Format

This is the de facto wire format that every OpenAI-compatible endpoint understands, including everything BeigeBox proxies:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "dom.snapshot",
        "description": "Returns a structural snapshot of the active tab's DOM including title, URL, and a simplified element tree. Use this FIRST when you need to understand page structure before interacting with elements.",
        "parameters": {
          "type": "object",
          "properties": {
            "selector": {
              "type": "string",
              "description": "Optional CSS selector to scope the snapshot to a subtree. Omit for full page."
            }
          },
          "required": []
        }
      }
    }
  ]
}
```

The model returns a `tool_calls` array in its response. You execute the tool, feed the result back as a `tool` role message. The round-trip:

```
user message → model response with tool_calls → execute tool → tool result message → model final response
```

Every major provider (OpenAI, Anthropic, Google, Ollama, vLLM, llama.cpp) has converged on this format or a close variant. BeigeBox already speaks this protocol natively.

### Pattern 1: Descriptions as Decision Logic, Not Documentation

Most people write tool descriptions like API reference docs. What actually works is writing them like **decision rules** — when to use this tool, when NOT to, and what to expect:

```json
{
  "name": "fetch.get",
  "description": "Make an HTTP GET request from the browser context. Carries session cookies automatically — use this instead of external HTTP when you need authenticated access. Do NOT use for pages you can navigate to with nav.go; prefer nav.go + dom.snapshot for browsable pages. Returns raw response body as string. Timeout: 10s."
}
```

The negative constraint ("Do NOT use when...") is as important as the positive description. Models are highly responsive to exclusion rules in tool descriptions. The pattern:

```
[What it does] + [When to use it] + [When NOT to use it] + [What it returns] + [Constraints/limits]
```

**Bad description:**
```
"description": "Gets the text content of an element"
```

**Good description:**
```
"description": "Extract visible text content from a DOM element matching a CSS selector. Returns only human-visible text (no hidden elements, no script content). Use after dom.snapshot when you need the full text of a specific element identified in the snapshot. Returns empty string if selector matches nothing — do NOT retry, check your selector against the snapshot."
```

### Pattern 2: Enum Constraints Over Free-Text

Anywhere you can constrain input to a finite set of valid values, use an enum. Models hallucinate values for free-text fields. They almost never hallucinate outside an enum:

```json
{
  "name": "nav.go",
  "parameters": {
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "description": "Full URL including protocol (https://...)"
      },
      "wait_until": {
        "type": "string",
        "enum": ["load", "domcontentloaded", "networkidle"],
        "description": "When to consider navigation complete. 'load' for full page, 'domcontentloaded' for faster on JS-heavy sites, 'networkidle' for SPAs. Default: load"
      }
    },
    "required": ["url"]
  }
}
```

Apply this aggressively:
- HTTP methods → `enum: ["GET", "POST", "HEAD"]`
- Output formats → `enum: ["json", "text", "base64"]`
- Severity levels → `enum: ["error", "warn", "info"]`
- Storage scopes → `enum: ["local", "session"]`

Even for fields that *technically* accept arbitrary strings, if 90% of real usage hits 4-5 values, providing an enum with a "custom" escape hatch is better than pure free-text:

```json
{
  "viewport": {
    "type": "string",
    "enum": ["mobile", "tablet", "desktop", "custom"],
    "description": "Preset viewport size, or 'custom' to use width/height params"
  }
}
```

### Pattern 3: Strict Mode + additionalProperties: false

OpenAI and Anthropic both support `strict: true` on function parameters. This forces the model to output JSON that validates against the schema exactly, eliminating the entire class of "almost right but malformed" tool calls:

```json
{
  "type": "function",
  "function": {
    "name": "dom.fill",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "selector": {
          "type": "string",
          "description": "CSS selector for the input element to fill"
        },
        "value": {
          "type": "string",
          "description": "Text value to set on the element"
        }
      },
      "required": ["selector", "value"],
      "additionalProperties": false
    }
  }
}
```

The `additionalProperties: false` + `strict: true` combo means:
- The model can't add fields you didn't ask for
- The model can't omit required fields
- Types are enforced (no string where number is expected)
- The response is guaranteed to parse without error

**Caveat**: strict mode is not universally supported across all backends. BeigeBox should set strict when the downstream model supports it and fall back gracefully when it doesn't. This is a good candidate for the WASM transform pipeline — a pre-flight validation module that checks tool call JSON against the schema before forwarding.

### Pattern 4: Hierarchical Naming Conventions

BrowserBox already does this with `namespace.method` naming (`dom.snapshot`, `fetch.get`, `nav.go`). This is significantly better than flat names for two reasons:

1. **Models cluster related tools by prefix.** When the model sees `dom.snapshot`, `dom.query`, `dom.click`, it understands these operate on the same domain. This improves tool selection accuracy in multi-tool environments.

2. **Easier to implement skill-gated loading.** You can load all `dom.*` tools without loading `network.*` tools by filtering on namespace prefix.

The naming convention for BeigeBox operator tools should follow this pattern consistently:

```
browser.dom.snapshot      # BrowserBox DOM tools
browser.fetch.get         # BrowserBox fetch tools
browser.tabs.list         # BrowserBox tab tools
shell.exec                # Operator shell execution
search.web                # DuckDuckGo / web search
search.vector             # ChromaDB semantic search
memory.store              # Conversation memory
memory.recall             # Conversation memory retrieval
```

### Pattern 5: Structured Tool Results with Hints

The result coming back from a tool call matters as much as the schema going in. If your tool returns a wall of unstructured text, the model struggles to extract what it needs for the next step.

**Bad result:**
```json
{"result": "<html><head><title>Login Page</title></head><body><form>...</form></body></html>"}
```

**Good result:**
```json
{
  "status": "success",
  "data": {
    "title": "Login Page",
    "url": "https://app.example.com/login",
    "elements": [
      {"tag": "input", "type": "email", "selector": "#email", "placeholder": "Email address"},
      {"tag": "input", "type": "password", "selector": "#password", "placeholder": "Password"},
      {"tag": "button", "type": "submit", "selector": "#login-btn", "text": "Sign In"}
    ]
  },
  "metadata": {
    "elapsed_ms": 142,
    "truncated": false,
    "element_count": 3
  },
  "hint": "Login form found with email and password fields. Use dom.fill with selector '#email' and '#password', then dom.click with selector '#login-btn'."
}
```

The `hint` field is not in any spec, but practitioners are finding it dramatically improves multi-step tool chains. It's a tool whispering to the model what to do next. The model treats it as context, not instruction — it can override the hint if it has reason to, but in practice it follows hints ~90% of the time.

**BrowserBox implementation note**: The adapters (`dom.js`, `tabs.js`, etc.) should return structured JSON with optional hints. The relay (`ws_relay.py`) passes them through unchanged. The hint generation can be simple — pattern-match on common page structures (login form → suggest fill + click, search box → suggest fill + submit, table → suggest snapshot or query_all).

### Pattern 6: Error Results That Guide Recovery

When a tool fails, the error message should tell the model what to try instead:

**Bad error:**
```json
{"error": "element not found"}
```

**Good error:**
```json
{
  "status": "error",
  "error": "No element matching selector '#submit-button' found in the active tab.",
  "recovery_hint": "The page structure may have changed. Take a fresh dom.snapshot to see current elements, then retry with the correct selector from the snapshot.",
  "context": {
    "selector_tried": "#submit-button",
    "page_url": "https://app.example.com/dashboard",
    "page_title": "Dashboard"
  }
}
```

This prevents the model from doing what it naturally wants to do when it hits an error: retry the exact same call, or hallucinate a different selector. The recovery hint redirects it to the correct action (re-snapshot the page).

---

## Layer 2: System Prompt Patterns

### Pattern 7: Tool Use Planning in System Prompt

Instead of just listing tools and hoping the model figures out sequencing, encode workflow knowledge in the system prompt:

```
When interacting with browser pages, follow this sequence:
1. dom.snapshot → understand the page structure first
2. Identify elements by their CSS selectors from the snapshot
3. dom.click / dom.fill → interact with specific elements
4. dom.snapshot again → verify the interaction took effect

Never click or fill without taking a snapshot first.
Never assume a page has loaded — always use nav.go with wait_until before interacting.
If a selector doesn't match, take a fresh snapshot — the page may have updated.
After filling a form field, snapshot to verify the value was set before submitting.
```

This is what `program.md` / `SKILL.md` / `CLAUDE.md` files do — they're procedural instructions that wrap raw tool schemas with workflow knowledge. The model follows procedure more reliably than it discovers procedure from tool descriptions alone.

### Pattern 8: Tool Selection Rubric

When multiple tools can accomplish the same task, give the model a decision rubric:

```
To get content from a web page:
- If you need the page structure (elements, selectors): use dom.snapshot
- If you need the visible text only: use dom.get_text
- If you need raw HTML: use dom.get_html
- If you need to download a file or API response: use fetch.get
- If the page requires navigation first: use nav.go, wait for load, THEN snapshot

To interact with page elements:
- Click buttons/links: dom.click with CSS selector
- Fill text inputs: dom.fill with CSS selector and value
- Scroll the page: dom.scroll
- Wait for dynamic content: dom.wait_for with selector

Never use inject.js for read-only tasks — use dom.* tools instead.
Only use inject.js when you need to execute custom JavaScript that dom.* can't handle.
```

### Pattern 9: The "Available Tools" Context Block

Rather than dumping all tool schemas into the tools array on every request, provide a summary in the system prompt and load full schemas on demand:

```
You have access to the following tool namespaces:
- browser.dom: Inspect and interact with web page elements (snapshot, query, click, fill)
- browser.tabs: Manage browser tabs (list, open, close, screenshot)
- browser.nav: Navigate pages (go, back, forward, reload)
- browser.fetch: Make HTTP requests with session cookies (get, post, head)
- browser.network: Capture and inspect network traffic
- shell: Execute allowlisted shell commands
- search: Web search and vector search

To use a tool, output a tool call with the namespace.method name and a JSON input object.
```

This is the "skill-gated lazy loading" approach. The model sees the namespace list (cheap, ~100 tokens) and only loads the full schemas (~2k-5k tokens per namespace) when it decides to use a specific namespace. BeigeBox's plugin auto-discovery system can provide this summary automatically.

### Pattern 10: Few-Shot Tool Use Examples

For complex multi-step workflows, include one concrete example in the system prompt:

```
Example: Log into a web application

User: "Log into the admin panel at https://admin.example.com"

Step 1: Navigate
Tool call: nav.go {"url": "https://admin.example.com", "wait_until": "load"}
Result: {"status": "success", "data": {"url": "https://admin.example.com/login"}}

Step 2: Snapshot the page
Tool call: dom.snapshot {}
Result: {"status": "success", "data": {"elements": [
  {"selector": "#username", "type": "input", "placeholder": "Username"},
  {"selector": "#password", "type": "input", "placeholder": "Password"},
  {"selector": "#login-btn", "tag": "button", "text": "Login"}
]}}

Step 3: Fill credentials
Tool call: dom.fill {"selector": "#username", "value": "admin"}
Tool call: dom.fill {"selector": "#password", "value": "admin123"}

Step 4: Click login
Tool call: dom.click {"selector": "#login-btn"}

Step 5: Verify success
Tool call: dom.snapshot {}
Result: {"status": "success", "data": {"url": "https://admin.example.com/dashboard", "title": "Admin Dashboard"}}
```

One good example is worth more than 10 lines of procedural instructions. The model pattern-matches against examples more reliably than it follows abstract rules. But keep it to one example — multiple examples eat context and the model starts blending them.

---

## Layer 3: Integration Architecture

### How BeigeBox Should Wire BrowserBox

BrowserBox exposes tools via two endpoints:
- `GET http://localhost:9010/tools` — full schema discovery (HTTP)
- `ws://localhost:9009` — tool execution (WebSocket)

There are three levels of integration, in increasing sophistication:

### Level 1: Direct Tool Registration (Simple, Expensive)

BeigeBox reads BrowserBox's `/tools` endpoint at startup and registers every tool as an OpenAI function calling schema in the tools array on every request to the downstream model.

```python
# Pseudocode
async def register_browserbox_tools():
    schema = await http_get("http://localhost:9010/tools")
    for tool in schema["tools"]:
        register_openai_tool({
            "type": "function",
            "function": {
                "name": f"browser.{tool['name']}",
                "description": tool["description"],
                "parameters": tool["input_schema"]
            }
        })
```

**Pros**: Simple, works immediately, full schema validation by the model.

**Cons**: Pays context window cost for ALL tool definitions on every request. BrowserBox has ~30 tools. At ~200 tokens per tool schema, that's ~6k tokens burned on every turn whether the user asks about the browser or not. This is the exact problem the HN thread was complaining about with Chrome DevTools MCP (17k tokens for 26 tools).

**When to use**: Development/testing. Quick integration. Single-purpose operator sessions where browser access is the primary task.

### Level 2: Skill-Gated Lazy Loading (RECOMMENDED)

The operator agent sees a single meta-tool called `browser` in its tool list. When it decides to use the browser, BeigeBox loads the full BrowserBox tool schemas into context for that turn only.

```json
{
  "type": "function",
  "function": {
    "name": "browser",
    "description": "Access the live Chrome browser session. Provides DOM inspection, page navigation, form filling, clicking, screenshots, network capture, and HTTP requests with session cookies. When you need to use browser tools, call this with action='discover' first to see available tools, then call with the specific tool name and input.",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "description": "Tool name (e.g. 'dom.snapshot', 'nav.go') or 'discover' to list all tools"
        },
        "input": {
          "type": "object",
          "description": "Tool-specific input parameters (see discover output for schemas)"
        }
      },
      "required": ["action"]
    }
  }
}
```

**Pros**: Only ~200 tokens in context until browser tools are needed. Discover call loads schemas once per conversation. Matches BeigeBox's plugin auto-discovery pattern.

**Cons**: The model doesn't have full JSON schemas for parameter validation until after discovery. The wrapper tool is less precise than individual tool registrations.

**When to use**: Production operator agent sessions. Multi-tool environments where browser is one of many capabilities.

This is the pattern Amp uses for chrome-devtools-mcp. BeigeBox should implement this as the default.

### Level 3: Skill Document Only (Lightest, Most Fragile)

No tool schemas in context at all. The operator agent has a skill document (`2600/browserbox-operator-skill.md`) that describes the tools in prose with workflow examples. The agent constructs JSON tool calls based on the prose instructions, BeigeBox translates them to BrowserBox WebSocket calls.

```markdown
# BrowserBox Browser Tools

You can interact with the live Chrome browser by calling the `browser` tool.

## Available Actions

### dom.snapshot
Get a structural view of the current page. Always call this before interacting.
Input: `{"selector": "#optional-css-selector"}` or `{}`
Output: JSON with title, URL, and element tree.
...
```

**Pros**: Zero context cost until the skill doc is loaded. Works with models that don't support function calling (raw completion mode).

**Cons**: No schema validation — the model constructs JSON from prose, which is more error-prone. Requires the model to be good at following prose into structured output.

**When to use**: Very constrained context windows. Models without function calling support. As a supplement to Level 2, not a replacement.

### Recommended Implementation: Level 2 with Level 3 Fallback

```yaml
# config.yaml
tools:
  browserbox:
    enabled: true
    ws_url: ws://localhost:9009
    schema_url: http://localhost:9010/tools
    loading: lazy              # lazy (Level 2) | eager (Level 1) | skill (Level 3)
    skill_doc: 2600/browserbox-operator-skill.md
    timeout: 10
    
    # Per-namespace enable/disable
    namespaces:
      dom: true
      tabs: true
      nav: true
      fetch: true
      storage: false           # disabled by default — security sensitive
      clip: false              # requires Chrome focus — unreliable for agents
      network: true
      inject: false            # eval() in page context — opt-in only
      pdf: true
```

### The Translation Layer

BeigeBox needs a thin translation layer between the OpenAI tool call format and BrowserBox's WebSocket wire protocol:

```
Model outputs:
  tool_calls: [{"function": {"name": "browser.dom.snapshot", "arguments": "{\"selector\": \"#main\"}"}}]

BeigeBox translates to BrowserBox wire protocol:
  → {"id": "<uuid>", "tool": "dom.snapshot", "input": {"selector": "#main"}}

BrowserBox responds:
  ← {"id": "<uuid>", "result": "{\"title\": ..., \"url\": ..., \"elements\": [...]}"}

BeigeBox translates back to OpenAI tool result format:
  messages: [{"role": "tool", "tool_call_id": "...", "content": "{\"status\": \"success\", ...}"}]
```

This translation is ~50 lines of Python. It lives in the operator agent's tool dispatch, not in the proxy layer — BeigeBox the proxy doesn't know or care about BrowserBox. The operator agent does.

---

## Patterns Specific to Local Models (Ollama / llama.cpp)

Local models are less reliable at tool use than frontier models. Patterns that help:

### Pattern 11: Single Tool per Turn

Frontier models (GPT-4o, Claude Sonnet/Opus) handle parallel tool calls well. Local models (Llama 3.2, Qwen, Mistral) are much better when constrained to one tool call per turn:

```
IMPORTANT: Call only ONE tool at a time. Wait for the result before deciding the next action. Do not attempt parallel tool calls.
```

This increases round-trips but dramatically reduces tool call errors on local models.

### Pattern 12: Explicit JSON Examples in Descriptions

Local models benefit from seeing the exact JSON shape they should produce:

```json
{
  "name": "dom.fill",
  "description": "Fill a form field with a value. Example call: {\"selector\": \"#email\", \"value\": \"user@example.com\"}"
}
```

Frontier models don't need this — they infer from the schema. Local models sometimes need the example to get the JSON structure right.

### Pattern 13: Smaller Tool Sets

Local models with 3B-8B parameters degrade rapidly past ~5-6 tools. For the BeigeBox operator running against llama3.2:3b (the default), limit to the most essential tools per task:

```yaml
# config.yaml — tool profiles for different model tiers
tool_profiles:
  minimal:     # 3B models
    max_tools: 5
    tools: [shell.exec, search.web, search.vector, memory.recall, browser]
  standard:    # 7B-13B models  
    max_tools: 12
    tools: [shell.exec, search.web, search.vector, memory.store, memory.recall, browser.dom.snapshot, browser.dom.click, browser.dom.fill, browser.nav.go, browser.tabs.list, browser.tabs.screenshot, browser.fetch.get]
  full:        # 70B+ or API models
    max_tools: 30
    tools: all
```

The routing classifier could automatically select the tool profile based on which model is handling the request. This ties directly into the autoresearch routing optimization — the tool profile is part of the routing config that gets tuned.

---

## Applying to Autoresearch

The autoresearch pattern from the earlier document applies here too. The tool schema + system prompt is an **editable asset**. The scalar metric is tool call success rate against a holdout set of tasks. The eval harness runs the agent through N tasks and scores:

- Did it pick the right tool? (tool selection accuracy)
- Did it provide valid arguments? (schema compliance)
- Did the multi-step chain complete? (task completion rate)
- How many turns did it take? (efficiency)
- What was the total cost? (token efficiency)

The agent in the autoresearch loop modifies the tool descriptions, system prompt workflow instructions, and tool selection rubric. The evaluation harness replays the same tasks and scores the results. Overnight, the loop grinds through description rewrites, prompt variations, and tool presentation strategies.

This is one of the most directly valuable autoresearch applications for BeigeBox — the tool prompting configuration is config-driven, the eval is deterministic (did the task succeed?), and improvements compound across every operator session.

---

## Checklist: Designing a New Tool for BeigeBox

When adding any new tool to the operator agent's repertoire:

- [ ] **Name**: `namespace.method` format, lowercase, descriptive
- [ ] **Description**: Includes when to use, when NOT to use, what it returns, and constraints
- [ ] **Parameters**: Enums where possible, `required` array correct, `additionalProperties: false`
- [ ] **Strict mode**: Set `strict: true` if downstream model supports it
- [ ] **Result format**: Structured JSON with `status`, `data`, `metadata`, optional `hint`
- [ ] **Error format**: Includes `recovery_hint` and `context` for the model to self-correct
- [ ] **Negative examples**: Description says what NOT to do with this tool
- [ ] **Workflow placement**: System prompt updated with where this tool fits in the sequence
- [ ] **Tool profile**: Added to appropriate profile tiers (minimal/standard/full)
- [ ] **Audit logging**: Tool calls and results logged to wiretap
- [ ] **Timeout**: Configured with sensible default, overridable in config
- [ ] **Security**: Reviewed against operator sandbox constraints (allowlist, bubblewrap)

---

## Reference: BrowserBox Tool Schema (Current)

From `GET http://localhost:9010/tools`, mapped to OpenAI function calling format:

| Tool | Input Schema | Notes |
|---|---|---|
| `dom.snapshot` | `{selector?: string}` | Always call first. Returns structured element tree. |
| `dom.query` | `{selector: string}` | Single element by CSS selector |
| `dom.query_all` | `{selector: string}` | All matching elements |
| `dom.get_text` | `{selector?: string}` | Visible text only |
| `dom.get_html` | `{selector?: string}` | Raw HTML |
| `dom.get_url` | `{}` | Current page URL |
| `dom.get_title` | `{}` | Current page title |
| `dom.click` | `{selector: string}` | Click element |
| `dom.fill` | `{selector: string, value: string}` | Set input value |
| `dom.scroll` | `{direction?: "up"\|"down", amount?: number}` | Scroll page |
| `dom.wait_for` | `{selector: string, timeout?: number}` | Wait for element to appear |
| `tabs.list` | `{}` | All open tabs |
| `tabs.get_current` | `{}` | Active tab info |
| `tabs.open` | `{url: string}` | Open new tab |
| `tabs.close` | `{tabId: number}` | Close tab |
| `tabs.switch` | `{tabId: number}` | Switch to tab |
| `tabs.screenshot` | `{}` | Screenshot → data URL |
| `nav.go` | `{url: string}` | Navigate active tab |
| `nav.back` | `{}` | Browser back |
| `nav.forward` | `{}` | Browser forward |
| `nav.reload` | `{}` | Reload page |
| `fetch.get` | `{url: string, headers?: object}` | GET with session cookies |
| `fetch.post` | `{url: string, body?: string, headers?: object}` | POST with session cookies |
| `fetch.head` | `{url: string}` | HEAD request |
| `storage.get` | `{key: string}` | chrome.storage read |
| `storage.set` | `{key: string, value: any}` | chrome.storage write |
| `storage.delete` | `{key: string}` | chrome.storage delete |
| `storage.list` | `{}` | All storage keys |
| `storage.get_cookie` | `{name: string, url: string}` | Read cookie |
| `storage.list_cookies` | `{url: string}` | All cookies for URL |
| `clip.read` | `{}` | Read clipboard (needs focus) |
| `clip.write` | `{text: string}` | Write clipboard (needs focus) |
| `network.start_capture` | `{}` | Start intercepting fetch/XHR |
| `network.stop_capture` | `{}` | Stop capture |
| `network.get_captured` | `{}` | Return captured requests |
| `network.clear` | `{}` | Clear capture buffer |
| `inject.js` | `{code: string}` | eval() in page MAIN world (CSP-restricted) |
| `inject.css` | `{css: string}` | Inject CSS |
| `inject.css_remove` | `{id: string}` | Remove injected CSS |
| `pdf.extract` | `{url: string}` | Fetch PDF with cookies → base64 |

**Total**: 37 tools across 9 namespaces. At ~200 tokens per schema, that's ~7.4k tokens if loaded eagerly. This is why Level 2 (lazy loading) is the right default.

---

## Relationship to Other BeigeBox Work

### Autoresearch (autoresearch-beigebox.md)

Tool prompting configuration is an autoresearch-eligible editable asset. The system prompt + tool descriptions + workflow instructions can be optimized overnight against a task completion benchmark.

### CDP Browser Automation (cdp-browser-automation-beigebox.md)

CDP is the fallback for headless/CI scenarios where BrowserBox's Chrome extension isn't available. The same tool schema patterns apply — the `tools/cdp.mjs` CLI should output structured JSON with hints and recovery hints, matching the patterns described here.

### BrowserBox (github.com/RALaBarge/browserbox)

BrowserBox is the primary browser automation path. This document defines how its tools get presented to models through BeigeBox's operator agent. The integration is Level 2 lazy loading with Level 3 skill doc as fallback.

### Plugin Registry Formalization

The hierarchical `namespace.method` naming, per-namespace enable/disable, and tool profile tiers all feed into the universal plugin vtable/dispatch pattern. BrowserBox is one plugin; the same patterns apply to any future tool plugin (database tools, deployment tools, monitoring tools, etc.).

### WASM Transform Pipeline

Schema validation (strict mode enforcement, additionalProperties checking) is a natural fit for a WASM pre-flight module in the transform pipeline. The module validates tool call JSON against the registered schema before the call reaches the tool, catching malformed calls before they cause errors.

---

## Next Steps

1. **Implement Level 2 lazy loading** for BrowserBox in operator agent tool dispatch
2. **Write `2600/browserbox-operator-skill.md`** — the Level 3 skill document with workflow examples
3. **Add structured results with hints** to BrowserBox adapter outputs
4. **Add recovery hints** to BrowserBox error responses
5. **Build tool prompting autoresearch eval set** — 30-50 tasks that require browser tool use, scored on task completion + efficiency
6. **Test tool profiles** against llama3.2:3b vs larger models — validate the minimal/standard/full tier approach
7. **Integrate tool schema validation** into WASM pipeline as pre-flight check
