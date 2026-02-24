# BeigeBox Configuration Guide — Harness & Operator Shell Security

**Updated**: February 23, 2026  
**Scope**: Complete configuration for harness orchestrator and operator shell hardening  
**Files**: `config.yaml` (permanent) and `runtime_config.yaml` (session overrides)

---

## Quick Reference

### For Production (Recommended)

**config.yaml**:
```yaml
# Harness orchestrator
harness:
  enabled: true
  retry:
    max_retries: 2
    backoff_base: 1.5
    backoff_max: 10
  stagger:
    operator_seconds: 1.0
    model_seconds: 0.4
  timeouts:
    task_seconds: 120
    operator_seconds: 180
  store_runs: true
  max_stored_runs: 1000

# Operator shell security
operator:
  shell:
    enabled: true
    shell_binary: "/usr/local/bin/bb"
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - ollama
      - beigebox
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
      - "; "
      - "| "
```

---

## Configuration Files

### 1. config.yaml (Permanent Settings)

**Location**: Project root  
**Purpose**: Permanent configuration that persists across restarts  
**Hot-reload**: No (requires restart)

**Key sections for v1.0**:

#### Harness Orchestrator
```yaml
harness:
  enabled: false                          # Set to true to enable
  retry:
    max_retries: 2                        # Retry transient errors up to 2 times
    backoff_base: 1.5                     # Exponential backoff: 1.5^attempt
    backoff_max: 10                       # Cap backoff at 10 seconds
  stagger:
    operator_seconds: 1.0                 # Delay between operator task launches
    model_seconds: 0.4                    # Delay between model task launches
  timeouts:
    task_seconds: 120                     # Per-task HTTP timeout (2 minutes)
    operator_seconds: 180                 # Operator endpoint timeout (3 minutes)
  store_runs: true                        # Persist runs to SQLite
  max_stored_runs: 1000                   # Keep last 1000 runs
```

**Configuration details**:

- **enabled**: Set to `true` to activate harness orchestrator
- **max_retries**: How many times to retry transient errors (0 = no retry)
- **backoff_base**: Exponential backoff multiplier (1.5^attempt seconds)
- **backoff_max**: Maximum wait between retries (prevents infinite waits)
- **operator_seconds**: Stagger between operator task launches (prevents ChromaDB contention)
- **model_seconds**: Stagger between model task launches (Ollama handles concurrent fine)
- **task_seconds**: Global HTTP timeout per task
- **operator_seconds**: Longer timeout for operator (it initializes ChromaDB)
- **store_runs**: Whether to persist orchestration runs to SQLite
- **max_stored_runs**: How many runs to keep (older ones deleted)

#### Operator Shell Security
```yaml
operator:
  model: "llama3.2:3b"                   # Model for operator agent
  max_iterations: 10                      # Max ReAct loop iterations
  shell:
    enabled: true                         # Set to false to disable shell
    shell_binary: "/usr/local/bin/bb"    # Hardened wrapper (preferred)
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - ollama
      - beigebox
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
      - "; "
      - "| "
```

**Configuration details**:

- **enabled**: `true` = shell commands allowed (with allowlist/patterns), `false` = no shell
- **shell_binary**: Path to shell binary
  - `/usr/local/bin/bb` = Hardened busybox wrapper (recommended for production)
  - `/bin/sh` = Standard shell (fallback if wrapper not available)
- **allowed_commands**: Whitelist of permitted base commands
  - Only these commands can be executed
  - Base command extracted before any space/pipe
  - Example: `"ls -la /tmp"` → base is `"ls"` → checked against allowlist
- **blocked_patterns**: Patterns that are always blocked (even if base command allowed)
  - `"rm -rf"` → Delete commands
  - `"sudo"` → Privilege escalation
  - `"> /etc"` → System config modification
  - `"; "` → Command chaining (with space after semicolon)
  - `"| "` → Pipe chaining (with space after pipe)

---

### 2. runtime_config.yaml (Session Overrides)

**Location**: Project root  
**Purpose**: Temporary session-specific overrides  
**Hot-reload**: Yes (mtime check, no restart needed)

**Current structure** (no harness overrides needed yet):
```yaml
runtime:
  default_model: ""                       # Override default model this session
  border_threshold: null                  # Classifier border threshold
  agentic_threshold: null                 # Agentic scorer threshold
  force_route: ""                         # Force all requests to this route
  tools_disabled: []                      # Disable specific tools
  system_prompt_prefix: ""                # Extra system prompt
  web_ui_vi_mode: false                   # Vi keybindings
  web_ui_palette: "default"               # UI color scheme
  log_level: ""                           # Log level override
```

**Why no harness/operator overrides**:
- Harness retry logic is per-request (always enabled if harness enabled)
- Operator shell allowlist should be consistent (not overridden per-session)
- Both are "infrastructure" settings, not "tuning" settings

**If you add runtime overrides later**:
```yaml
runtime:
  harness_max_retries: null              # null = use config.yaml value (2)
  harness_enabled: null                  # null = use config.yaml value
  operator_shell_enabled: null            # null = use config.yaml value
```

---

## Configuration Scenarios

### Scenario 1: Development (Loose)

**goal**: Easy testing, full feature access

```yaml
# config.yaml
harness:
  enabled: true
  retry:
    max_retries: 2
  store_runs: true

operator:
  shell:
    enabled: true
    shell_binary: "/bin/sh"              # Fallback is fine for dev
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - curl                             # Allow web requests
      - git                              # Allow git commands
    blocked_patterns:
      - "rm -rf"
      - "sudo"
```

**Trade-off**: Convenience over security

### Scenario 2: Testing (Medium)

**goal**: Validate features safely

```yaml
# config.yaml
harness:
  enabled: true
  retry:
    max_retries: 2
  store_runs: true

operator:
  shell:
    enabled: true
    shell_binary: "/usr/local/bin/bb"   # Hardened wrapper
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
      - "; "
      - "| "
```

**Trade-off**: Balanced security and functionality

### Scenario 3: Production (Tight)

**goal**: Maximum security

```yaml
# config.yaml
harness:
  enabled: true
  retry:
    max_retries: 3                       # More retries for reliability
    backoff_base: 2.0                    # Longer backoff (2^x instead of 1.5^x)
  store_runs: true
  max_stored_runs: 5000                  # Keep more history

operator:
  shell:
    enabled: false                       # DISABLE shell entirely
    # Or if shell needed, keep strict allowlist:
    # shell_binary: "/usr/local/bin/bb"
    # allowed_commands:
    #   - ls
    #   - cat
    #   - grep
    # blocked_patterns:
    #   - "rm -rf"
    #   - "sudo"
    #   - "> /etc"
```

**Trade-off**: Security over convenience

### Scenario 4: LXD Container (Recommended for Prod)

**goal**: OS-level isolation + app security

```yaml
# config.yaml
harness:
  enabled: true
  retry:
    max_retries: 2
  store_runs: true

operator:
  shell:
    enabled: true                        # Can enable safely in container
    shell_binary: "/usr/local/bin/bb"
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
      - "; "
      - "| "
```

**Why**: Container provides OS-level isolation, app-level allowlist provides extra safety  
**Result**: Best of both worlds

---

## Configuration Changes (v0.9 → v1.0)

### Added in This Session

1. **Harness orchestrator section** (lines ~174-191 in config.yaml)
   - Retry logic configuration
   - Stagger configuration
   - Timeout configuration
   - Run persistence configuration

2. **Operator shell security enhancements** (lines ~217-234 in config.yaml)
   - `shell_binary` setting (prefer `/usr/local/bin/bb`)
   - `allowed_commands` list
   - `blocked_patterns` list
   - Comprehensive comments explaining each setting

### No Changes to runtime_config.yaml

Runtime config remains unchanged (all harness/operator settings are permanent).

---

## How to Configure

### Step 1: Copy Config Files

```bash
# From outputs/ directory
cp config.yaml /path/to/beigebox/
cp runtime_config.yaml /path/to/beigebox/
```

### Step 2: Edit config.yaml (Permanent Settings)

```bash
vim config.yaml
```

**Look for these sections**:
- `harness:` (around line 174)
- `operator: shell:` (around line 217)

**Make your changes**:
- Enable harness: `enabled: true`
- Adjust retry settings as needed
- Tighten allowlist if desired
- Add/remove blocked patterns

### Step 3: (Optional) Edit runtime_config.yaml

For one-off session overrides:

```bash
vim runtime_config.yaml
```

**Example**: Disable harness for this session only:
```yaml
harness:
  enabled: false   # Overrides config.yaml for this session
```

### Step 4: Restart BeigeBox

```bash
pkill -f "python.*beigebox"
python -m beigebox dial
```

**Check logs**:
```bash
tail -30 beigebox.log | grep -i "harness\|shell\|operator"
```

**Should see**:
- "Harness orchestrator configured: retry_max=2, stagger_op=1.0s, stagger_model=0.4s"
- "Operator shell hardened: 7 allowed commands, 5 blocked patterns"

---

## Tuning Guide

### If Harness Tasks Keep Timing Out

**Problem**: Operator endpoint slow, tasks hitting 120s timeout

**Solution 1: Increase timeout**
```yaml
harness:
  timeouts:
    task_seconds: 180              # 3 minutes
    operator_seconds: 240          # 4 minutes
```

**Solution 2: Increase stagger**
```yaml
harness:
  stagger:
    operator_seconds: 2.0          # More space between tasks
    model_seconds: 0.4
```

**Solution 3: Increase retries**
```yaml
harness:
  retry:
    max_retries: 3                 # Try 4 times total
    backoff_base: 2.0              # Wait longer between retries
```

### If Operator Shell Blocked Legitimate Commands

**Problem**: Task needs `curl` or `find` but it's not in allowlist

**Solution**: Add to allowlist carefully
```yaml
operator:
  shell:
    allowed_commands:
      - ls
      - cat
      - grep
      - ps
      - df
      - free
      - curl                        # ⚠️ Web access, monitor logs
      - find                        # ⚠️ Filesystem search, monitor logs
```

**Important**: Commands like `curl` and `find` expand attack surface. Monitor logs:
```bash
grep "OPERATOR_SHELL.*curl" beigebox.log
```

### If Operator Shell Blocks Legitimate Patterns

**Problem**: Task tries `ls | head` but `"| "` is blocked

**Solution 1**: Use shell operators without spaces
```
ls|head     # No space after pipe, still blocked (good!)
# Instead, use separate commands
ls
head -10
```

**Solution 2**: Remove the pattern if truly needed
```yaml
operator:
  shell:
    blocked_patterns:
      - "rm -rf"
      - "sudo"
      - "> /etc"
      # Remove "| " if piping is safe in your context
      # - "| "
```

**Warning**: Pattern blocking is extra safety. Only remove if you're sure.

---

## Monitoring & Compliance

### Daily Log Check

```bash
# How many operator shell commands executed?
grep "OPERATOR_SHELL.*ALLOWED" beigebox.log | wc -l

# How many were blocked (suspicious)?
grep "OPERATOR_SHELL.*DENIED" beigebox.log | wc -l

# What patterns are being blocked?
grep "OPERATOR_SHELL.*DENIED" beigebox.log | grep "blocked_pattern"
```

### Alert Thresholds

Create alerts for:
- `> 10` denied attempts in 1 hour (possible attack)
- Any `blocked_pattern: rm` (deletion attempts)
- Any `blocked_pattern: sudo` (privilege escalation attempts)
- Any `not_in_allowlist: bash` (shell escape attempts)

### Compliance Checklist

```
[ ] Operator shell enabled: yes/no
[ ] Allowlist configured: min 5 safe commands
[ ] Blocked patterns defined: min 3 patterns
[ ] Shell binary set to busybox wrapper: yes/no
[ ] Audit logging enabled: yes/no (should be automatic)
[ ] Daily log reviews in place: yes/no
```

---

## Files Provided

```
outputs/
  ├─ config.yaml              (Complete permanent config)
  └─ runtime_config.yaml      (Session overrides template)
```

Both are **1:1 mirrors** where applicable:
- Harness config only in `config.yaml` (not in runtime)
- Operator shell config only in `config.yaml` (not in runtime)
- Both are "permanent" infrastructure settings

---

## Summary

**For v1.0 Production**:

1. Use provided `config.yaml` (already has harness + operator shell sections)
2. Keep `runtime_config.yaml` as-is (no runtime overrides needed)
3. Adjust allowlist/patterns based on your needs
4. Monitor logs daily for security

**Recommended defaults**:
- Harness: enabled, 2 retries, 1.5 backoff, 1.0s op stagger
- Operator shell: enabled, hardened wrapper, 7 commands, 5 patterns

**For maximum security**: Run in LXD container + disable shell

---

Both files are ready to deploy. No 1:1 mirroring needed—harness and operator shell settings are permanent and live only in `config.yaml`.
