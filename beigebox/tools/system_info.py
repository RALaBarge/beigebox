"""
System info tool — reports host system stats with hardened shell security.

Execution model (layered defense):

  Layer 1 — Python allowlist   : only whitelisted base commands pass at all
  Layer 2 — bwrap sandbox      : if bubblewrap is available, every command
                                  runs in a namespaced sandbox:
                                    • no network
                                    • no /app, no /home (data & code invisible)
                                    • /proc and /sys read-only
                                    • tmpfs scratch only
                                    • dies with parent process
  Layer 3 — busybox fallback   : if bwrap is unavailable (kernel restriction,
                                  not installed), the busybox wrapper + non-root
                                  user is the hard wall instead
  Layer 4 — audit logging      : every attempt logged regardless of outcome

Ollama model query uses httpx directly — no shell, no network namespace needed.

GPU note: nvidia-smi needs real /dev device nodes. A separate bwrap profile
binds the host /dev read-write for that one query only.
"""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from functools import lru_cache

logger = logging.getLogger(__name__)


# ── bwrap availability probe ──────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _bwrap_available() -> bool:
    """
    Return True if bubblewrap is installed AND user namespaces work.
    Result is cached after the first call.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        logger.info("bwrap not found — falling back to busybox wrapper")
        return False
    try:
        # Minimal smoke test: launch a sandboxed true(1)
        subprocess.run(
            [
                bwrap,
                "--die-with-parent",
                "--unshare-all",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind-try", "/bin", "/bin",
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--chdir", "/tmp",
                "/bin/true",
            ],
            check=True,
            capture_output=True,
            timeout=5,
        )
        logger.info("bwrap sandbox available and functional")
        return True
    except Exception as e:
        logger.warning(
            "bwrap probe failed (%s) — falling back to busybox wrapper. "
            "Check kernel.unprivileged_userns_clone if unexpected.", e
        )
        return False


def _bwrap_argv(gpu: bool = False) -> list[str]:
    """
    Build the base bwrap argument list for a sandboxed command.

    Standard profile:  minimal devtmpfs  → good for most commands.
    GPU profile:       host /dev bound   → required for nvidia-smi device nodes.
    Neither profile mounts /app, /home, or any writable persistent path.
    """
    bwrap = shutil.which("bwrap") or "bwrap"
    args = [
        bwrap,
        "--die-with-parent",   # sandbox dies if beigebox dies
        "--unshare-all",       # fresh pid/net/ipc/uts/user namespaces
        "--new-session",       # detach from terminal
        # ── read-only runtime ────────────────────────────────────────────
        "--ro-bind",     "/usr", "/usr",
        "--ro-bind-try", "/bin", "/bin",
        "--ro-bind-try", "/lib", "/lib",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/lib32", "/lib32",
        # ── kernel virtual filesystems ───────────────────────────────────
        "--proc", "/proc",          # /proc/cpuinfo, /proc/loadavg
        "--ro-bind-try", "/sys", "/sys",   # nvidia-smi sysfs queries
        # ── scratch only ─────────────────────────────────────────────────
        "--tmpfs", "/tmp",
        "--chdir", "/tmp",
        # ── explicitly NOT mounting: /app /home /root /etc (writable) ───
    ]
    if gpu:
        # Bind real host /dev so GPU character devices (nvidia0, nvidiactl,
        # nvidia-uvm) are accessible. Network is still isolated via
        # --unshare-all above.
        args += ["--dev-bind", "/dev", "/dev"]
    else:
        args += ["--dev", "/dev"]   # minimal devtmpfs only

    return args


# ── Config helpers ────────────────────────────────────────────────────────────

def _get_shell() -> str:
    """Return the configured fallback shell binary."""
    try:
        from beigebox.config import get_config
        cfg = get_config()
        shell = cfg.get("operator", {}).get("shell", {}).get("shell_binary", "")
        if shell:
            return shell
    except Exception:
        pass
    if os.path.exists("/usr/local/bin/bb"):
        return "/usr/local/bin/bb"
    return "/bin/sh"


def _get_allowed_commands() -> list[str]:
    try:
        from beigebox.config import get_config
        cfg = get_config()
        return cfg.get("operator", {}).get("shell", {}).get("allowed_commands", [])
    except Exception:
        return []


def _get_blocked_patterns() -> list[str]:
    try:
        from beigebox.config import get_config
        cfg = get_config()
        return cfg.get("operator", {}).get("shell", {}).get("blocked_patterns", [])
    except Exception:
        return ["rm -rf", "sudo", "> /etc", "; ", "| "]


# ── Allowlist enforcement ─────────────────────────────────────────────────────

def _is_command_allowed(cmd: str) -> tuple[bool, str]:
    if not cmd or not cmd.strip():
        return False, "empty_command"

    cmd_stripped = cmd.strip()
    allowed  = _get_allowed_commands()
    blocked  = _get_blocked_patterns()

    if not allowed:
        return False, "allowlist_empty"

    base_cmd = cmd_stripped.split()[0].split("|")[0].split(";")[0].strip()

    if base_cmd not in allowed:
        return False, f"not_in_allowlist: {base_cmd}"

    for pattern in blocked:
        if pattern in cmd_stripped:
            return False, f"blocked_pattern: {pattern}"

    return True, "allowed"


# ── Audit logging ─────────────────────────────────────────────────────────────

def _audit_log(command: str, result: str, allowed: bool) -> None:
    ts     = datetime.now(timezone.utc).isoformat()
    status = "ALLOWED" if allowed else "DENIED"
    if allowed:
        logger.info("SYSINFO_SHELL | %s | %s | %s | %s", ts, status, command[:100], result[:50])
    else:
        logger.warning("SYSINFO_SHELL | %s | %s | %s | %s", ts, status, command[:100], result)


# ── Execution ─────────────────────────────────────────────────────────────────

def _run(cmd: str, gpu: bool = False, workspace_env: dict = None) -> str:
    """
    Run a shell command through the security stack.

    Path A — bwrap available:
        allowlist check → namespaced sandbox → audit log
    Path B — bwrap unavailable:
        allowlist check → busybox wrapper → audit log

    Args:
        cmd:  Shell command string. Must pass the allowlist.
        gpu:  If True, use the GPU bwrap profile (binds host /dev).
        workspace_env: Optional dict of env vars to inject (e.g., PATH with workspace/bin).
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
            # busybox wrapper expects:  bb sh -c <cmd>
            if os.path.basename(shell) == "bb":
                argv = [shell, "sh", "-c", cmd]
            else:
                argv = [shell, "-c", cmd]

        # Prepare environment: start with parent's env, inject workspace updates
        env = os.environ.copy()
        if workspace_env:
            env.update(workspace_env)

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            # Note: do NOT pass user= here — setuid requires root.
            # We're already running as non-root appuser.
        )

        stdout = result.stdout.strip()
        if result.returncode == 0:
            _audit_log(cmd, "exit_0", True)
        else:
            _audit_log(cmd, result.stderr.strip() or f"exit_{result.returncode}", True)

        return stdout

    except subprocess.TimeoutExpired:
        _audit_log(cmd, "timeout_5s", True)
        logger.warning("SYSINFO_TIMEOUT: %s", cmd[:100])
        return ""
    except Exception as e:
        _audit_log(cmd, str(e)[:100], True)
        logger.error("SYSINFO_EXCEPTION: %s — %s", cmd[:100], e)
        return ""


# ── Ollama query (no shell) ───────────────────────────────────────────────────

def _query_ollama_models() -> str | None:
    """
    Query Ollama for loaded models via httpx.
    No shell command, no network namespace dependency.
    Returns a formatted string or None on failure.
    """
    try:
        from beigebox.config import get_config
        cfg = get_config()
        base_url = cfg.get("backend", {}).get("url", "http://localhost:11434").rstrip("/")
    except Exception:
        base_url = "http://localhost:11434"

    try:
        import httpx
        resp = httpx.get(f"{base_url}/api/ps", timeout=5.0)
        resp.raise_for_status()
        data   = resp.json()
        models = data.get("models", [])
        if not models:
            return "Ollama: no models loaded"
        loaded = [
            f"{m['name']} ({m.get('size_vram', 0) // 1024 // 1024}MB VRAM)"
            for m in models
        ]
        return f"Ollama loaded: {', '.join(loaded)}"
    except Exception as e:
        logger.debug("Ollama query failed: %s", e)
        return None


# ── Tool class ────────────────────────────────────────────────────────────────

class SystemInfoTool:
    """
    Reports host system information through the layered security stack.

    bwrap sandbox → busybox fallback → Python allowlist → audit log.
    Ollama model list is fetched via httpx (no shell, no network namespace).
    """

    def __init__(self):
        # Probe bwrap eagerly so the first real call isn't slow
        available = _bwrap_available()
        mode = "bwrap sandbox" if available else "busybox fallback"
        logger.info("SystemInfoTool initialised (execution mode: %s)", mode)
        allowed = _get_allowed_commands()
        logger.info("Shell allowlist: %d commands", len(allowed))

    def run(self, query: str = "", workspace_env: dict = None) -> str:
        """Gather and return system information.
        
        Args:
            query: User query (unused, for interface compatibility)
            workspace_env: Optional env dict to inject into shell (e.g., workspace PATH)
        """
        sections = []

        # CPU
        cpu_model = _run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2", workspace_env=workspace_env)
        cpu_cores = _run("nproc", workspace_env=workspace_env)
        load      = _run("cat /proc/loadavg | cut -d' ' -f1-3", workspace_env=workspace_env)
        if cpu_model:
            sections.append(
                f"CPU: {cpu_model.strip()} ({cpu_cores} cores, load: {load})"
            )

        # Memory
        mem_info = _run("free -h | grep Mem", workspace_env=workspace_env)
        if mem_info:
            parts = mem_info.split()
            if len(parts) >= 4:
                avail = parts[6] if len(parts) > 6 else "?"
                sections.append(
                    f"RAM: {parts[2]} used / {parts[1]} total (available: {avail})"
                )

        # GPU — uses the gpu bwrap profile (needs real /dev device nodes)
        gpu_info = _run(
            "nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu"
            " --format=csv,noheader,nounits 2>/dev/null",
            gpu=True,
            workspace_env=workspace_env,
        )
        if gpu_info:
            for line in gpu_info.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    sections.append(
                        f"GPU: {parts[0]} — {parts[1]}MB / {parts[2]}MB VRAM"
                        f" ({parts[3]}% util)"
                    )
        else:
            sections.append("GPU: nvidia-smi not available")

        # Disk
        disk = _run("df -h / | tail -1", workspace_env=workspace_env)
        if disk:
            parts = disk.split()
            if len(parts) >= 5:
                sections.append(
                    f"Disk (/): {parts[2]} used / {parts[1]} total ({parts[4]} full)"
                )

        # Ollama — httpx, not shell
        ollama = _query_ollama_models()
        if ollama:
            sections.append(ollama)

        # Uptime
        uptime = _run("uptime -p", workspace_env=workspace_env)
        if uptime:
            sections.append(f"Uptime: {uptime}")

        return "\n".join(sections) if sections else "Could not gather system information."
