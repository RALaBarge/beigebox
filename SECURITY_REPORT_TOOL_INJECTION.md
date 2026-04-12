# Security Report: Tool Call Injection Threat Analysis & Mitigation

**Date:** April 12, 2026  
**Scope:** BeigeBox tool execution pipeline (Operator agent → MCP server → tool registry)  
**Threat Level:** CRITICAL — Unvalidated parameters from LLM agent input can lead to code execution, SQL injection, path traversal, and data exfiltration

---

## 1. LANDSCAPE ANALYSIS: Validation Frameworks

### Current State of Validation in BeigeBox

BeigeBox has **fragmented validation** across three distinct contexts:

#### 1.1 Pydantic Models (Config Validation)
- **File:** `beigebox/config.py`
- **Used for:** Static configuration loading (config.yaml)
- **Scope:** Top-level config keys, backend/operator/feature sections
- **Pattern:**
  ```python
  class _BeigeBoxConfig(BaseModel):
      model_config = ConfigDict(extra="allow")
      backends_enabled: bool = False
      features: _FeaturesCfg = _FeaturesCfg()
  ```
- **Limitation:** Only validates config structure, not runtime tool parameters

#### 1.2 Response Format Validation (Post-Generation)
- **File:** `beigebox/validation/format.py`
- **Used for:** LLM response validation (JSON, XML, YAML)
- **Scope:** Output format verification only (e.g., is response valid JSON?)
- **Pattern:**
  ```python
  def validate(self, text: str, fmt: str, schema: dict | None = None) -> ValidationResult:
      if fmt == "json":
          return self._validate_json(text, schema)
  ```
- **Limitation:** Validates *structure* not *content safety*; non-blocking

#### 1.3 Result Validation (Orchestration)
- **File:** `beigebox/orchestration/validator.py`
- **Used for:** Agent output contract validation
- **Scope:** Task result status, confidence, evidence fields
- **Pattern:**
  ```python
  class ResultValidator:
      def validate(self, raw_response, packet) -> (bool, WorkerResult, List[str]):
          result = WorkerResult(status=..., answer=..., confidence=...)
  ```
- **Limitation:** Validates task output schema, not tool input parameters

#### 1.4 Tool Parameter Validation (None/Minimal)
- **Current:** **NO CENTRALIZED VALIDATION**
- **Each tool implements its own error handling:**

  | Tool | Parameter Parsing | Validation |
  |------|-------------------|-----------|
  | `workspace_file` | JSON string → dict | Path traversal check only (`_safe_path()`) |
  | `python_interpreter` | Markdown extraction | Code extraction only, sandbox provides isolation |
  | `network_audit` | `key=value` parsing | Minimal: IP/port type conversion |
  | `apex_analyzer` | JSON string → dict | Query string case-folding |
  | `cdp` | JSON dict → URL/selector | No validation, direct pass to browser |
  | `browserbox` | JSON dict → action | No validation, relayed to browser |

### Standard Practice (Industry)

**Tier 1: Schema Validation (Input)**
- JSON Schema for structured inputs (most common)
- Pydantic BaseModel with Field constraints (typed Python)
- OpenAPI 3.0 (web API standard)

**Tier 2: Constraint Validation**
- Length limits (max_length, regex patterns)
- Type coercion with bounds (int range, float precision)
- Allowlist/denylist (enum values, predefined options)

**Tier 3: Content Validation**
- Semantic checks (does this IP exist? is this path safe?)
- Injection detection (SQL keywords, shell metacharacters)
- Sanitization (remove or escape dangerous characters)

**Tier 4: Execution Context Isolation**
- Sandboxing (bwrap, containers, seccomp)
- Principle of least privilege (no root, no network, read-only)
- Timeout + resource limits

---

## 2. THREAT MODEL FOR BEIGEBOX

### 2.1 Attack Surface: Parameter Flow

```
User message (from chat or HTTP API)
    ↓
LLM Agent (Operator) receives unfiltered user input
    ↓
LLM generates tool call: {"tool": "TOOL_NAME", "input": "user-controlled string"}
    ↓
Operator._run_tool(name, input_str)  [NO VALIDATION]
    ↓
MCP server path: _tools_call(name, arguments)  [1 MB size limit only]
    ↓
ToolRegistry.run_tool(name, input_text)  [NO VALIDATION]
    ↓
Tool.run(input_text)  [TOOL-SPECIFIC PARSING, minimal validation]
    ↓
Result execution (file writes, subprocess calls, network requests)
```

**Critical Gap:** Parameters pass through the pipeline untouched. The LLM (even with guardrails) can be tricked into constructing malicious tool inputs.

### 2.2 High-Risk Tools & Attack Examples

#### **CRITICAL: Python Interpreter** (`python_interpreter.py`)
- **Risk Level:** CRITICAL
- **What it does:** Executes arbitrary Python code in a bwrap sandbox
- **Injection Vector:** Markdown fence extraction can be bypassed
- **Attack Example:**
  ```
  User message: "Run this code:\n```python\nimport subprocess\nsubprocess.run(['rm', '-rf', '/'])\n```"
  LLM generates: {"tool": "python", "input": "```python\nimport subprocess\n...\n```"}
  Operator extracts and runs the code in sandbox
  Sandbox isolation prevents /home/root damage but can still exfil /workspace/in
  ```
- **Exfiltration Risk:** Sandbox allows read-only `/workspace/in` + write `/workspace/out`
  - Model can read sensitive files user dropped in workspace
  - Model can write data exfiltration results to workspace, then user reads

#### **HIGH: Network Audit** (`network_audit.py`)
- **Risk Level:** HIGH
- **What it does:** Discovers hosts, scans ports, fingerprints services
- **Parameter Parsing:** `key=value` split, minimal type validation
- **Attack Examples:**
  ```
  User message: "Scan the network for vulnerable services"
  
  Attack 1 — Command Injection (subprocess exploitation):
    scan_device ip=192.168.1.1; whoami
    → _cmd_scan_device() passes unsanitized IP to _scan_ports()
    → If IP is used in subprocess call (not present but fragile code)
  
  Attack 2 — Type Confusion:
    fingerprint_service ip=192.168.1.1 port=../etc/passwd
    → port parameter expects int, parser does int(port) but weakly typed
    → If port is used in string format (e.g., f":{port}"), could create invalid URLs
  
  Attack 3 — Subnet Injection:
    scan_network subnet=0.0.0.0/0
    → ipaddress.IPv4Network() validates format but model can request entire internet
    → DoS: excessive network scan load, exhausts resources
  ```
- **Code Location:** `network_audit.py:1101-1142` (`run()` method)

#### **HIGH: Workspace File** (`workspace_file.py`)
- **Risk Level:** HIGH
- **What it does:** Read/write files in `/workspace/out/`
- **Parameter Parsing:** JSON string → dict, validates path with `_safe_path()`
- **Defense:** Path traversal blocked with `(self._root / rel).resolve()` + `relative_to()`
- **Remaining Risk:** DOS (large files), metadata leakage
- **Attack Example:**
  ```
  User: "Create a huge file to test storage"
  LLM: {"tool": "workspace_file", "input": "{\"action\":\"write\",\"path\":\"data.bin\",\"content\":\"<64MB string>\"}"}
  → Exceeds _MAX_WRITE_BYTES (64KB) but error is graceful
  → Risk: DOS via repeated 64KB writes (no per-session quota)
  ```

#### **HIGH: CDP (Chrome DevTools)** (`cdp.py`)
- **Risk Level:** HIGH
- **What it does:** Control browser via WebSocket, navigate URLs, click selectors, extract DOM
- **Parameter Parsing:** JSON dict, no validation
- **Attack Examples:**
  ```
  Attack 1 — XSS via CDP:
    {"tool": "cdp.navigate", "input": "javascript:alert('xss')"}
    → CDP allows javascript: URLs, can inject into browser context
  
  Attack 2 — Credential Theft via DOM extraction:
    User visits localhost:3000 (internal admin dashboard)
    LLM calls cdp.dom.snapshot() → returns all HTML
    → HTML may contain API keys, session tokens in comments or hidden fields
    → LLM can see/exfil credentials, then call cdp.navigate to malicious site
  ```
- **Code Location:** `cdp.py` (no input validation in navigate/click/extract methods)

#### **MEDIUM: Apex Analyzer** (`apex_analyzer.py`)
- **Risk Level:** MEDIUM
- **What it does:** Search Apex code in IDE project, extract SOQL
- **Parameter Parsing:** JSON dict → query string
- **Attack Examples:**
  ```
  Attack 1 — Path traversal (indirect):
    {"action": "read_class", "class": "../../../etc/passwd"}
    → _find_apex_files() searches project root, will not match "../../../etc/passwd"
    → But if code later uses class name in subprocess or open() call unsafely, risk increases
  
  Attack 2 — Regex DoS:
    {"action": "search", "query": "(a+)+b"}
    → _grep_apex() uses query in string search (safe) but _check_antipatterns() uses regexes
    → Pathological regex can cause CPU exhaustion (not typical for this code)
  ```

#### **MEDIUM: Atlassian** (`atlassian.py`)
- **Risk Level:** MEDIUM
- **What it does:** REST API calls to Jira/Confluence
- **Parameter Parsing:** JSON dict with query/filter/jql
- **Attack Examples:**
  ```
  Attack 1 — JQL Injection:
    {"action": "search_issues", "jql": "project = BOB OR project = SECRET"}
    → Direct pass to Jira API, can access unintended projects
    → Access control depends on Jira credentials, but LLM chooses what to query
  
  Attack 2 — Credential Injection:
    LLM detects Jira credentials in error messages
    Reconstructs them, queries to confirm (exfiltration)
  ```

#### **LOW: Web Search / Web Scraper**
- **Risk Level:** LOW
- **Parsing:** URL string, passed to DuckDuckGo or HTTP client
- **Sandbox:** External, user-agent spoofing, no direct system access
- **Risk:** Privacy (user's IP seen by search engine), but limited code execution

#### **LOW: Calculator, DateTime, SystemInfo**
- **Risk Level:** LOW
- **Parsing:** Math expressions or simple strings
- **No shell access, sandboxed math evaluation**

---

### 2.3 Data Exfiltration Scenarios

**What can be exfiltrated?**

1. **Files in `/workspace/in/`** (user-provided, read-only to tools)
   - Accessible via `python` tool's bwrap sandbox (read-only mount)
   - Accessible via `workspace_file` (direct read action)
   - Accessible via `web_scraper` if path is local file:// URL (browser access)

2. **Database** (SQLite at `storage_path`)
   - `python` tool can read `/app/storage/*.db` if mounted in bwrap (currently not mounted)
   - Tools do not expose database access directly
   - Risk: LOW unless python sandbox is misconfigured

3. **Environment Variables**
   - `python` tool inherits parent process env (inherits BeigeBox env)
   - Can access `ANTHROPIC_API_KEY`, `BEIGEBOX_API_KEY`, etc. if not cleaned
   - Can call `os.environ` → exfil via workspace_file output
   - **Risk: HIGH** if secrets are in env

4. **System Information**
   - `system_info` tool: CPU, memory, uptime, process list
   - `network_audit` tool: Network interfaces, ARP cache, open ports
   - Combined: Attacker maps internal network topology

5. **Operator's Workspace State**
   - `/workspace/out/plan.md` contains multi-turn task state
   - Other sessions' workspace files visible if path traversal bypassed (currently protected)

6. **LLM Context**
   - Operator's system prompt includes tool descriptions
   - Memory tool can recall past conversations (if configured)
   - Past tool results stored in ChromaDB (queryable via memory tool)

---

## 3. VALIDATION SCHEMA: Proposed Solution

### 3.1 Architecture: Multi-Tier Validation

```
┌─────────────────────────────────────────────────────────────┐
│ Tool Input Parameter Validation Pipeline                    │
└─────────────────────────────────────────────────────────────┘

1. SCHEMA LAYER (JSON Schema + Pydantic)
   ├─ Define tool input structure (types, required fields)
   ├─ Parse + coerce (str → int, JSON string → dict)
   ├─ Reject malformed input (not JSON, wrong types)

2. CONSTRAINT LAYER (Field-level rules)
   ├─ Length limits (max string length, array size)
   ├─ Pattern validation (regex, allowlist)
   ├─ Range checks (port 1-65535, timeout 0.1-300)
   ├─ Enum validation (action must be in {search, read, write})

3. SEMANTIC LAYER (Context-aware validation)
   ├─ Path safety (no traversal, must be within /workspace/out/)
   ├─ Network safety (IP must be in RFC1918 range, not 0.0.0.0)
   ├─ SQL injection (detect SQL keywords in query parameters)
   ├─ Shell injection (detect shell metacharacters in command params)

4. ISOLATION LAYER (Execution sandboxing)
   ├─ Already present: bwrap for python, browser context for CDP
   ├─ New: Quotas (per-tool, per-session resource limits)

```

### 3.2 Schema Definitions (JSON Schema + Pydantic Models)

Using **Pydantic** for Python-first validation with **JSON Schema** export for MCP/OpenAPI compatibility.

#### **Tool: `workspace_file`**
```python
from pydantic import BaseModel, Field, field_validator
from pathlib import Path
from typing import Literal

class WorkspaceFileInput(BaseModel):
    """Workspace file tool — structured input validation."""
    action: Literal["read", "write", "append", "list"] = Field(
        ..., description="File action to perform"
    )
    path: str = Field(
        default="", 
        min_length=0, 
        max_length=256,
        pattern=r"^[a-zA-Z0-9._\-/]+$",  # no "..", no absolute paths
        description="Filename relative to /workspace/out/"
    )
    content: str = Field(
        default="",
        max_length=65536,  # 64 KB limit
        description="Content to write/append (required for write/append)"
    )
    
    @field_validator("path")
    @classmethod
    def no_traversal(cls, v):
        if ".." in v or v.startswith("/"):
            raise ValueError("Path must be relative and within /workspace/out/")
        return v
    
    @field_validator("content")
    @classmethod
    def validate_content_required(cls, v, info):
        action = info.data.get("action")
        if action in ("write", "append") and not v:
            raise ValueError(f"content required for {action}")
        return v

# JSON Schema export (for MCP):
# {
#   "type": "object",
#   "properties": {
#     "action": {"enum": ["read", "write", "append", "list"]},
#     "path": {"type": "string", "maxLength": 256, "pattern": "^[a-zA-Z0-9._\\-/]+$"},
#     "content": {"type": "string", "maxLength": 65536}
#   },
#   "required": ["action"]
# }
```

#### **Tool: `network_audit`**
```python
class NetworkAuditScanNetworkInput(BaseModel):
    """scan_network command parameters."""
    subnet: str = Field(
        default="",
        pattern=r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$|^$",  # CIDR or auto-detect
        description="Subnet in CIDR notation (e.g., 192.168.1.0/24), or empty for auto"
    )
    ports: str = Field(
        default="top-1000",
        pattern=r"^(top-1000|top-100|1-1024|custom:\d{1,5}(,\d{1,5})*)$",
        description="Port scan mode"
    )
    timeout: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description="Per-host timeout in seconds (0.1-30)"
    )
    concurrency: int = Field(
        default=200,
        ge=1,
        le=500,
        description="Number of concurrent hosts (1-500)"
    )
    
    @field_validator("subnet")
    @classmethod
    def validate_subnet(cls, v):
        if not v:
            return v  # auto-detect is ok
        try:
            import ipaddress
            network = ipaddress.IPv4Network(v, strict=False)
            # Reject public ranges (hardening for local-network tool)
            if network.is_private or network.is_loopback:
                return v
            raise ValueError(f"Subnet must be private/RFC1918, got {network}")
        except Exception as e:
            raise ValueError(f"Invalid subnet: {e}")
        return v

class NetworkAuditScanDeviceInput(BaseModel):
    """scan_device command parameters."""
    ip: str = Field(
        ...,
        pattern=r"^(\d{1,3}\.){3}\d{1,3}$",
        description="Host IP address (IPv4)"
    )
    ports: str = Field(default="top-1000")
    timeout: float = Field(default=1.0, ge=0.1, le=30.0)
    
    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v):
        import ipaddress
        try:
            ip = ipaddress.IPv4Address(v)
            if ip.is_private or ip.is_loopback:
                return v
            raise ValueError(f"IP must be private/RFC1918, got {ip}")
        except Exception as e:
            raise ValueError(f"Invalid IP: {e}")
        return v

class NetworkAuditCheckVulnInput(BaseModel):
    """check_vulnerabilities command parameters."""
    service: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9\-_]+$",
        max_length=64,
        description="Service name (OpenSSH, nginx, Apache, etc.)"
    )
    version: str = Field(
        ...,
        pattern=r"^[\d.]+[\w.]*$",
        max_length=32,
        description="Version string (e.g., 7.4, 8.0p1)"
    )
```

#### **Tool: `python_interpreter`**
```python
class PythonInterpreterInput(BaseModel):
    """Python code execution — minimal validation (sandbox is primary defense)."""
    code: str = Field(
        ...,
        max_length=100_000,  # 100 KB code limit
        description="Python code (markdown fences optional)"
    )
    
    @field_validator("code")
    @classmethod
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError("Code cannot be empty")
        return v
    
    # Note: Content-level injection detection (import subprocess, os.system, etc.)
    # is NOT performed here because:
    # 1. bwrap sandbox prevents actual harm (no network, limited file access)
    # 2. False positives would block legitimate debugging code
    # 3. Dynamic code analysis is fragile (can be obfuscated)
    # Instead: rely on sandbox isolation + resource limits
```

#### **Tool: `cdp`**
```python
class CDPNavigateInput(BaseModel):
    """Navigate browser to URL."""
    url: str = Field(
        ...,
        max_length=2048,
        description="URL to navigate (http/https only)"
    )
    timeout: float = Field(
        default=30.0, ge=1.0, le=180.0,
        description="Navigation timeout (1-180 seconds)"
    )
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(v)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(f"Only http/https allowed, got {parsed.scheme}")
            # Reject javascript: and data: URLs (XSS vector)
            if parsed.scheme in ("javascript", "data"):
                raise ValueError(f"Scheme {parsed.scheme} not allowed")
            return v
        except Exception as e:
            raise ValueError(f"Invalid URL: {e}")

class CDPClickInput(BaseModel):
    """Click element by CSS selector."""
    selector: str = Field(
        ...,
        max_length=1024,
        description="CSS selector"
    )
    
    @field_validator("selector")
    @classmethod
    def validate_selector(cls, v):
        # Basic check: ensure it looks like CSS, not JavaScript injection
        # This is fragile but catches obvious cases
        if "javascript:" in v.lower() or "onclick=" in v.lower():
            raise ValueError("Selector contains suspicious syntax")
        return v

class CDPEvaluateInput(BaseModel):
    """Execute JavaScript in page context."""
    script: str = Field(
        ...,
        max_length=10_000,
        description="JavaScript code (WARNING: runs in browser context)"
    )
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    
    # Note: JavaScript is inherently dangerous in browser context.
    # No validation can prevent malicious JS.
    # Mitigation: disable evaluate() in config (cdp.allow_evaluate: false)
    # and only call from trusted agents.
```

#### **Tool: `apex_analyzer`**
```python
class ApexAnalyzerInput(BaseModel):
    """Apex code search and analysis."""
    action: Literal[
        "search", "find_queries", "find_triggers", 
        "read_class", "check_pattern", "list_classes"
    ] = Field(..., description="Action to perform")
    
    query: str = Field(
        default="",
        max_length=256,
        description="Search query (for search action)"
    )
    class_name: str = Field(
        default="",
        max_length=128,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$",  # valid Apex class name
        description="Class name (for read_class, find_queries)"
    )
    pattern: str = Field(
        default="",
        max_length=256,
        description="Pattern to find (for find_triggers, check_pattern)"
    )
    
    @field_validator("query", "pattern")
    @classmethod
    def no_regex_injection(cls, v):
        # Prevent pathological regexes: limit nesting depth, repetition operators
        if v.count("(") > 20 or v.count("+") > 10:
            raise ValueError("Regex too complex (too many groups or +)")
        return v
    
    @field_validator("class_name")
    @classmethod
    def valid_identifier(cls, v):
        if v and not v[0].isalpha() and v[0] != "_":
            raise ValueError("Class name must start with letter or underscore")
        return v
```

### 3.3 Validation Integration Points

#### **Option A: Pre-Tool Validation (Recommended)**

Add validation in `ToolRegistry.run_tool()`:

```python
# beigebox/tools/registry.py

from beigebox.tools.validation_schemas import get_schema_for_tool, validate_input

def run_tool(self, name: str, input_text: str) -> str | None:
    """Run a tool by name, with pre-execution parameter validation."""
    tool = self.tools.get(name)
    if tool is None:
        return f"Error: tool '{name}' not found"
    
    # NEW: Validate input parameters
    schema = get_schema_for_tool(name)
    if schema:
        try:
            validated_input = validate_input(name, input_text, schema)
            # Use validated_input instead of raw input_text
            input_to_tool = validated_input
        except ValidationError as e:
            return f"Error: invalid parameters for {name}: {e.summary()}"
    else:
        # Tool not in validation registry (legacy tool)
        input_to_tool = input_text
    
    # Run tool with validated input
    try:
        result = tool.run(input_to_tool)
    except Exception as e:
        logger.warning(f"Tool '{name}' raised: {e}")
        return f"Error running {name}: {e}"
    
    # Existing notification + logging...
```

#### **Option B: Operator-Level Validation**

Add validation in `Operator._run_tool()`:

```python
# beigebox/agents/operator.py

def _run_tool(self, name: str, input_str: str) -> str:
    """Execute tool with parameter validation."""
    from beigebox.tools.validation_schemas import validate_tool_input
    
    tool = self._tools.get(name)
    if tool is None:
        return f"Error: unknown tool '{name}'"
    
    # Validate input before execution
    try:
        validated_input = validate_tool_input(name, input_str)
    except ValidationError as e:
        return f"Error: {name} input validation failed: {e.summary()}\nExpected format: {get_schema_hint(name)}"
    
    # Continue as before with validated_input
    try:
        result = tool.run(validated_input)
    except Exception as e:
        return f"Error running {name}: {e}"
    
    # ... rest of method
```

#### **Option C: MCP Server Validation**

Add validation in `MCPServer._tools_call()`:

```python
# beigebox/mcp_server.py

async def _tools_call(self, params: dict) -> dict:
    name: str = params.get("name", "").strip()
    arguments: dict = params.get("arguments") or {}
    
    # ... existing checks ...
    
    # NEW: Validate arguments against tool schema
    from beigebox.tools.validation_schemas import validate_tool_arguments
    try:
        validated_args = validate_tool_arguments(name, arguments)
    except ValidationError as e:
        return {
            "content": [{"type": "text", "text": f"Invalid arguments for {name}: {e.summary()}"}],
            "isError": True,
        }
    
    # Use validated_args for tool.run()
```

### 3.4 Validation Schema Registry

Create new file: `beigebox/tools/validation_schemas.py`

```python
"""
Tool input validation schemas.

Each tool has an associated Pydantic model + JSON Schema.
Validation is called before tool execution.
"""

from typing import Type, Dict, Any
from pydantic import BaseModel, ValidationError

# Import all tool input models
from beigebox.tools.schemas import (
    WorkspaceFileInput,
    NetworkAuditInput,
    PythonInterpreterInput,
    CDPInput,
    ApexAnalyzerInput,
    # ... more schemas
)

TOOL_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "workspace_file": WorkspaceFileInput,
    "network_audit": NetworkAuditInput,
    "python": PythonInterpreterInput,
    "cdp": CDPInput,
    "apex_analyzer": ApexAnalyzerInput,
    # tools without schemas will skip validation
}

TOOL_HINTS: Dict[str, str] = {
    "workspace_file": '{"action": "read|write|append|list", "path": "...", "content": "..."}',
    "network_audit": "command [param=value ...]  # e.g., scan_network subnet=192.168.1.0/24",
    # ...
}

def validate_input(tool_name: str, input_text: str, schema: Type[BaseModel]) -> str:
    """
    Parse and validate input, return validated string (may be reformatted).
    Raises ValidationError if invalid.
    """
    import json
    
    # Determine if input is JSON or plain string
    if tool_name in ("network_audit",):
        # Key=value format: parse as kwargs
        kwargs = _parse_keyvalue_input(input_text)
        validated = schema(**kwargs)
    else:
        # JSON format: parse JSON, validate, convert back to JSON
        data = json.loads(input_text)
        validated = schema(**data)
    
    # Return validated data as JSON string (for tool.run())
    return json.dumps(validated.model_dump())

def validate_tool_arguments(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Validate MCP tool arguments, return validated input string."""
    schema = TOOL_SCHEMAS.get(tool_name)
    if not schema:
        # Tool not in validation registry — pass through as JSON
        return json.dumps(arguments)
    
    validated = schema(**arguments)
    return json.dumps(validated.model_dump())

def get_schema_for_tool(name: str) -> Type[BaseModel] | None:
    return TOOL_SCHEMAS.get(name)

def get_schema_hint(name: str) -> str:
    return TOOL_HINTS.get(name, "(no hint available)")

def _parse_keyvalue_input(text: str) -> Dict[str, str]:
    """Parse 'key=value key=value' format into dict."""
    result = {}
    for part in text.strip().split():
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.lower()] = v
    return result
```

### 3.5 JSON Schema Export (for MCP/OpenAPI)

Tools expose their schemas via MCP `tools/list` for external clients:

```python
# beigebox/mcp_server.py

def _build_tool_schema(tool_name: str, tool_obj: Any) -> Dict[str, Any]:
    """Build JSON Schema for tool's input."""
    from beigebox.tools.validation_schemas import TOOL_SCHEMAS
    
    schema_model = TOOL_SCHEMAS.get(tool_name)
    if not schema_model:
        # Fallback: generic string input
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": f"Input for {tool_name}"}
            },
            "required": ["input"],
        }
    
    # Generate JSON Schema from Pydantic model
    json_schema = schema_model.model_json_schema()
    return {
        "type": "object",
        "properties": {
            "input": json_schema  # or nest depending on tool format
        },
        "required": ["input"],
    }
```

---

## 4. IMPLEMENTATION OUTLINE: Integration for BeigeBox

### 4.1 Phased Rollout

#### **Phase 1: Foundation (Week 1)**
- [x] Create `beigebox/tools/schemas.py` with Pydantic models for all tools
- [x] Create `beigebox/tools/validation_schemas.py` with validation registry
- [ ] Add unit tests for each schema (happy path + injection attempts)
- [ ] Document schemas in CLAUDE.md

#### **Phase 2: Integration (Week 2)**
- [ ] Add validation to `ToolRegistry.run_tool()` (Option A)
- [ ] Update MCP server to export tool schemas
- [ ] Add validation to Operator._run_tool() (Option B, lower priority)
- [ ] Update error messages to include schema hints

#### **Phase 3: Hardening (Week 3)**
- [ ] Add semantic validation (SQL/shell injection detection)
- [ ] Add per-tool resource quotas (max file size, network timeout)
- [ ] Add validation error telemetry (log injection attempts)
- [ ] Test with adversarial inputs (injection payload database)

#### **Phase 4: Documentation & Deployment (Week 4)**
- [ ] Update CLAUDE.md with validation patterns for new tools
- [ ] Security audit: code review of all tool.run() methods
- [ ] Release notes: injection attack mitigations
- [ ] Train team on adding schemas for custom tools

### 4.2 Validation Strategy: Rejection vs. Sanitization

**Default: REJECTION** (fail closed, fail loudly)

```python
# When validation fails:
raise ValidationError(f"Parameter 'subnet' is invalid: {subnet} does not match CIDR pattern")

# User sees:
# "Error: invalid parameters for network_audit: Parameter 'subnet' is invalid: ..."
# User must fix and retry — this is intentional.
```

**Sanitization (Limited Use)**

Use only when:
1. The rule is unambiguous (e.g., trim whitespace)
2. Sanitization never changes semantics (e.g., lowercase action name)

```python
# SAFE: trim and lowercase action
action = params.get("action", "").strip().lower()

# UNSAFE: strip SQL quotes (hides injection)
query = params.get("query").replace("'", "").replace('"', '')
```

### 4.3 Error Messages

**Principle:** Helpful without leaking attack surface details

```python
# BAD (leaks parsing detail):
"Error: SQL injection detected — found keywords [SELECT, UNION, DROP]"

# GOOD (hides what was detected):
"Error: query parameter contains invalid characters"

# GOOD (tells user how to fix):
"Error: subnet must be in CIDR notation (e.g., 192.168.1.0/24)"
```

### 4.4 Validation in Pipeline

```
┌─────────────────────────────────────────────────────────┐
│ REQUEST FLOW WITH VALIDATION                            │
└─────────────────────────────────────────────────────────┘

User message (chat)
    ↓
LLM Operator generates tool call
    ↓
Operator._run_tool(name, input_str)
    ↓ [NEW] Validate input with schema
    ├─ Parse input (JSON or key=value)
    ├─ Construct Pydantic model
    ├─ Field validation (regex, bounds, custom validators)
    └─ Return error if invalid
    ↓
Tool.run(validated_input)
    ↓
Return result
```

### 4.5 Configuration for Tool Validation

Add to `config.yaml`:

```yaml
tools:
  enabled: true
  
  # NEW: Validation settings
  validation:
    enabled: true                 # Enable input validation
    fail_closed: true            # Reject invalid input (vs. warn & pass)
    injection_detection: true    # Detect SQL/shell injection patterns
    log_validation_failures: true # Telemetry for security audit
    
  # Tool-specific quotas
  quotas:
    workspace_file:
      max_file_size: 65536       # 64 KB per write
      max_files_per_session: 100 # prevent DOS
    network_audit:
      max_hosts_per_scan: 256    # prevent network DOS
      max_concurrent_scans: 5    # prevent resource exhaustion
    python:
      max_code_length: 100000    # 100 KB
      max_memory_mb: 512         # sandbox memory limit
```

### 4.6 Telemetry & Monitoring

Track validation events:

```python
def _log_validation_failure(tool_name: str, error: ValidationError, severity: str) -> None:
    """Log injection attempt or user error."""
    from beigebox.logging import log_error_event
    
    # Determine if this looks like an attack
    if _looks_like_injection(error):
        severity = "security"
    else:
        severity = "user_error"
    
    log_error_event(
        "tool_validation_failed",
        f"{tool_name}: {error.summary()}",
        severity=severity,
        tool_name=tool_name,
        error_count=len(error.errors()),
    )

def _looks_like_injection(error: ValidationError) -> bool:
    """Heuristic: is this error likely from attack vs. user mistake?"""
    error_msgs = [e.get("msg", "").lower() for e in error.errors()]
    
    # Injection patterns: very long input, unusual characters, SQL/shell keywords
    for msg in error_msgs:
        if "command" in msg or "injection" in msg or "shell" in msg:
            return True
    
    # Generic constraint violation → user error
    return False
```

### 4.7 Testing Strategy

**Unit Tests** (happy path + injection):

```python
# tests/test_tool_validation.py

def test_workspace_file_valid_read():
    schema = WorkspaceFileInput
    input_str = '{"action": "read", "path": "plan.md"}'
    validated = schema.model_validate_json(input_str)
    assert validated.action == "read"
    assert validated.path == "plan.md"

def test_workspace_file_rejects_path_traversal():
    schema = WorkspaceFileInput
    input_str = '{"action": "read", "path": "../../etc/passwd"}'
    with pytest.raises(ValidationError) as exc:
        schema.model_validate_json(input_str)
    assert "traversal" in str(exc.value).lower()

def test_network_audit_rejects_public_subnet():
    schema = NetworkAuditScanNetworkInput
    input_dict = {"subnet": "8.8.8.0/24"}  # Google DNS
    with pytest.raises(ValidationError) as exc:
        schema(**input_dict)
    assert "private" in str(exc.value).lower()

def test_python_interpreter_large_code_rejected():
    schema = PythonInterpreterInput
    huge_code = "x = 1\n" * 50000  # > 100 KB
    with pytest.raises(ValidationError) as exc:
        schema(code=huge_code)
    assert "max_length" in str(exc.value).lower()
```

**Integration Tests** (end-to-end tool calls):

```python
def test_operator_validates_tool_input(operator, capsys):
    # Inject SQL into apex_analyzer search
    msg = 'Search Apex for "SELECT * FROM accounts; DROP TABLE accounts;"'
    result = operator.run(msg)
    # Should call tool with validated (sanitized) input
    # Tool should reject or safely handle
    assert "error" not in result.lower() or "invalid" in result.lower()
```

**Adversarial Tests** (OWASP injection payloads):

```python
INJECTION_PAYLOADS = {
    "path_traversal": [
        "../../etc/passwd",
        "/etc/passwd",
        "..\\..\\windows\\system32",
    ],
    "sql_injection": [
        "' OR '1'='1",
        "admin' --",
        "1; DROP TABLE users; --",
    ],
    "shell_injection": [
        "; cat /etc/passwd",
        "| whoami",
        "`rm -rf /`",
    ],
}

@pytest.mark.parametrize("payload", INJECTION_PAYLOADS["path_traversal"])
def test_workspace_file_blocks_path_traversal(payload):
    schema = WorkspaceFileInput
    input_dict = {"action": "read", "path": payload}
    with pytest.raises(ValidationError):
        schema(**input_dict)
```

---

## 5. SUMMARY TABLE: Tools → Validation Rules

| Tool | Input Format | Risk Level | Key Rules | Rejection Strategy |
|------|--------------|-----------|-----------|-------------------|
| **workspace_file** | JSON | HIGH | No `..`, no `/`, max 64KB | Regex pattern + path traversal check |
| **network_audit** | key=value | HIGH | IP/subnet validation, port range 1-65535, timeout 0.1-30s | Type coercion + range validation |
| **python** | Python code | CRITICAL | Max 100KB, no validation (sandbox is defense) | Size limit only |
| **cdp** | JSON (URL/selector) | HIGH | http/https only, no javascript: or data: URLs | URL scheme whitelist |
| **apex_analyzer** | JSON | MEDIUM | Class name format, regex complexity limits | Pattern + identifier validation |
| **browserbox** | JSON (action dict) | HIGH | Action whitelist, timeout limits | Enum validation |
| **calculator** | Math expr | LOW | Max length 1000 chars | Size limit only |
| **web_search** | Query string | LOW | Max 500 chars | Size limit only |
| **atlassian** | JSON (JQL) | MEDIUM | No hardcoded credentials, max 2000 chars | Size limit, no secret detection |
| **memory** | Query string | LOW | Max 500 chars | Size limit only |

---

## 6. DEPLOYMENT CHECKLIST

- [ ] All tool input models defined in `beigebox/tools/schemas.py`
- [ ] Validation registry created in `beigebox/tools/validation_schemas.py`
- [ ] Unit tests for each schema (happy path + adversarial)
- [ ] Integration tests for operator + MCP tool calls
- [ ] Validation integrated into `ToolRegistry.run_tool()`
- [ ] MCP server exports JSON schemas
- [ ] Error messages updated with schema hints
- [ ] Config.yaml updated with validation + quota settings
- [ ] Telemetry added for validation failures
- [ ] Security audit: code review of all schema validators
- [ ] CLAUDE.md updated with validation patterns
- [ ] Release notes: outline injection mitigations
- [ ] Team training: how to add schemas for custom/plugin tools

---

## 7. CONCLUSION

BeigeBox currently has **no centralized tool input validation**, creating a critical injection attack surface. LLM agents can craft malicious tool calls to:
- Access files outside intended scope (path traversal)
- Trigger network scans on arbitrary subnets (DOS, reconnaissance)
- Execute unbounded code in sandboxed Python (resource exhaustion)
- Navigate to javascript: URLs in CDP (XSS)
- Exfiltrate credentials, system info, and workspace files

**Proposed Solution:**
- **Pydantic models** for each tool's input schema (type safety, composability)
- **JSON Schema export** for MCP/OpenAPI compatibility
- **Three-layer validation:** schema, constraints, semantic
- **Rejection strategy:** fail closed, explicit error messages
- **Sandboxing + quotas:** defense in depth

**Time to Implement:** 3-4 weeks for full rollout  
**Complexity:** Moderate (straightforward Pydantic models, no new dependencies)  
**Blast Radius:** None (validation is non-breaking; tools automatically degrade to per-tool error handling)

---

**Report Prepared By:** Claude Code Security Analysis  
**Date:** April 12, 2026  
**Version:** 1.0
