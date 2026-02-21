# BeigeBox: Genericity, Extensibility & Standards Review

**Date**: February 21, 2026  
**Status**: Architecture Assessment  
**Scope**: v0.9.2 release

---

## Executive Summary

**Strengths**: 
- ✅ Excellent backend abstraction (ABC pattern)
- ✅ Strong hook system (Python files, no restart needed)
- ✅ OpenAI API compatibility (universal standard)
- ✅ Config-driven feature flags (extensible, disabled by default)
- ✅ Single-file HTML (zero build, lightweight)

**Gaps**:
- ❌ Tool registry requires code edits (not config-driven like hooks)
- ❌ No middleware/plugin system for request/response pipeline
- ❌ Main.py is 1250+ lines with all endpoints mixed (should be modular)
- ❌ Web UI has zero mobile support (viewport meta exists but no responsive CSS)
- ❌ CLI is monolithic (not plugin-extensible)
- ❌ No clear extension point documentation

---

## Part 1: Genericity & Extensibility Analysis

### What We're Doing Well ✅

#### 1. Backend Abstraction (Excellent)

**Location**: `beigebox/backends/base.py`

```python
class BaseBackend(abc.ABC):
    @abc.abstractmethod
    async def forward(self, body: dict) -> BackendResponse
    @abc.abstractmethod
    async def forward_stream(self, body: dict)
    @abc.abstractmethod
    async def health_check(self) -> bool
    @abc.abstractmethod
    async def list_models(self) -> list[str]
```

**What's great**:
- Clear interface (ABC)
- Minimal required methods (4)
- Standardized response format (BackendResponse dataclass)
- Easy to add new backend: Replicate `beigebox/backends/ollama.py`, implement 4 methods, register in router
- Multi-backend router with priority + failover

**How it works**:
- User adds `CustomBackend(BaseBackend)` in new file
- Registers in `beigebox/backends/router.py`
- Config specifies which backends are active and in what order
- No changes to proxy, main.py, or core logic needed

**Could be better**: Backend list isn't auto-discovered (requires manual registration in router)

---

#### 2. Hook System (Excellent)

**Location**: `beigebox/hooks.py`

```python
# Drop in hooks/ directory:
def pre_request(body: dict, context: dict) -> dict:
    # Modify request before backend sees it
    return body

def post_response(body: dict, response: dict, context: dict) -> dict:
    # Modify response before client sees it
    return response
```

**What's great**:
- Zero code changes needed to add a hook
- Python file drop-in (no restart needed if hotload works)
- Graceful failure: broken hook is logged and skipped
- Rich context passed to hooks (conversation_id, model, decision, config)
- Multiple hooks can run in sequence
- Both pre and post hooks optional

**How it works**:
- User creates `hooks/my_filter.py` with hook functions
- `HookManager` loads all .py files from hooks/ directory
- Config lists which hooks to enable/disable
- Pre-request hooks can add features (sanitization, logging, tool injection)
- Post-response hooks can modify responses (formatting, filtering, caching)

**Real example**: `hooks/prompt_injection.py` detects and flags/blocks jailbreaks

**Could be better**: 
- No hook ordering guarantees (though execution is sequential)
- Context is a dict (no type safety)
- Hook modules can't depend on each other easily

---

#### 3. Tool Registry (Good but Improvable)

**Location**: `beigebox/tools/registry.py`

**Current approach**:
```python
class ToolRegistry:
    def __init__(self, vector_store=None):
        # Each tool is manually instantiated based on config
        if tools_cfg.get("web_search", {}).get("enabled"):
            self.tools["web_search"] = WebSearchTool(...)
        if tools_cfg.get("calculator", {}).get("enabled"):
            self.tools["calculator"] = CalculatorTool()
```

**What's good**:
- Config-driven (features disabled by default)
- Each tool is self-contained (simple .run(input) interface)
- All tools pass through notifier (webhook integration)
- Registry is centralized (operator, decision LLM, orchestrator all use it)

**What's not great**:
- Adding a new tool requires:
  1. Write tool class
  2. Import it in registry.py
  3. Add if/elif block in ToolRegistry.__init__
  4. Add config section to config.yaml
- **Not plugin-like**: User can't drop in a tool without editing registry.py
- **Monolithic registry**: 129 lines of if/elif conditionals

**How it should work** (like hooks):
```python
# hooks/my_custom_tool.py
class MyTool:
    description = "My custom tool"
    def run(self, input: str) -> str:
        return "result"

# Register via config:
# tools:
#   custom_tool:
#     enabled: true
#     class: hooks.my_custom_tool:MyTool
```

---

#### 4. OpenAI API Compatibility (Excellent)

**Location**: `beigebox/proxy.py` + `beigebox/main.py`

**What's great**:
- Accepts OpenAI-format requests (messages array, model, stream, etc.)
- Returns OpenAI-format responses
- Works with any OpenAI SDK without modification
- Transparent to frontends (Open WebUI, LiteLLM, etc. just work)
- Extensible: Non-standard endpoints pass through to backend (catch-all)

**This is genericity at its best**: The world uses OpenAI API as lingua franca. Supporting it means supporting everything.

---

#### 5. Config-Driven Features (Excellent)

**Location**: `config.yaml` + `beigebox/config.py`

**What's great**:
```yaml
# All features disabled by default
cost_tracking:
  enabled: false

flight_recorder:
  enabled: false

semantic_map:
  enabled: false

hooks:
  - name: prompt_injection
    enabled: false
    mode: flag  # or "block"
```

**Principle**: Enable features by user choice, not by default. Keeps deployment safe.

**Hot-reload support**: `runtime_config.yaml` changes apply without restart via mtime check.

---

#### 6. Data Model Portability (Good)

**Location**: `beigebox/storage/models.py`

```python
@dataclass
class Message:
    def to_openai_format(self) -> dict:
        """Export in OpenAI messages array format (the portable standard)."""
        return {
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "timestamp": self.timestamp,
        }
```

**What's great**:
- Messages can be exported to OpenAI format
- Makes it easy to port conversations to other systems
- SQLite is standard (not proprietary database)
- JSONL wire log is human-readable

---

### What Others Do That We Should Consider

#### 1. Plugin System with Entry Points (Poetry/setuptools)

**What**: Define extension points in pyproject.toml instead of hardcoding

**Example** (from other projects):
```toml
[project.entry-points."beigebox.backends"]
custom = "my_package:MyBackend"

[project.entry-points."beigebox.tools"]
custom = "my_package:MyTool"
```

Then auto-discover at runtime:

```python
from importlib.metadata import entry_points

backends = entry_points(group="beigebox.backends")
for ep in backends:
    backend_cls = ep.load()
    register(ep.name, backend_cls)
```

**Benefit**: Users can package custom backends/tools as separate packages without forking BeigeBox.

**Where to apply**: Tools registry (most urgent), then backends (less urgent since backends are less common)

---

#### 2. Middleware Pipeline (FastAPI standard)

**What**: Chain pre/post processing as FastAPI middleware instead of custom hook system

**Current approach** (custom):
- HookManager loads Python files
- Runs pre_request, then proxy, then post_response

**Better approach** (FastAPI standard):
```python
from fastapi import Request

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    response = await call_next(request)
    # Log response
    return response

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Check auth
    response = await call_next(request)
    return response
```

**Benefit**: 
- Uses standard FastAPI middleware (developers know it)
- Integrates with FastAPI testing
- Can be packaged as separate packages
- More powerful (access to FastAPI context, dependency injection)

**Could co-exist**: Keep hooks for simple Python-file-based extensions, add middleware for power users.

---

#### 3. Modular Endpoint Organization

**What**: Split main.py endpoints into separate files/modules by domain

**Current state**: `main.py` is 1250+ lines with 40+ endpoints mixed together

```python
# Current structure:
# beigebox/main.py
#   - Chat completion (proxy core)
#   - Web UI serving
#   - Config endpoints
#   - Stats endpoints
#   - Operator endpoints
#   - Harness endpoints
#   - Audio endpoints
#   - File endpoints
#   - Ollama endpoints
#   - Catch-all passthrough
```

**Better structure**:
```python
# beigebox/main.py (core, 200 lines)
from beigebox.api import core, config, stats, operator, harness, media

app = FastAPI()
app.include_router(core.router)      # /v1/chat/completions
app.include_router(config.router)    # /api/v1/config
app.include_router(stats.router)     # /api/v1/stats
app.include_router(operator.router)  # /api/v1/operator
app.include_router(harness.router)   # /api/v1/harness/orchestrate
app.include_router(media.router)     # /v1/audio/*, /v1/vision/*
```

**Benefit**:
- Each endpoint module is <200 lines (readable)
- Easier to understand, test, modify
- Can load/unload endpoint groups via config
- Follows FastAPI best practices

---

#### 4. Type Hints & Pydantic Models

**What**: Use Pydantic models for all request/response validation

**Current state**: Mostly dict-based with some Pydantic usage

**Better approach**:
```python
from pydantic import BaseModel

class ChatCompletionRequest(BaseModel):
    messages: list[Message]
    model: str
    stream: bool = False
    temperature: float = 0.7
    
@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    # FastAPI validates JSON against model
    # Type hints throughout code
    # Auto-generates OpenAPI docs
```

**Benefits**:
- Validates input automatically (security)
- Type safety (IDE hints, mypy)
- Auto-generated OpenAPI/Swagger docs (free documentation)
- Better error messages to clients

**Current gap**: Many endpoints take raw dict from `request.json()`

---

#### 5. Dependency Injection (FastAPI Depends)

**What**: Use FastAPI's Depends for shared resources instead of globals

**Current state**: Global objects set in lifespan
```python
proxy: Proxy | None = None
sqlite_store: SQLiteStore | None = None
```

**Better approach**:
```python
def get_proxy() -> Proxy:
    return proxy  # initialized in lifespan

def get_store() -> SQLiteStore:
    return sqlite_store

@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    proxy: Proxy = Depends(get_proxy),
    store: SQLiteStore = Depends(get_store),
):
    # Cleaner, testable, type-safe
```

**Benefit**: Makes testing easier, dependencies explicit.

---

#### 6. Standard Logging Setup

**What**: Use Python's logging module properly

**Current state**: Some logging, but no structured logging

**Better approach** (like modern apps):
```python
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
logger.info("request_received", model="llama3", tokens=42)
```

**Benefit**: Structured logs are parseable, queryable, better for monitoring.

**Current issue**: Logs are somewhat free-form strings.

---

### Standards Adherence Summary

| Standard | Status | Notes |
|----------|--------|-------|
| **OpenAI API compatibility** | ✅ Excellent | Universal standard, works with any SDK |
| **REST conventions** | ✅ Good | Clear endpoint structure, mostly sensible |
| **Python best practices** | ⚠️ Medium | Some globals, minimal type hints, mixed logging |
| **FastAPI patterns** | ⚠️ Medium | Using FastAPI but not leveraging Depends, Pydantic fully |
| **Extensibility (hooks)** | ✅ Good | Plugin-like system, low friction |
| **Extensibility (backends)** | ✅ Good | Clean ABC, but no auto-discovery |
| **Extensibility (tools)** | ❌ Poor | Requires code edits to add tools |
| **Extensibility (CLI)** | ❌ Poor | Monolithic, no plugin system |
| **Extensibility (endpoints)** | ❌ Poor | main.py is one big file, hard to extend |
| **Configuration** | ✅ Excellent | Config-driven, disabled by default, hot-reloadable |
| **Documentation** | ✅ Good | README is thorough, in-code comments good |

---

## Part 2: Mobile Web UI Assessment

### Current State

**File**: `beigebox/web/index.html` (3070 lines)

**Viewport setup** (good start):
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0">
```

**CSS observations**:
- ✅ Flexbox layout (responsive foundation)
- ✅ CSS variables for theming
- ✅ Relative sizing (not fixed pixels everywhere)
- ❌ **Zero media queries** (`@media` not in file)
- ❌ Minimum widths hardcoded: `minmax(220px, 1fr)` on grid
- ❌ Assumes fixed 40px header, assuming mouse-friendly tab bar
- ❌ No touch-friendly input sizes
- ❌ Monospace font may be tiny on mobile
- ❌ 8-column tab bar (1=Dashboard, 2=Chat, ..., 8=Config) impossible on phone

### Mobile Blockers

1. **Tabs (8 of them)**
   - Desktop: All visible in header (easy to click)
   - Mobile: Text shrinks to unreadable, not swipe-friendly

2. **Grid cards**
   - `grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))` - 220px minimum won't fit phone

3. **Input fields & buttons**
   - Designed for mouse (small hit targets)
   - Need 44px+ on touch devices

4. **Multi-pane chat**
   - Desktop: 2 panes side-by-side (easy split-screen)
   - Mobile: Must stack vertically, single pane focus mode, tab switcher

5. **Sidebar/navigation**
   - No mobile drawer, no hamburger menu

### Solution: Minimal CSS-Only Approach

**Key insight**: Don't need a separate mobile view. Just add CSS media queries.

**Recommended breakpoints**:
```css
/* Desktop: 1200px+ (existing design) */
/* Tablet: 768px - 1199px (slight adjustments) */
@media (max-width: 1199px) { ... }
/* Mobile: <768px (major layout changes) */
@media (max-width: 767px) { ... }
```

### Minimal Mobile CSS Changes

```css
/* Mobile-first: start small, add to desktop */

/* On mobile, make tabs scrollable or use dropdown */
@media (max-width: 767px) {
  #tabs {
    overflow-x: auto;  /* Horizontal scroll tabs */
    -webkit-overflow-scrolling: touch;  /* Smooth scroll on iOS */
  }
  .tab {
    padding: 8px 12px;  /* Smaller tabs */
    font-size: 11px;    /* Tiny font OK for scrolling */
    white-space: nowrap;
  }
  
  /* Hide tab key indicator on mobile (takes space) */
  .tab .tab-key {
    display: none;
  }
}

/* Grid cards: 1 column on mobile, 2 on tablet, 3+ on desktop */
@media (max-width: 767px) {
  .card-grid {
    grid-template-columns: 1fr;  /* Single column */
  }
}

@media (max-width: 1199px) {
  .card-grid {
    grid-template-columns: repeat(2, 1fr);  /* Two columns */
  }
}

/* Chat panes: stack on mobile */
@media (max-width: 767px) {
  .chat-panes {
    flex-direction: column;  /* Stack vertically */
  }
  .chat-pane {
    width: 100%;
    height: auto;
    min-height: 300px;
  }
}

/* Input fields: larger on mobile (touch targets) */
@media (max-width: 767px) {
  input, textarea, button {
    min-height: 44px;  /* Standard iOS touch target */
    min-width: 44px;
    padding: 12px;
    font-size: 16px;  /* Prevents zoom-on-focus on iOS */
  }
}

/* Sidebar/panels: drawer on mobile */
@media (max-width: 767px) {
  #sidebar {
    position: fixed;
    left: -100%;
    width: 80%;
    height: 100vh;
    transition: left 0.3s;
    z-index: 100;
  }
  #sidebar.open {
    left: 0;
  }
  /* Hamburger menu visible */
  #menu-toggle {
    display: block;
  }
}

/* Text: readable on mobile */
@media (max-width: 767px) {
  body {
    font-size: 14px;  /* Slightly larger than desktop 13px */
  }
}

/* Scrollbars: hide on mobile (takes space) */
@media (max-width: 767px) {
  .scroll-area::-webkit-scrollbar {
    display: none;
  }
}
```

### No separate mobile view needed. Why?

1. **Flexbox + Grid already responsive**: Just need media queries
2. **Single-file HTML advantage**: No build step to maintain two versions
3. **CSS changes are ~50 lines** (minimal)
4. **Lighter than separate view** (no duplicate code)
5. **Easier to maintain** (one HTML, one CSS)

### What NOT to do:
- ❌ Create `index-mobile.html` (duplicate code, maintenance nightmare)
- ❌ Use separate mobile framework (Svelte, React, etc.) — adds weight, build step
- ❌ Responsive images with srcset (no images in BeigeBox)

### What to do:
- ✅ Add ~50 lines of CSS media queries
- ✅ Add hamburger menu toggle (10 lines of JS)
- ✅ Test on real phone (or Chrome device emulator)
- ✅ Ensure touch targets are 44px minimum

### Implementation Steps (Lightest Path)

1. **Add media queries to CSS** (~50 lines)
   - Tablets: slightly adjusted grid/font
   - Mobile: single column, stacked panes, scrollable tabs

2. **Add hamburger menu** (~15 lines HTML + 10 lines CSS + 10 lines JS)
   ```html
   <!-- In header -->
   <button id="menu-toggle" style="display:none">☰</button>
   
   <style>
   @media (max-width: 767px) {
     #menu-toggle { display: block; }
   }
   </style>
   
   <script>
   document.getElementById('menu-toggle').onclick = () => {
     document.getElementById('sidebar').classList.toggle('open');
   };
   </script>
   ```

3. **Test on device emulator** (Chrome DevTools)
   - F12 → Device toolbar → iPhone 12

4. **Done.** Total change: <100 lines added.

---

## Recommendations Summary

### Priority 1: Improve Extensibility (Medium Effort, High Value)

1. **Tool Registry Auto-Discovery** (1-2 days)
   - Allow tools to be registered via config class path instead of code
   - Keep manual hardcoding as fallback for built-ins
   - Reduces friction for custom tools

2. **Add FastAPI Middleware Support** (1 day, optional)
   - Keep hooks as-is (low-friction)
   - Add middleware option for power users
   - Document difference

### Priority 2: Add Mobile CSS (Very Low Effort, High Value)

1. **Media queries for mobile layout** (1-2 hours)
   - Single column cards
   - Scrollable tabs
   - Stacked panes

2. **Hamburger menu** (30 min)
   - JS toggle for navigation on mobile

3. **Test on device** (30 min)
   - Chrome DevTools emulator

### Priority 3: Improve Code Organization (Medium Effort)

1. **Split main.py into endpoint modules** (2-3 days)
   - Use FastAPI APIRouter
   - One file per domain (config, stats, operator, etc.)
   - Main.py orchestrates

2. **Add Pydantic models for requests/responses** (2-3 days)
   - Better validation, type safety, OpenAPI docs

### Priority 4: Documentation

1. **Extension guide**
   - How to add a backend (existing: good)
   - How to add a hook (existing: good)
   - How to add a tool (to improve)
   - How to add an endpoint (to document)
   - How to add middleware (if implemented)

---

## Standards Adherence Checklist

| Principle | Status | Evidence |
|-----------|--------|----------|
| **Single Responsibility** | ⚠️ | main.py does too much; backends are clean |
| **DRY (Don't Repeat)** | ✅ | Good backend/hook patterns, but tools repeated logic |
| **Open/Closed** | ⚠️ | Open to backends/hooks, closed to tools/CLI |
| **Dependency Inversion** | ⚠️ | ABC backends good, but globals in main.py |
| **Composition > Inheritance** | ✅ | Tools, hooks, backends all composition-friendly |
| **Configuration over Code** | ✅ | Excellent config-driven approach |
| **Fail Safe** | ✅ | Broken hooks are skipped, defaults everywhere |
| **Testability** | ⚠️ | Globals make testing harder |
| **Portability** | ✅ | OpenAI format, JSONL logs, SQLite |
| **Backward Compatibility** | ✅ | Versions, migration, deprecation warnings |

---

## Conclusion

**BeigeBox is well-designed for genericity and extensibility** at the macro level (backends, hooks, OpenAI compatibility). The gaps are:

1. **Tools need plugin system** (like hooks)
2. **Main.py needs modularization** (FastAPI routers)
3. **Mobile CSS is missing** (media queries only)
4. **Code organization could use FastAPI standards** (Depends, Pydantic)

None of these are blocking. The architecture is sound. Incremental improvements follow standard patterns. The single-file HTML is a strength, not a weakness — adding mobile support needs ~100 lines of CSS, no more.

