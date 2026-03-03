# Agent Workspace

Persistent shared directory between you and sandboxed agents.

## Convention

| Directory      | Who reads | Who writes | Inside sandbox    |
|----------------|-----------|------------|-------------------|
| `workspace/in/`  | Agents    | You        | `/workspace/in`  (read-only)  |
| `workspace/out/` | You       | Agents     | `/workspace/out` (read-write) |

## Usage

**Drop a file for an agent:**
```
cp mydata.zip workspace/in/
```
Agent can then: `ls /workspace/in`, `cat /workspace/in/mydata.zip`, etc.

**Retrieve agent output:**
```
ls workspace/out/
cat workspace/out/result.txt
```

## Notes

- Files in `in/` persist across container restarts (host bind mount)
- Agents cannot delete or overwrite files in `in/` (read-only inside sandbox)
- `out/` is writable by agents — treat it like a shared output tray
- Neither directory has network access (bwrap isolates that separately)
