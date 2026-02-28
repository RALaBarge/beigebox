# Operator Workspace Implementation Guide

**Date**: February 28, 2026  
**Feature**: ZIP File Mounting for Operator Agent  
**Status**: Ready to Deploy

---

## Overview

Your operator agent can now access custom tools, scripts, and data files via a **mounted ZIP file**. This solves the `nvidia-smi` access issue and enables you to provide the operator with whatever environment it needs.

**How it works**:
1. You create a `operator-tools.zip` with custom tools/scripts/data
2. BeigeBox extracts it to `/tmp/operator_env/` at startup
3. Operator's shell gets `$PATH` updated to include `/tmp/operator_env/bin/`
4. Operator can now call those tools as if they were system commands

---

## Files Changed

| File | Changes |
|------|---------|
| **beigebox/operators/__init__.py** | NEW - Empty module init file |
| **beigebox/operators/workspace.py** | NEW - Workspace manager (ZIP extraction + env injection) |
| **beigebox/agents/operator.py** | MODIFIED - Initialize workspace + pass env to tools |
| **beigebox/tools/system_info.py** | MODIFIED - Accept workspace_env parameter in _run() and run() |
| **config.yaml** | MODIFIED - Added operator.workspace section |

---

## Deployment Steps

### Step 1: Copy Files Into Place

```bash
# From your beigebox project root:

# Create operators directory if it doesn't exist
mkdir -p beigebox/operators

# Copy new files
cp operators__init__.py beigebox/operators/__init__.py
cp workspace.py beigebox/operators/workspace.py

# Copy modified files (overwrites existing)
cp operator.py beigebox/agents/operator.py
cp system_info.py beigebox/tools/system_info.py
cp config.yaml config.yaml
```

Or with mv commands if you prefer:
```bash
mv operators__init__.py beigebox/operators/__init__.py
mv workspace.py beigebox/operators/workspace.py
mv operator.py beigebox/agents/operator.py
mv system_info.py beigebox/tools/system_info.py
mv config.yaml config.yaml
```

### Step 2: Create Your First operator-tools.zip

Create a ZIP with tools/scripts for your operator:

```bash
# Create directory structure
mkdir -p operator-workspace/bin
mkdir -p operator-workspace/scripts
mkdir -p operator-workspace/data
mkdir -p operator-workspace/scratch

# Example: Add a custom GPU monitor script
cat > operator-workspace/bin/gpu-check.sh << 'EOF'
#!/bin/bash
# Custom GPU health check script
echo "=== GPU Status ==="
nvidia-smi --query-gpu=index,name,memory.used,memory.total,temperature.gpu \
  --format=csv,noheader,nounits 2>/dev/null || echo "nvidia-smi not available"
EOF

chmod +x operator-workspace/bin/gpu-check.sh

# Example: Add configuration data
cat > operator-workspace/data/thresholds.json << 'EOF'
{
  "gpu_temp_warning": 70,
  "gpu_temp_critical": 85,
  "memory_usage_warning": 0.85
}
EOF

# Create the ZIP
cd operator-workspace
zip -r ../operator-tools.zip .
cd ..

# Verify ZIP structure
unzip -l operator-tools.zip
```

### Step 3: Configure in config.yaml

In your `config.yaml`, find the `operator.workspace` section and enable it:

```yaml
operator:
  workspace:
    enabled: true                       # ← Change from false to true
    zip_path: "./operator-tools.zip"    # Path to your ZIP
    extract_dir: "/tmp/operator_env"    # Extraction directory
    auto_reload: true                   # Re-extract if ZIP changes
    max_extract_size_mb: 100            # Adjust if needed
```

### Step 4: Restart BeigeBox

If running in Docker:
```bash
docker compose up --build -d
```

Or local:
```bash
# Kill existing process
pkill -f "python.*beigebox"

# Restart
python -m beigebox.main
```

### Step 5: Verify It Worked

Check the logs for workspace extraction message:

```bash
docker logs beigebox | grep -i workspace
# Should see: "Operator workspace: extracted ./operator-tools.zip → /tmp/operator_env"
```

Or test via the operator:

1. Go to **http://localhost:8001** (or your BeigeBox URL)
2. Click **Operator** tab
3. Ask: `"What tools are available in the workspace?"`
4. Operator should respond with contents of `/tmp/operator_env/`

---

## Example Use Cases

### Use Case 1: Custom GPU Monitoring

**ZIP structure**:
```
operator-tools.zip
├── bin/
│   └── gpu-monitor.py     # Python script with custom logic
├── data/
│   └── alerts.json        # Alert configuration
└── README.md
```

**gpu-monitor.py**:
```python
#!/usr/bin/env python3
import json
import subprocess

with open("/tmp/operator_env/data/alerts.json") as f:
    alerts = json.load(f)

# Use nvidia-smi or other tools
result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used"], 
                       capture_output=True, text=True)
memory = int(result.stdout.strip())

if memory > alerts["memory_warning"]:
    print(f"⚠️  GPU memory high: {memory}MB")
```

**Ask operator**:
```
"Run the GPU monitoring script and alert if anything is concerning"
```

### Use Case 2: Data Analysis Tools

**ZIP structure**:
```
operator-tools.zip
├── bin/
│   └── analyze.py         # CSV/data analysis script
├── data/
│   ├── sample.csv
│   └── config.yaml
└── README.md
```

**Ask operator**:
```
"Analyze the sample data and tell me the key metrics"
```

### Use Case 3: System Diagnostics

**ZIP structure**:
```
operator-tools.zip
├── bin/
│   ├── system-health.sh
│   ├── network-check.sh
│   └── disk-analysis.py
├── scripts/
│   └── report.sh           # Composite reporting
└── README.md
```

**Ask operator**:
```
"Run a full system health check and generate a report"
```

---

## Configuration Reference

### operator.workspace section

```yaml
operator:
  workspace:
    # Master enable/disable switch
    enabled: false
    
    # Path to ZIP file (relative or absolute)
    # - Relative: ./operator-tools.zip (relative to working directory)
    # - Absolute: /mnt/shared/operator-tools.zip
    # - Environment var: $HOME/operator-tools.zip
    zip_path: "./operator-tools.zip"
    
    # Where to extract ZIP inside container
    # - Must be writable by the appuser (UID 1000)
    # - /tmp/ is safe and ephemeral
    # - Can be /var/lib/beigebox/operator_env for persistence
    extract_dir: "/tmp/operator_env"
    
    # Auto-reload: Check ZIP mtime and re-extract if changed
    # - true: Operator reloads ZIP on every tool call if file changed
    # - false: Extract once at startup, never reload
    auto_reload: true
    
    # Maximum ZIP file size in MB
    # - Prevents zip bombs (100MB default is safe for most uses)
    # - Set to 0 for unlimited (not recommended)
    max_extract_size_mb: 100
```

### What Goes in the ZIP

```
operator-tools.zip/
│
├── bin/                       # Executable scripts (chmod 755)
│   ├── my-script.sh
│   ├── analyze.py
│   └── monitor.sh
│
├── scripts/                   # Longer scripts (chmod 755)
│   ├── complex-job.py
│   └── reporting.sh
│
├── data/                      # Static data files (chmod 644)
│   ├── config.json
│   ├── thresholds.yaml
│   └── sample.csv
│
├── scratch/                   # Operator can write here
│   └── (empty at start)
│
└── README.md                  # Documentation for operator
```

**Permissions**:
- `/bin/*` and `/scripts/*` → **executable** (755)
- `/data/*` → **readable** (644)
- All directories → **755**

BeigeBox automatically fixes permissions after extraction.

---

## Troubleshooting

### Issue: "Operator workspace: ZIP not found"

**Cause**: `zip_path` points to non-existent file  
**Fix**: Check path in config.yaml:
```bash
ls -la ./operator-tools.zip    # If relative path
ls -la /path/to/operator-tools.zip  # If absolute path
```

### Issue: "Operator workspace: ZIP too large"

**Cause**: ZIP exceeds `max_extract_size_mb`  
**Fix**: Increase limit or make ZIP smaller:
```yaml
max_extract_size_mb: 500  # Increased from 100
```

### Issue: Operator can't find tools in workspace

**Cause**: Tools aren't in `/bin/` subdirectory  
**Fix**: ZIP structure must have `bin/` folder:
```bash
# Wrong structure (won't work)
unzip -l operator-tools.zip
  my-script.sh       ← At root level

# Right structure (will work)
unzip -l operator-tools.zip
  bin/
    my-script.sh     ← In bin/ folder
```

### Issue: Permission denied when running workspace tools

**Cause**: Files don't have execute permission in ZIP  
**Fix**: Fix before zipping:
```bash
chmod +x operator-workspace/bin/*
chmod +x operator-workspace/scripts/*
zip -r operator-tools.zip operator-workspace/
```

### Issue: Workspace doesn't reload when ZIP changes

**Cause**: `auto_reload: false` in config  
**Fix**: Enable auto-reload:
```yaml
workspace:
  auto_reload: true
```

Or manually restart BeigeBox if auto-reload is off.

---

## Security Notes

### What's Protected

✅ **ZIP size limit** — Prevents zip bombs  
✅ **Safe extraction** — Python `zipfile` module is safe from path traversal  
✅ **Sandbox isolation** — Workspace runs inside bwrap/busybox sandbox  
✅ **Allowlist enforcement** — Operator still needs allowlisted commands  
✅ **Audit logging** — Every shell call is logged  

### What You Control

🔐 **ZIP contents** — You provide what goes in the ZIP (trust it)  
🔐 **Operator enabled** — Only if `operator.enabled: true`  
🔐 **Workspace enabled** — Only if `workspace.enabled: true`  
🔐 **File permissions** — ZIP extraction fixes permissions automatically  

### Best Practices

1. **Only enable workspace for trusted ZIPs** — Admin reviews content before enabling
2. **Use allowlist** — Keep `operator.allowed_tools` restrictive if possible
3. **Review ZIP contents** — Before uploading:
   ```bash
   unzip -l operator-tools.zip
   ```
4. **Keep ZIP small** — Large files slow down extraction and waste memory
5. **Version your ZIPs** — Use naming like `operator-tools-v1.0.zip`

---

## Examples: Complete ZIP Archives

### Example 1: GPU Management Tools

**Create the archive**:
```bash
mkdir -p gpu-tools/{bin,data,scratch}

# Add GPU monitoring script
cat > gpu-tools/bin/gpu-health.py << 'EOF'
#!/usr/bin/env python3
import subprocess, json
result = subprocess.run(["nvidia-smi", "--json"], capture_output=True, text=True)
data = json.loads(result.stdout)
gpus = data.get("nvidia_smi_log", {}).get("gpu", [])
for gpu in (gpus if isinstance(gpus, list) else [gpus]):
    print(f"GPU {gpu['gpu_bus_id']}: {gpu['temperature']['gpu_temp']}°C")
EOF

chmod +x gpu-tools/bin/gpu-health.py

# Create config
cat > gpu-tools/data/config.json << 'EOF'
{"alert_temp": 80, "check_interval": 300}
EOF

# Add README
cat > gpu-tools/README.md << 'EOF'
# GPU Tools for Operator

## Available Tools
- gpu-health.py - Check GPU temperature and memory

## Usage
Ask the operator: "Check GPU health and alert if any issues"
EOF

# Create ZIP
cd gpu-tools && zip -r ../gpu-tools.zip . && cd ..

# Deploy
cp gpu-tools.zip ~/beigebox/
echo "zip_path: ./gpu-tools.zip" >> ~/beigebox/config.yaml
```

### Example 2: System Analysis Tools

```bash
mkdir -p sys-tools/{bin,scripts,data}

# Add analysis scripts
cat > sys-tools/bin/analyze-logs.sh << 'EOF'
#!/bin/bash
# Analyze system logs for errors
grep -i error /var/log/syslog 2>/dev/null | tail -20 || echo "No errors found"
EOF

cat > sys-tools/scripts/network-diag.py << 'EOF'
#!/usr/bin/env python3
import subprocess
result = subprocess.run(["netstat", "-an"], capture_output=True, text=True)
connections = len(result.stdout.split('\n'))
print(f"Active connections: {connections}")
EOF

chmod +x sys-tools/bin/* sys-tools/scripts/*

cd sys-tools && zip -r ../sys-tools.zip . && cd ..
cp sys-tools.zip ~/beigebox/
```

---

## Testing

### Test 1: Verify extraction
```bash
# Start BeigeBox and check logs
docker logs beigebox | grep "workspace: extracted"

# Should output:
# Operator workspace: extracted ./operator-tools.zip → /tmp/operator_env
```

### Test 2: Verify PATH injection
```bash
# In web UI Operator tab, ask:
"What tools are in my workspace?"

# Operator should list /tmp/operator_env/bin/ contents
```

### Test 3: Call a workspace tool
```bash
# Ask:
"Run gpu-monitor.py and tell me what it reports"

# Operator calls the script and returns output
```

---

## Migration from Old Setup

If you were working around the workspace limitation:

**Before** (manually passing commands):
```
User: "run this command: /path/to/custom-script.sh"
Operator: Can't find it (not in allowlist or PATH)
```

**After** (with workspace):
```
User: "analyze the system"
Operator: Automatically finds and runs /tmp/operator_env/bin/analyze.sh
```

---

## Next Steps

1. ✅ Deploy the 5 updated files
2. ✅ Create your `operator-tools.zip` with custom tools
3. ✅ Enable `workspace.enabled: true` in config.yaml
4. ✅ Restart BeigeBox
5. ✅ Test with Operator tab in web UI
6. ✅ Iterate: Add more tools, update ZIP, reload

---

## Reference

- **Workspace manager**: `beigebox/operators/workspace.py`
- **Integration point**: `beigebox/agents/operator.py` line ~118
- **Shell execution**: `beigebox/tools/system_info.py` function `_run()`
- **Config**: `config.yaml` section `operator.workspace`

---

## Questions?

- Check logs: `docker logs beigebox | grep operator`
- Verify ZIP: `unzip -l operator-tools.zip`
- Test extraction: `ls -la /tmp/operator_env/`
