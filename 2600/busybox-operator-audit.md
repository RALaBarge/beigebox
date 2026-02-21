# BeigeBox Busybox & Operator Shell Access Review

**Date**: February 21, 2026  
**Status**: Quick Audit

---

## Current Implementation ✅

### Busybox Hardening (Docker)

**File**: `docker/Dockerfile` lines 12-33

Strong foundation:
- ✅ Busybox-static installed
- ✅ Wrapper script `/usr/local/bin/bb` created with blocklist
- ✅ Blocked applets: 40+ dangerous commands (rm, chmod, su, kill, reboot, etc.)
- ✅ Non-root user (appuser) runs the container
- ✅ Wrapper is immutable after build (chmod 755, chown root:root)

**Blocked applets** include:
```
rm, rmdir, mv, cp, chmod, chown, chroot, ln, mknod, sh, bash, su, sudo,
kill, killall, reboot, insmod, rmmod, nc, wget, httpd, awk, sed, xargs, find
```

---

## Operator Shell Access ⚠️

### Config File (config.yaml)

**File**: `config.yaml` lines ~1200+

**Current allowlist** (5 commands):
```yaml
operator:
  shell:
    enabled: true
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - ollama
      - beigebox
```

**Problem**: The allowlist exists in config but **is not enforced in code**.

---

## Critical Gaps Found 🚨

### Gap 1: Allowlist Not Enforced

**Location**: `beigebox/tools/system_info.py:36-45`

```python
def _run(cmd: str) -> str:
    """Run a shell command via the configured shell binary, return stdout or empty string."""
    try:
        shell = _get_shell()
        result = subprocess.run(
            [shell, "-c", cmd], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""
```

**Issue**: The `_run()` function accepts **any command** without checking against `config.yaml`'s `allowed_commands` list.

**Attack vector**:
```
Operator Agent asks: "Can you check if git is installed?"
LLM calls: system_info.run("which git")
  ↓
_run("which git")
  ↓
subprocess.run(["/bin/sh", "-c", "which git"], ...)
  ✅ Runs successfully (which is not blocked)
  
Then LLM escalates: "Delete all conversations"
LLM calls: system_info.run("rm -rf /app/data")
  ↓
_run("rm -rf /app/data")
  ↓
subprocess.run(["/bin/sh", "-c", "rm -rf /app/data"], ...)
  ❌ SUCCEEDS — appuser CAN delete /app/data (it owns it)
     Even though rm is in busybox blocklist, this uses /bin/rm (not busybox)
     OR busybox wrapper is never invoked for this call
```

### Gap 2: Operator Agent Can't Call Shell Tool Directly

**Location**: `beigebox/tools/registry.py`

**Available tools to operator**:
- web_search ✅
- web_scraper ✅
- google_search ✅
- calculator ✅
- datetime ✅
- system_info ✅ (reports stats, but calls `_run()` internally)
- memory ✅

**Missing**: There's NO explicit `shell_command` or `execute_command` tool in the registry that the operator agent can call via LangChain. The shell access is hidden inside `system_info.run()`.

**This is both good and bad**:
- ✅ Good: Operator can't explicitly ask to run arbitrary commands
- ❌ Bad: If `system_info.run()` gets abused, it's a silent backdoor
- ❌ Bad: No audit trail (no logging of which commands are run)

### Gap 3: Blocked Patterns Not Enforced

**Location**: `config.yaml` has this:

```yaml
blocked_patterns:
  - "rm -rf"
  - "sudo"
  - "> /etc"
```

**But**: `_run()` function doesn't check these patterns. They exist in config but are never read or validated.

### Gap 4: Shell Binary Not Validated

**Location**: `beigebox/tools/system_info.py:23-33`

```python
def _get_shell() -> str:
    """Return the configured shell binary, falling back to /bin/sh."""
    global _SHELL_BINARY
    if _SHELL_BINARY is None:
        try:
            from beigebox.config import get_config
            cfg = get_config()
            _SHELL_BINARY = cfg.get("operator", {}).get("shell_binary", "/bin/sh")
        except Exception:
            _SHELL_BINARY = "/bin/sh"
    return _SHELL_BINARY
```

**Issue**: Config allows specifying any shell binary. No validation that it exists or is safe.

**Attack**: Edit config to set `shell_binary: "/usr/bin/python"` and you've changed the shell.

### Gap 5: Busybox Wrapper Not Used

**Location**: `docker/Dockerfile` creates `/usr/local/bin/bb` but...

**Problem**: `_get_shell()` defaults to `/bin/sh` (the real shell), NOT the wrapped busybox.

**To use busybox hardening**, the config would need to explicitly set:
```yaml
operator:
  shell_binary: "bb"
```

But even then, it would call `bb` which invokes `busybox` applets. **Direct calls to `/bin/rm`, `/bin/chmod`, etc. would still work** because they're native tools, not busybox applets.

---

## Recommendations

### Priority 1: Add Command Validation (URGENT)

```python
# beigebox/tools/system_info.py

def _is_command_allowed(cmd: str) -> bool:
    """Check if command passes allowlist and blocked patterns."""
    from beigebox.config import get_config
    cfg = get_config()
    
    # Get allowed commands from config
    allowed = cfg.get("operator", {}).get("shell", {}).get("allowed_commands", [])
    blocked_patterns = cfg.get("operator", {}).get("shell", {}).get("blocked_patterns", [])
    
    if not allowed:
        return False  # If no allowlist, deny everything
    
    # Extract base command (before first space or pipe)
    base_cmd = cmd.split()[0].split("|")[0].split(";")[0].strip()
    
    # Check allowlist
    if base_cmd not in allowed:
        return False
    
    # Check blocked patterns
    for pattern in blocked_patterns:
        if pattern in cmd:
            return False
    
    return True

def _run(cmd: str) -> str:
    """Run a shell command via the configured shell binary, return stdout or empty string."""
    if not _is_command_allowed(cmd):
        logger.warning("Command blocked: %s", cmd[:100])
        return ""  # Silently fail or raise?
    
    try:
        shell = _get_shell()
        result = subprocess.run(
            [shell, "-c", cmd], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        logger.error("Command execution failed: %s — %s", cmd[:100], e)
        return ""
```

### Priority 2: Add Command Execution Logging

```python
def _run(cmd: str) -> str:
    """Run a shell command via the configured shell binary, return stdout or empty string."""
    if not _is_command_allowed(cmd):
        logger.warning("BLOCKED_COMMAND: %s", cmd[:100])
        return ""
    
    logger.info("OPERATOR_SHELL: %s", cmd[:100])  # Audit trail
    
    try:
        shell = _get_shell()
        result = subprocess.run(
            [shell, "-c", cmd], capture_output=True, text=True, timeout=5
        )
        logger.info("OPERATOR_SHELL_RESULT: exit=%d, len=%d", result.returncode, len(result.stdout))
        return result.stdout.strip()
    except Exception as e:
        logger.error("OPERATOR_SHELL_EXCEPTION: %s", e)
        return ""
```

### Priority 3: Use Busybox Wrapper by Default

```yaml
# In config.yaml
operator:
  shell_binary: "bb"  # Use hardened busybox wrapper, not raw /bin/sh
```

Or better, detect it:

```python
def _get_shell() -> str:
    """Return the configured shell binary, defaulting to hardened busybox."""
    global _SHELL_BINARY
    if _SHELL_BINARY is None:
        try:
            from beigebox.config import get_config
            from pathlib import Path
            
            cfg = get_config()
            shell = cfg.get("operator", {}).get("shell_binary")
            
            # If not specified, use bb if available, else /bin/sh
            if not shell:
                if Path("/usr/local/bin/bb").exists():
                    shell = "bb"
                else:
                    shell = "/bin/sh"
            
            _SHELL_BINARY = shell
        except Exception:
            _SHELL_BINARY = "/bin/sh"
    return _SHELL_BINARY
```

### Priority 4: Make Shell Access Explicit & Auditable

Option A: Create a dedicated `shell_command` tool in registry:

```python
# beigebox/tools/shell_command.py
class ShellCommandTool:
    """Execute shell commands (allowlisted & audited)."""
    
    def __init__(self):
        self.description = "Execute a shell command (must be allowlisted)"
    
    def run(self, cmd: str) -> str:
        """Execute command with validation."""
        # Use same _is_command_allowed() from system_info
        if not _is_command_allowed(cmd):
            return f"ERROR: Command not allowed: {cmd}"
        
        # Log to wiretap or audit log
        logger.warning("OPERATOR_SHELL_EXEC: %s", cmd[:200])
        
        return _run(cmd)
```

Then add to registry:

```python
# beigebox/tools/registry.py
if tools_cfg.get("shell", {}).get("enabled", False):
    self.tools["shell"] = ShellCommandTool()
```

And in config:

```yaml
tools:
  enabled: true
  shell:
    enabled: false  # Feature flag, disabled by default
```

**Benefit**: Operator agent can explicitly call shell tool, making it visible in logs and audit trail.

### Priority 5: Tighten Allowlist

Current:
```yaml
allowed_commands:
  - ls
  - cat
  - grep
  - ps
  - df
  - free
  - ollama      # ← Can restart Ollama or pull models
  - beigebox    # ← Can shut down BeigeBox or change config
```

**Risks**:
- `ollama` can pull/delete models, restart service
- `beigebox` is too broad (depends on what subcommands it has)

**Recommendation**: Whitelist specific subcommands:
```yaml
allowed_commands:
  - "ls"
  - "cat"
  - "grep"
  - "ps"
  - "df"
  - "free"
  # - "ollama ps"          # Only view models
  # - "beigebox flash"     # Only stats queries
```

Then match full command, not just prefix.

---

## Summary Table

| Issue | Severity | Status |
|-------|----------|--------|
| Allowlist not enforced in code | 🔴 HIGH | ❌ Gap |
| No command execution logging | 🟡 MEDIUM | ❌ Gap |
| Busybox wrapper not used by default | 🟡 MEDIUM | ❌ Gap |
| Blocked patterns not enforced | 🟡 MEDIUM | ❌ Gap |
| No audit trail for shell access | 🟡 MEDIUM | ❌ Gap |
| Shell tool not explicit in registry | 🟡 MEDIUM | ⚠️ Design |

---

## The Good News ✅

- Busybox wrapper is well-designed and comprehensive
- Code runs as non-root (appuser) — limits damage
- 5-second command timeout prevents hangs
- Most dangerous commands (rm, chmod, kill) are blocked at OS level
- `system_info.run()` is only called from `SystemInfoTool.run()`, not exposed to LLM directly

## The Bad News ⚠️

- Config allowlist is ignored by the code that runs commands
- Silent failures (exceptions are caught and return "")
- No visibility into which commands the operator actually executes
- Operator agent could theoretically escalate via `system_info.run()`

---

## Quick Wins

1. **Today**: Add `_is_command_allowed()` check in `_run()` (~10 lines)
2. **Today**: Add logging for all command execution (~3 lines)
3. **Today**: Change default shell from `/bin/sh` to `bb` (1 line)
4. **This week**: Add `blocked_patterns` enforcement (~5 lines)
5. **This sprint**: Create explicit `shell_command` tool in registry (make it auditable)
