"""
System info tool — reports host system stats.

Useful for "how much VRAM am I using?" or "what's my disk space?" type
questions. Also lets the LLM know its own runtime environment.

Examples the decision LLM would route here:
  "How much RAM am I using?"
  "What GPU do I have?"
  "Disk space?"
  "System info"
"""

import logging
import subprocess
import os

logger = logging.getLogger(__name__)

_SHELL_BINARY: str | None = None


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


class SystemInfoTool:
    """Reports host system information."""

    def __init__(self):
        logger.info("SystemInfoTool initialized")

    def run(self, query: str = "") -> str:
        """Gather and return system information."""
        sections = []

        # CPU
        cpu_model = _run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
        cpu_cores = _run("nproc")
        load = _run("cat /proc/loadavg | cut -d' ' -f1-3")
        if cpu_model:
            sections.append(f"CPU: {cpu_model.strip()} ({cpu_cores} cores, load: {load})")

        # Memory
        mem_info = _run("free -h | grep Mem")
        if mem_info:
            parts = mem_info.split()
            if len(parts) >= 4:
                sections.append(f"RAM: {parts[2]} used / {parts[1]} total (available: {parts[6] if len(parts) > 6 else '?'})")

        # GPU (nvidia-smi)
        gpu_info = _run("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null")
        if gpu_info:
            for line in gpu_info.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    sections.append(f"GPU: {parts[0]} — {parts[1]}MB / {parts[2]}MB VRAM ({parts[3]}% util)")
        else:
            sections.append("GPU: nvidia-smi not available")

        # Disk
        disk = _run("df -h / | tail -1")
        if disk:
            parts = disk.split()
            if len(parts) >= 5:
                sections.append(f"Disk (/): {parts[2]} used / {parts[1]} total ({parts[4]} full)")

        # Ollama models loaded
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
            except Exception:
                pass

        # Uptime
        uptime = _run("uptime -p")
        if uptime:
            sections.append(f"Uptime: {uptime}")

        return "\n".join(sections) if sections else "Could not gather system information."
