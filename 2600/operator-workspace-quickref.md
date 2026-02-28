# Operator Workspace — Quick Deploy Reference

## The 4-Step Deploy

### 1. Copy Files
```bash
mkdir -p beigebox/operators
cp operators__init__.py beigebox/operators/__init__.py
cp workspace.py beigebox/operators/workspace.py
cp operator.py beigebox/agents/operator.py
cp system_info.py beigebox/tools/system_info.py
cp config.yaml config.yaml
```

### 2. Create Your ZIP
```bash
mkdir -p operator-workspace/{bin,data,scratch}

# Add your custom scripts/tools to bin/
cat > operator-workspace/bin/my-tool.sh << 'EOF'
#!/bin/bash
echo "Hello from operator workspace"
EOF

chmod +x operator-workspace/bin/my-tool.sh

# Create ZIP
cd operator-workspace && zip -r ../operator-tools.zip . && cd ..
```

### 3. Enable in config.yaml
```yaml
operator:
  workspace:
    enabled: true          # ← This one!
    zip_path: "./operator-tools.zip"
    extract_dir: "/tmp/operator_env"
    auto_reload: true
    max_extract_size_mb: 100
```

### 4. Restart
```bash
docker compose up --build -d
# or
python -m beigebox.main
```

---

## What Changed

| File | Type | What It Does |
|------|------|-------------|
| `beigebox/operators/__init__.py` | NEW | Module init file |
| `beigebox/operators/workspace.py` | NEW | ZIP extraction + PATH injection |
| `beigebox/agents/operator.py` | MOD | Initialize workspace at startup |
| `beigebox/tools/system_info.py` | MOD | Accept `workspace_env` param |
| `config.yaml` | MOD | Added `operator.workspace` section |

---

## Verify It Worked

```bash
# Check logs
docker logs beigebox | grep "workspace: extracted"

# Should output:
# "Operator workspace: extracted ./operator-tools.zip → /tmp/operator_env"
```

---

## ZIP Structure

```
operator-tools.zip
├── bin/              ← Scripts go here (chmod 755)
│   ├── script1.sh
│   └── script2.py
├── data/             ← Config/data (chmod 644)
│   └── config.json
└── scratch/          ← Writable temp space
```

---

## How It Works

1. **At startup**: BeigeBox extracts `operator-tools.zip` → `/tmp/operator_env/`
2. **In operator**: PATH gets updated to include `/tmp/operator_env/bin/`
3. **In shell**: Commands like `my-tool.sh` are found in workspace
4. **Bonus**: Auto-reload if ZIP changes (if `auto_reload: true`)

---

## Common Problems

| Problem | Solution |
|---------|----------|
| "ZIP not found" | Check path in config.yaml |
| "Permission denied" | `chmod +x operator-workspace/bin/*` before zipping |
| "Tool not found" | Must be in `bin/` subfolder, not at ZIP root |
| "Doesn't reload" | Set `auto_reload: true` or restart BeigeBox |
| "ZIP too large" | Increase `max_extract_size_mb` in config.yaml |

---

## Test It

In **Operator tab** of web UI:

```
Q: What tools are in my workspace?
A: [Lists contents of /tmp/operator_env/bin/]

Q: Run my-tool.sh and tell me what it outputs
A: [Executes your script and returns output]
```

---

## Key Files in Outputs

```
operators__init__.py              → beigebox/operators/__init__.py
workspace.py                      → beigebox/operators/workspace.py
operator.py                       → beigebox/agents/operator.py
system_info.py                    → beigebox/tools/system_info.py
config.yaml                       → config.yaml (root)

operator-workspace-implementation.md  ← Full deployment guide
operator-workspace-design.md          ← Architecture & security
```

---

## Environment Variables Available in Workspace

```bash
# Operator can use these in scripts:
$OPERATOR_WORKSPACE    # Path to extracted ZIP (/tmp/operator_env)
$PATH                  # Updated to include $OPERATOR_WORKSPACE/bin:...
```

Example in script:
```bash
#!/bin/bash
echo "Workspace is at: $OPERATOR_WORKSPACE"
ls $OPERATOR_WORKSPACE/data/
```

---

## Security Checklist

✅ ZIP size limited (prevents zip bombs)  
✅ Runs in bwrap/busybox sandbox  
✅ Allowlist still enforced  
✅ All shell calls audited  
✅ You control ZIP contents  
✅ Operator disabled by default  

---

## Next: Example Workspace

Want a ready-made `operator-tools.zip`? Create:

```bash
# GPU monitoring
mkdir -p gpu-tools/bin
cat > gpu-tools/bin/check-gpu.py << 'EOF'
#!/usr/bin/env python3
import subprocess
r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used"], 
                   capture_output=True, text=True)
print(r.stdout)
EOF

chmod +x gpu-tools/bin/check-gpu.py
cd gpu-tools && zip -r ../gpu-tools.zip . && cd ..

# Use it
echo "zip_path: ./gpu-tools.zip" >> config.yaml
```

Then ask operator: "Check GPU status"

---

## Questions?

Check the full docs:
- `operator-workspace-implementation.md` ← Start here
- `operator-workspace-design.md` ← Deep dive

Or grep logs:
```bash
docker logs beigebox | grep -i workspace
```
