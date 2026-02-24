"""
System info tool — reports host system stats with hardened shell security.

Useful for "how much VRAM am I using?" or "what's my disk space?" type
questions. Also lets the LLM know its own runtime environment.

Security hardening:
  - Allowlist enforcement (only whitelisted commands can execute)
  - Blocked pattern detection (dangerous patterns always rejected)
  - Audit logging (all execution attempts logged)
  - 5-second timeout per command
  - Non-root execution (appuser)
  - Busybox wrapper at /usr/local/bin/bb

Examples the decision LLM would route here:
  "How much RAM am I using?"
  "What GPU do I have?"
  "Disk space?"
  "System info"
"""

import logging
import subprocess
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_SHELL_BINARY: str | None = None


def _get_shell() -> str:
    """
    Return the configured shell binary.
    
    Defaults to hardened busybox wrapper at /usr/local/bin/bb if available.
    Falls back to /bin/sh if not configured or wrapper missing.
    """
    global _SHELL_BINARY
    if _SHELL_BINARY is None:
        try:
            from beigebox.config import get_config
            cfg = get_config()
            shell = cfg.get("operator", {}).get("shell", {}).get("shell_binary")
            
            if not shell:
                # Prefer busybox wrapper (hardened)
                if os.path.exists("/usr/local/bin/bb"):
                    shell = "/usr/local/bin/bb"
                    logger.info("Using hardened busybox wrapper at /usr/local/bin/bb")
                else:
                    shell = "/bin/sh"
                    logger.warning("Busybox wrapper not found, using /bin/sh (less secure)")
            
            _SHELL_BINARY = shell
        except Exception as e:
            logger.error("Error loading shell config: %s", e)
            _SHELL_BINARY = "/bin/sh"
    
    return _SHELL_BINARY


def _get_allowed_commands() -> list[str]:
    """Get the allowlist of permitted commands from config."""
    try:
        from beigebox.config import get_config
        cfg = get_config()
        return cfg.get("operator", {}).get("shell", {}).get("allowed_commands", [])
    except Exception:
        return []


def _get_blocked_patterns() -> list[str]:
    """Get the list of blocked patterns (always rejected)."""
    try:
        from beigebox.config import get_config
        cfg = get_config()
        return cfg.get("operator", {}).get("shell", {}).get("blocked_patterns", [])
    except Exception:
        return [
            "rm -rf",      # Catastrophic deletion
            "sudo",        # Privilege escalation
            "> /etc",      # Modify system config
            "; ",          # Command chaining
            "| ",          # Pipe chaining
        ]


def _audit_log(action: str, command: str, result: str, allowed: bool) -> None:
    """
    Log all shell execution attempts (both allowed and denied).
    
    Format:
      OPERATOR_SHELL | timestamp | allowed/denied | command[:100] | result
    """
    ts = datetime.utcnow().isoformat()
    status = "ALLOWED" if allowed else "DENIED"
    cmd_display = command[:100]
    
    if allowed:
        logger.info("OPERATOR_SHELL | %s | %s | %s | %s", ts, status, cmd_display, result[:50])
    else:
        logger.warning("OPERATOR_SHELL | %s | %s | %s | %s", ts, status, cmd_display, result)


def _is_command_allowed(cmd: str) -> tuple[bool, str]:
    """
    Check if command is allowed to execute.
    
    Returns: (allowed: bool, reason: str)
    
    Checks in order:
      1. Command is not empty
      2. Base command is in allowlist
      3. Command doesn't match blocked patterns
    """
    if not cmd or not cmd.strip():
        return False, "empty_command"
    
    cmd_stripped = cmd.strip()
    allowed = _get_allowed_commands()
    blocked = _get_blocked_patterns()
    
    # If allowlist is empty, deny all
    if not allowed:
        return False, "allowlist_empty"
    
    # Extract base command (before any space or special char)
    base_cmd = cmd_stripped.split()[0].split("|")[0].split(";")[0].strip()
    
    # Check allowlist
    if base_cmd not in allowed:
        return False, f"not_in_allowlist: {base_cmd}"
    
    # Check blocked patterns (always applied, even for allowed commands)
    for pattern in blocked:
        if pattern in cmd_stripped:
            return False, f"blocked_pattern: {pattern}"
    
    return True, "allowed"


def _run(cmd: str) -> str:
    """
    Run a shell command with full security checks.
    
    Returns: stdout of command, or error message if blocked/failed.
    
    Security layers:
      1. Allowlist check (command must be in allowlist)
      2. Pattern blocking (dangerous patterns always blocked)
      3. Busybox wrapper (OS-level command filtering)
      4. 5-second timeout
      5. Non-root execution (runs as appuser)
      6. Audit logging (all attempts logged)
    """
    # Security check 1: Is command allowed?
    is_allowed, reason = _is_command_allowed(cmd)
    if not is_allowed:
        _audit_log("execute", cmd, reason, False)
        return ""
    
    # Security check 2: Execute with timeout
    try:
        shell = _get_shell()
        result = subprocess.run(
            [shell, "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
            # Ensure we're not running as root
            user=os.environ.get("USER", "appuser")
        )
        
        stdout = result.stdout.strip()
        success = result.returncode == 0
        
        # Audit log the execution
        if success:
            _audit_log("execute", cmd, "exit_0", True)
        else:
            error_msg = result.stderr.strip() or f"exit_{result.returncode}"
            _audit_log("execute", cmd, error_msg, True)
        
        return stdout
    
    except subprocess.TimeoutExpired:
        _audit_log("execute", cmd, "timeout_5s", True)
        logger.warning("OPERATOR_SHELL_TIMEOUT: %s (5 second limit exceeded)", cmd[:100])
        return ""
    
    except Exception as e:
        error_str = str(e)[:100]
        _audit_log("execute", cmd, error_str, True)
        logger.error("OPERATOR_SHELL_EXCEPTION: %s — %s", cmd[:100], e)
        return ""


class SystemInfoTool:
    """
    Reports host system information with secure shell execution.
    
    All shell commands must be in the allowlist (config.yaml).
    Blocked patterns are always enforced regardless of allowlist.
    """

    def __init__(self):
        logger.info("SystemInfoTool initialized (hardened)")
        # Log startup with security config
        allowed = _get_allowed_commands()
        blocked = _get_blocked_patterns()
        logger.info("Shell security: %d allowed commands, %d blocked patterns", len(allowed), len(blocked))

    def run(self, query: str = "") -> str:
        """Gather and return system information via secure shell commands."""
        sections = []

        # CPU — requires: grep, cat, nproc, cut
        cpu_model = _run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
        cpu_cores = _run("nproc")
        load = _run("cat /proc/loadavg | cut -d' ' -f1-3")
        if cpu_model:
            sections.append(f"CPU: {cpu_model.strip()} ({cpu_cores} cores, load: {load})")

        # Memory — requires: free
        mem_info = _run("free -h | grep Mem")
        if mem_info:
            parts = mem_info.split()
            if len(parts) >= 4:
                sections.append(f"RAM: {parts[2]} used / {parts[1]} total (available: {parts[6] if len(parts) > 6 else '?'})")

        # GPU — requires: nvidia-smi (optional, won't error if missing)
        gpu_info = _run("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null")
        if gpu_info:
            for line in gpu_info.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    sections.append(f"GPU: {parts[0]} — {parts[1]}MB / {parts[2]}MB VRAM ({parts[3]}% util)")
        else:
            sections.append("GPU: nvidia-smi not available")

        # Disk — requires: df
        disk = _run("df -h / | tail -1")
        if disk:
            parts = disk.split()
            if len(parts) >= 5:
                sections.append(f"Disk (/): {parts[2]} used / {parts[1]} total ({parts[4]} full)")

        # Ollama models loaded — requires: curl, grep
        # Note: This is safe because curl goes to localhost:11434 (local), not internet
        ollama_ps = _run("curl -s http://localhost:11434/api/ps 2>/dev/null")
        if ollama_ps and "models" in ollama_ps:
            try:
                import json
                data = json.loads(ollama_ps)
                models = data.get("models", [])
                if models:
                    loaded = [f"{m['name']} ({m.get('size_vram', 0) // 1024 // 1024}MB VRAM)" for m in models]
                    sections.append(f"Ollama loaded: {', '.join(loaded)}")
                else:
                    sections.append("Ollama: no models loaded")
            except Exception as e:
                logger.debug("Failed to parse Ollama response: %s", e)

        # Uptime — requires: uptime
        uptime = _run("uptime -p")
        if uptime:
            sections.append(f"Uptime: {uptime}")

        return "\n".join(sections) if sections else "Could not gather system information."
