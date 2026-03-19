"""
System metrics collection — CPU, RAM, GPU.
All collection is best-effort; missing hardware or libraries produce None/[] rather than raising.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL: float = 1.0  # seconds


def _collect_cpu() -> dict[str, Any]:
    """Collect CPU utilization and temperature."""
    try:
        import psutil

        pct = psutil.cpu_percent(interval=0.1)
        # sensors_temperatures() is Linux-only; gracefully absent on macOS/Windows
        temp = None
        temps = getattr(psutil, "sensors_temperatures", lambda: {})()
        for key in ("coretemp", "k10temp", "acpitz", "acpi", "cpu_thermal"):
            entries = temps.get(key, [])
            if entries:
                temp = round(entries[0].current, 1)
                break
        return {"cpu_percent": round(pct, 1), "cpu_temp_c": temp}
    except Exception as e:
        logger.debug("CPU metrics failed: %s", e)
        return {"cpu_percent": None, "cpu_temp_c": None}


def _collect_ram() -> dict[str, Any]:
    """Collect system RAM utilization."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "ram_percent": round(vm.percent, 1),
            "ram_used_mb": vm.used // (1024 * 1024),
            "ram_total_mb": vm.total // (1024 * 1024),
        }
    except Exception as e:
        logger.debug("RAM metrics failed: %s", e)
        return {"ram_percent": None, "ram_used_mb": None, "ram_total_mb": None}


def _collect_gpus() -> list[dict[str, Any]]:
    """Collect NVIDIA GPU metrics (utilization, memory, temperature)."""
    gpus = []
    try:
        import pynvml

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(
                    h, pynvml.NVML_TEMPERATURE_GPU
                )
            except Exception:
                temp = None
            gpus.append(
                {
                    "id": i,
                    "name": name,
                    "load_percent": round(util.gpu, 1),
                    "memory_used_mb": mem.used // (1024 * 1024),
                    "memory_total_mb": mem.total // (1024 * 1024),
                    "temp_c": temp,
                }
            )
    except ImportError:
        pass  # pynvml not installed — no NVIDIA GPU data
    except Exception as e:
        logger.debug("GPU metrics failed: %s", e)
    return gpus


def collect_system_metrics() -> dict[str, Any]:
    """
    Collect all system metrics synchronously.
    Cached for _CACHE_TTL seconds to avoid repeated expensive calls.
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    result = {
        **_collect_cpu(),
        **_collect_ram(),
        "gpus": _collect_gpus(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _cache = result
    _cache_ts = now
    return result


async def collect_system_metrics_async() -> dict[str, Any]:
    """
    Run collect_system_metrics() in a thread pool to avoid blocking the event loop.
    psutil.cpu_percent(interval=0.1) does a 100ms blocking sleep.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, collect_system_metrics)
