# Operator Agent Workspace — ZIP File Mounting Design

**Date**: February 28, 2026  
**Status**: Design Proposal  
**Purpose**: Enable operator agent to access curated tools/scripts/data via mounted ZIP

---

## Problem Statement

Currently, the operator agent has **no persistent workspace** — it can only run whitelisted shell commands with no access to custom tools, scripts, or data files beyond what's on the host.

**Current limitations**:
- Agent can't access custom Python scripts
- Agent can't access data files (CSVs, configs, datasets)
- Agent can't use specialized tools not in the allowlist
- Agent can't maintain state across iterations (no scratch space)
- If nvidia-smi fails, there's no fallback or workaround environment

**Desired behavior**:
- Upload a `operator-tools.zip` containing tools/scripts/data
- BeigeBox extracts it to a sandbox directory
- Operator can call those tools via the shell
- Extraction is repeatable (can update the ZIP and reload)
- Isolation is maintained (can't escape sandbox)

---

## Architecture

### Option A: ZIP Mount + Extract (Recommended)

**Workflow**:

1. **Admin uploads or provides `operator-tools.zip`** at startup:
   ```yaml
   operator:
     workspace:
       enabled: true
       zip_path: ./operator-tools.zip      # Path to ZIP (relative or absolute)
       extract_dir: /tmp/operator_env      # Where to extract
       auto_reload: true                   # Re-extract on file mtime change
   ```

2. **BeigeBox extracts at startup** (or on first operator call):
   - Unzip `operator-tools.zip` → `/tmp/operator_env/`
   - Keep directory structure intact
   - Set permissions: `chmod 755 /tmp/operator_env/*`

3. **Operator shell gets PATH updated**:
   ```bash
   PATH=/tmp/operator_env/bin:$PATH nvidia-smi
   # or directly:
   /tmp/operator_env/bin/nvidia-smi
   ```

4. **Operator can now execute**:
   - Shell scripts in `/tmp/operator_env/bin/`
   - Python scripts in `/tmp/operator_env/scripts/`
   - Read data files in `/tmp/operator_env/data/`
   - Write to `/tmp/operator_env/scratch/` (temporary)

### Option B: ZIP Mount + Lazy Extract

Same as Option A, but extract only on first shell call (faster startup if ZIP not needed).

### Option C: Direct ZIP Exploration (Simpler)

Agent uses `unzip -l operator-tools.zip` to list contents, then explicitly extracts files on demand:
```
operator: "Extract data.csv from the ZIP to /tmp/"
tool: "unzip -x operator-tools.zip data.csv -d /tmp/"
```

Less automated, but gives agent visibility into available tools.

---

## Implementation Details

### File Structure

**operator-tools.zip** (user-provided):
```
operator-tools.zip
├── bin/                    # Executable scripts/binaries
│   ├── nvidia-smi-wrapper  # Custom nvidia-smi wrapper
│   ├── process-data.py     # Python script to process CSVs
│   └── analyze.sh          # Shell script
├── scripts/                # Longer Python/shell scripts
│   ├── deep-analysis.py
│   └── report.sh
├── data/                   # Static data files
│   ├── config.json
│   └── samples.csv
└── README.md               # Documentation for operator
```

**Extracted in container**:
```
/tmp/operator_env/
├── bin/
│   ├── nvidia-smi-wrapper
│   ├── process-data.py
│   └── analyze.sh
├── scripts/
│   ├── deep-analysis.py
│   └── report.sh
├── data/
│   ├── config.json
│   └── samples.csv
├── scratch/                # Operator can write here
└── README.md
```

### Config Changes

**config.yaml**:
```yaml
operator:
  enabled: false                    # Master switch
  model: "llama3.2:3b"
  max_iterations: 10
  allowed_tools: []                 # Empty = all tools
  
  shell:
    enabled: true
    shell_binary: "/usr/local/bin/bb"
    allowed_commands:               # Existing allowlist
      - cat
      - grep
      - ls
      - # ... etc
    blocked_patterns:
      - "rm -rf"
      - # ... etc
  
  # NEW: Workspace configuration
  workspace:
    enabled: false                  # Set to true to enable ZIP mounting
    zip_path: "./operator-tools.zip"           # Path to ZIP file
    extract_dir: "/tmp/operator_env"           # Where to extract
    auto_reload: true                          # Re-extract if ZIP mtime changes
    max_extract_size_mb: 100                   # Prevent zip bombs
    preserve_permissions: true                 # Keep execute bits from ZIP
```

### Python Implementation

**beigebox/operators/workspace.py** (new file):

```python
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

class OperatorWorkspace:
    """Manage extracted ZIP workspace for operator agent."""
    
    def __init__(self, cfg: dict):
        self.cfg = cfg.get("operator", {}).get("workspace", {})
        self.enabled = self.cfg.get("enabled", False)
        self.zip_path = self.cfg.get("zip_path", "./operator-tools.zip")
        self.extract_dir = Path(self.cfg.get("extract_dir", "/tmp/operator_env"))
        self.auto_reload = self.cfg.get("auto_reload", True)
        self.max_size_mb = self.cfg.get("max_extract_size_mb", 100)
        self._last_mtime = None
        
        if self.enabled:
            self._initial_extract()
    
    def _initial_extract(self) -> bool:
        """Extract ZIP at startup or if mtime changed."""
        if not os.path.exists(self.zip_path):
            logger.warning("Workspace ZIP not found: %s", self.zip_path)
            return False
        
        try:
            # Check if reload is needed
            current_mtime = os.path.getmtime(self.zip_path)
            if not self.auto_reload and self._last_mtime and current_mtime == self._last_mtime:
                return True  # Already extracted, no change
            
            self._last_mtime = current_mtime
            self.extract_dir.mkdir(parents=True, exist_ok=True)
            
            # Validate ZIP size
            zip_size_mb = os.path.getsize(self.zip_path) / (1024 * 1024)
            if zip_size_mb > self.max_size_mb:
                logger.error("ZIP too large: %.1f MB > %d MB", zip_size_mb, self.max_size_mb)
                return False
            
            # Extract
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                zf.extractall(self.extract_dir)
            
            # Fix permissions
            self._fix_permissions()
            
            logger.info("Workspace extracted: %s → %s", self.zip_path, self.extract_dir)
            return True
        
        except Exception as e:
            logger.error("Workspace extraction failed: %s", e)
            return False
    
    def _fix_permissions(self):
        """Make scripts executable, keep data readable."""
        for root, dirs, files in os.walk(self.extract_dir):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
            for f in files:
                fpath = os.path.join(root, f)
                if root.endswith("/bin") or root.endswith("/scripts"):
                    os.chmod(fpath, 0o755)  # Executable
                else:
                    os.chmod(fpath, 0o644)  # Readable
    
    def get_path(self) -> str:
        """Return the extracted workspace path."""
        return str(self.extract_dir) if self.enabled else ""
    
    def get_env_updates(self) -> dict:
        """Return env vars to inject into operator shell."""
        if not self.enabled:
            return {}
        
        workspace = str(self.extract_dir)
        return {
            "OPERATOR_WORKSPACE": workspace,
            "PATH": f"{workspace}/bin:{os.environ.get('PATH', '')}",
        }
```

**beigebox/tools/system_info.py** (modified):

```python
def _run(cmd: str, gpu: bool = False, workspace_env: dict = None) -> str:
    """
    Run shell command with optional workspace environment.
    
    Args:
        cmd: Command to run
        gpu: Use GPU bwrap profile
        workspace_env: Dict of env vars to inject (from OperatorWorkspace.get_env_updates())
    """
    is_allowed, reason = _is_command_allowed(cmd)
    if not is_allowed:
        _audit_log(cmd, reason, False)
        return ""
    
    try:
        if _bwrap_available():
            argv = _bwrap_argv(gpu=gpu) + ["/bin/sh", "-c", cmd]
        else:
            shell = _get_shell()
            if os.path.basename(shell) == "bb":
                argv = [shell, "sh", "-c", cmd]
            else:
                argv = [shell, "-c", cmd]
        
        env = os.environ.copy()
        if workspace_env:
            env.update(workspace_env)  # Inject workspace PATH, etc
        
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,  # Pass updated env
        )
        
        # ... rest of function
```

### Operator Integration

**beigebox/agents/operator.py** (at initialization):

```python
from beigebox.operators.workspace import OperatorWorkspace

class OperatorAgent:
    def __init__(self, cfg):
        # ... existing code ...
        self.workspace = OperatorWorkspace(cfg)
        self.workspace_env = self.workspace.get_env_updates()
    
    async def run(self, query: str, ...):
        # ... tool calls pass workspace_env to system_info ...
        result = system_info_tool.run(cmd, workspace_env=self.workspace_env)
```

---

## Example Use Case

### Scenario: Custom GPU Monitoring Script

User wants operator to monitor GPU with a custom script that `nvidia-smi` can't access.

**operator-tools.zip**:
```
bin/
  gpu-monitor.py      # Custom monitoring script
  nvidia-wrapper.sh   # Wrapper for nvidia-smi
data/
  thresholds.json     # Alert thresholds
```

**gpu-monitor.py**:
```python
#!/usr/bin/env python3
import subprocess
import json

with open("/tmp/operator_env/data/thresholds.json") as f:
    thresholds = json.load(f)

# Custom logic using nvidia-smi or other tools
result = subprocess.run(["nvidia-smi", "--query-gpu=..."], capture_output=True)
# ... process and alert ...
```

**Operator call**:
```
User: "Monitor the GPU and report if any card is overheating"
Operator: gpu-monitor.py
Result: "GPU 0 is at 82°C — above 80° threshold. Recommending workload reduction."
```

---

## Security Considerations

### Threat Model

**Who provides the ZIP?**
- **Admin/trusted operator** — they control the server anyway; assume trusted
- **Not user-uploaded** — no untrusted ZIP uploads from chat

**What's the risk?**
- ZIP bomb (100GB uncompressed) → mitigated by `max_extract_size_mb`
- Symlink attacks (ZIP contains `../../etc/passwd`) → Python `zipfile` extracts safely by default
- Executable scripts with malicious code → expected; admin reviews before uploading
- Operator escaping sandbox → mitigated by bwrap + allowlist

### Best Practices

1. **Only extract trusted ZIPs** — admin provides/reviews content
2. **Keep `max_extract_size_mb` reasonable** (default 100MB)
3. **Don't expose ZIP path to users** — only admins update it
4. **Review extracted content** before enabling operator:
   ```bash
   unzip -l operator-tools.zip
   ```
5. **Use allowlist** to prevent calling arbitrary scripts:
   ```yaml
   allowed_commands:
     - gpu-monitor.py    # Explicit whitelist
     - nvidia-wrapper.sh
     - /tmp/operator_env/bin/*  # Or wildcard if needed
   ```

---

## Configuration Examples

### Example 1: Minimal Setup (Just enable it)

```yaml
operator:
  workspace:
    enabled: true
    zip_path: ./operator-tools.zip
```

### Example 2: Large Workspace + Custom Paths

```yaml
operator:
  workspace:
    enabled: true
    zip_path: /mnt/shared/operator-tools.zip
    extract_dir: /var/lib/beigebox/operator_env
    auto_reload: true
    max_extract_size_mb: 500
```

### Example 3: Disabled (Default)

```yaml
operator:
  workspace:
    enabled: false
```

---

## Testing

### Test 1: Extract and list

```python
def test_workspace_extract():
    """Workspace extracts ZIP and preserves structure."""
    cfg = {"operator": {"workspace": {
        "enabled": True,
        "zip_path": "tests/fixtures/operator-tools.zip",
        "extract_dir": "/tmp/test_operator_env",
    }}}
    ws = OperatorWorkspace(cfg)
    assert (Path("/tmp/test_operator_env") / "bin" / "test.sh").exists()
    assert os.access("/tmp/test_operator_env/bin/test.sh", os.X_OK)  # Executable
```

### Test 2: PATH injection

```python
def test_workspace_env_updates():
    """Env vars include workspace in PATH."""
    cfg = {"operator": {"workspace": {
        "enabled": True,
        "zip_path": "tests/fixtures/operator-tools.zip",
        "extract_dir": "/tmp/test_operator_env",
    }}}
    ws = OperatorWorkspace(cfg)
    env = ws.get_env_updates()
    assert "/tmp/test_operator_env/bin:" in env["PATH"]
    assert env["OPERATOR_WORKSPACE"] == "/tmp/test_operator_env"
```

### Test 3: Shell command with workspace

```python
def test_system_info_with_workspace():
    """Shell command runs with workspace env."""
    ws_env = {
        "PATH": "/tmp/test_operator_env/bin:/bin:/usr/bin",
        "OPERATOR_WORKSPACE": "/tmp/test_operator_env",
    }
    result = _run("test.sh", workspace_env=ws_env)
    assert "success" in result
```

---

## Migration Path

### v1.0 → v1.1 (This Feature)

1. **Step 1**: Add `OperatorWorkspace` class (Python)
2. **Step 2**: Add workspace config to `config.yaml`
3. **Step 3**: Modify `SystemInfoTool._run()` to accept `workspace_env`
4. **Step 4**: Initialize `OperatorAgent` with workspace
5. **Step 5**: Document in README with examples
6. **Step 6**: Add test coverage (5-10 tests)
7. **Step 7**: Release as v1.1.0

### Backward Compatibility

- Workspace is **disabled by default** — zero impact if not configured
- Existing operator behavior unchanged if `workspace.enabled: false`
- New parameter is optional (all defaults safe)

---

## FAQ

**Q: Can users upload custom tools via the chat?**
A: No. ZIP is provided by admin at startup. Future: could add `/api/v1/operator/upload-tools` endpoint with proper auth.

**Q: What if the ZIP is corrupted?**
A: Extraction fails gracefully; operator falls back to allowlisted commands only. Logged as warning.

**Q: Can operator read/write to `/app` or `/home`?**
A: No. Workspace is isolated to `/tmp/operator_env/` and allowlist prevents directory escapes.

**Q: Is there a hot-reload for ZIP changes?**
A: Yes, if `auto_reload: true`. Next shell call checks mtime and re-extracts if changed.

**Q: Can I use environment variables in tool scripts?**
A: Yes. `$OPERATOR_WORKSPACE` and `$PATH` are available in scripts running under operator.

---

## Future Extensions

1. **Multi-workspace profiles** — Different ZIPs for different operator models
2. **Workspace versioning** — Keep multiple extracted versions, switch between them
3. **User-uploaded tools** — Sandboxed upload with admin approval
4. **Workspace templates** — Pre-built ZIPs for common tasks (data analysis, DevOps, etc.)
5. **Workspace marketplace** — Share/reuse curated tool collections

---

## References

- **bubblewrap sandboxing** — Already implemented in `system_info.py`
- **allowlist enforcement** — `system_info.py:152-172`
- **audit logging** — `system_info.py:175-184`
- **operator agent** — `beigebox/agents/operator.py`
