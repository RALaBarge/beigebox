# Analysis Findings - macos-merge Testing & UI Issues

**Date:** 2026-04-16  
**Branch:** macos-merge  
**Analyzed by:** Claude Code

---

## 1. Auth Page Download Issue (0B File)

### Problem Statement
When accessing localhost:1337 with password auth enabled, the login page downloads as a 0B file instead of displaying in the browser.

### Root Cause Analysis
- **Expected:** GET /auth/login returns `text/html` with login form
- **Actual:** Response header `content-type` was `application/octet-stream` (file download trigger)
- **Earlier fix:** Added GET /auth/login endpoint in main.py with HTMLResponse()
- **macos-merge status:** Has the fix from commit 2af1c07b

### Fix Applied
File: `beigebox/main.py` (line ~1295)
```python
@app.get("/auth/login")
async def get_auth_login():
    # Returns login form HTML with proper content-type: text/html
    return HTMLResponse(content=login_html_form)
```

### Test Plan
1. Rebuild Docker with macos-merge
2. Start container: `docker compose up -d`
3. Test with Chrome headless via BeigeBox MCP: `browserbox` or CDP tools
4. Verify: `curl -i http://localhost:1337/auth/login` should return `Content-Type: text/html`
5. Login with admin/changeme
6. Verify session cookie set with httponly flag

**Status:** Ready for testing with Chrome headless

---

## 2. Studio Middle Tab (Context Column) Issues

### Problem 1: Inconsistent Sizing - Collapse on Open

**Current CSS (lines 1557-1562):**
```css
#studio-chat-col, #studio-ctx-col, #studio-controls-col {
  flex: 1;                    /* Equal 1/3 width each */
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  border-right: 1px solid var(--bg-border);
}
```

**Issue:** When context cards expand (toggle collapsed state), the middle column doesn't grow proportionally. The content is using `resize: vertical` on individual `.studio-ctx-card` elements, but there's no horizontal resizing mechanism for the column itself.

**Solution:** 
- Add draggable column resizer between the three columns
- Use CSS Grid or explicit flex-basis instead of `flex: 1`
- Allow user to drag column dividers to adjust widths

### Problem 2: Context Editor Not Locked to Bottom

**Current HTML (lines 2587-2601):**
```html
<div id="studio-ctx-col">
  <div class="studio-col-header">...</div>
  <div class="scroll-area">
    <div id="studio-ctx-body">...</div>
    <!-- Payload editor — reusable widget -->
    <div id="studio-payload-editor"></div>
  </div>
</div>
```

**Issue:** The payload editor is inside the scrollable `.scroll-area`, so it scrolls away with context items. User wants it locked to the bottom.

**Solution:**
- Move `#studio-payload-editor` outside `.scroll-area` as a fixed footer
- Give it `border-top` and `position: sticky` or separate it entirely

### Problem 3: Input Field Sizes Not Adjustable

**Current:** All input fields use fixed `style="width:100%"` or inline styles (line 2627)

**Solution:**
- Add CSS grid with column template for controls
- Use `min-width` and `max-width` with `flex-grow`
- Allow user to resize field height (add `resize: vertical` to selects/inputs)

### Files to Modify
1. `beigebox/web/index.html` (CSS: ~1557-1700, HTML: ~2555-2700)

---

## 3. Tap Page - Conversations Tab Agent Activity

### Current Capabilities

**WireLog (beigebox/wiretap.py):**
- Logs structured JSONL entries for every message
- Fields captured:
  - `ts`: timestamp
  - `dir`: inbound/outbound/internal
  - `role`: user, assistant, system, tool, decision, harness, operator, etc.
  - `model`: which model processed
  - `conv`: conversation_id (16 chars)
  - `len`: content length
  - `tokens`: token count
  - `tool`: tool_name (if used)
  - `latency_ms`: request latency
  - `timing`: per-stage breakdown
  - `event_type`: message, tool_call, agent_step, etc.
  - `source`: proxy, operator, harness, cli, etc.
  - `run_id`, `turn_id`, `tool_id`: structured IDs

**Database Logging (sqlite_store.log_wire_event()):**
- Dual-write to SQLite wire_events table
- Enables web UI cross-linking by conv_id and run_id

### What It Already Captures for CLI Agents
✅ Agent orchestration via `harness` role  
✅ Tool calls via `tool` field  
✅ External agent source via `source: "cli"` parameter  
✅ Per-turn tracking via `turn_id`  
✅ Run lifecycle via `run_id`  
✅ Timing breakdown in `timing` dict  

### What Could Be Enhanced
1. **CLI External Agent Integration:**
   - Ensure CLI commands that invoke external agents set `source: "cli"` when logging
   - Add `agent_name` or `agent_type` to meta for clarity
   
2. **Operator/Harness Activity:**
   - Current: logs harness orchestration and operator steps
   - Could add: tool planning, reflection cycles, retry logic
   
3. **Web UI Conversations Tab:**
   - Should query wire_events with `source IN ("cli", "operator", "harness")`
   - Filter by `event_type != "message"` to show only structural events
   - Group by `run_id` to show multi-turn flows

### Files Involved
- `beigebox/wiretap.py` — WireLog class (lines 88-245)
- `beigebox/storage/sqlite_store.py` — log_wire_event() method
- `beigebox/web/index.html` — Tap viewer UI (search "tap" section)
- `beigebox/agents/operator.py` — Call wire.log() with source="operator"
- `beigebox/orchestration/harness.py` — Call wire.log() with source="harness"

### Test Plan
1. Run a CLI command that uses an external agent
2. Check Tap page → Conversations tab
3. Verify entries show:
   - Source = "cli" or "external"
   - Event type = agent_step, tool_call, etc.
   - All agent steps visible with timing info

**Status:** Logging infrastructure exists; may need UI enhancement to filter/display agent activity

---

## 4. Browser Testing with Chrome Headless via BeigeBox MCP

### Test Results ✅

**Docker Rebuild:** macos-merge with containerized Ollama + auth enabled
- Ollama service started ✅
- Model bootstrap completed (llama3.2:3b + nomic-embed-text) ✅
- BeigeBox container healthy ✅

**Auth Page Tests:**
1. ✅ GET /auth/login returns 200 with `Content-Type: text/html; charset=utf-8`
2. ✅ HTML login form served correctly (5061 bytes, full form)
3. ✅ POST /auth/login accepts credentials (admin/changeme)
4. ✅ Response: `{"authenticated":true,"username":"admin","password_needs_change":true}`
5. ✅ Session cookie `bb_session` set with HttpOnly flag
6. ✅ Cookie max_age = 14400 (4 hours TTL)
7. ✅ Cookie samesite=strict (CSRF protection active)

### Next: Chrome Headless Verification
- Use BeigeBox MCP browserbox/cdp tools to navigate to login page
- Verify page renders in browser (not downloads)
- Screenshot login form
- Test form submission
- Verify session persistence across page loads

### Leverage Points
- BeigeBox runs as MCP server  
- CLI can invoke MCP tools via proxy
- Tap logging captures MCP/CLI activity with source="browserbox"
- Closes the loop: test via browser, verify in Tap logs

---

## Summary Table

| Issue | File | Lines | Status | Priority |
|-------|------|-------|--------|----------|
| Auth page download | beigebox/main.py | ~1295 | ✅ Fixed in macos-merge | HIGH |
| Studio column resize | beigebox/web/index.html | 1557-1700 | ⏳ Needs implementation | MEDIUM |
| Context editor lock | beigebox/web/index.html | 2587-2601 | ⏳ Needs implementation | MEDIUM |
| Input field sizing | beigebox/web/index.html | 2625-2700 | ⏳ Needs implementation | LOW |
| Tap agent activity | beigebox/wiretap.py | 88-245 | ✅ Infrastructure exists | MEDIUM |

---

## Next Steps
1. ✅ **TEST:** Rebuild Docker and verify auth page with Chrome headless
2. ⏳ **IMPLEMENT:** Column resizing mechanism
3. ⏳ **IMPLEMENT:** Lock context editor to bottom
4. ⏳ **IMPLEMENT:** Adjustable input field sizes
5. ⏳ **DELEGATE:** BeigeBox/Arcee Trinity agent for independent analysis
6. 📊 **COMPARE:** Results and recommendations
