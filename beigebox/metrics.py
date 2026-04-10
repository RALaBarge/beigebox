"""
System metrics collection — CPU, RAM, GPU.
All collection is best-effort; missing hardware or libraries produce None/[] rather than raising.

Host-bridge mode (macOS): when running BeigeBox inside Docker on macOS, in-container
psutil reports the Linux VM's stats, not the host Mac's. Set the env var
BEIGEBOX_HOST_METRICS_URL (or `metrics.host_metrics_url` in config.yaml) to a tiny
host-side helper (~/.beigebox/host_metrics.py) and the collector will fetch real
host stats from there instead. The helper runs as a LaunchAgent on the host.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.request
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL: float = 1.0  # seconds

# Set once at first use; populated from env var or config.
_host_metrics_url: str | None = None
_host_metrics_resolved: bool = False
_host_metrics_failures: int = 0
_HOST_METRICS_FAIL_THRESHOLD = 3  # back off after this many consecutive failures


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


def _get_host_metrics_url() -> str | None:
    """
    Resolve the host-bridge URL once. Order of precedence:
      1. BEIGEBOX_HOST_METRICS_URL env var
      2. config.yaml -> metrics.host_metrics_url
    Returns None if neither is set, or after we've tripped the failure threshold.
    """
    global _host_metrics_url, _host_metrics_resolved
    if _host_metrics_failures >= _HOST_METRICS_FAIL_THRESHOLD:
        return None
    if _host_metrics_resolved:
        return _host_metrics_url
    url = os.environ.get("BEIGEBOX_HOST_METRICS_URL", "").strip()
    if not url:
        try:
            from beigebox.config import get_config
            cfg = get_config()
            url = (cfg.get("metrics", {}) or {}).get("host_metrics_url", "").strip()
        except Exception:
            url = ""
    _host_metrics_url = url or None
    _host_metrics_resolved = True
    if _host_metrics_url:
        logger.info("metrics: using host bridge at %s", _host_metrics_url)
    return _host_metrics_url


def _collect_via_host_bridge(url: str) -> dict[str, Any] | None:
    """Fetch real host stats from the host-side metrics helper. Returns None on any failure."""
    global _host_metrics_failures
    try:
        with urllib.request.urlopen(url, timeout=4.0) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
            _host_metrics_failures = 0
            # Normalize: ensure all expected keys exist so the UI never sees None where it expects a number.
            data.setdefault("cpu_percent", None)
            data.setdefault("cpu_temp_c", None)
            data.setdefault("ram_percent", None)
            data.setdefault("ram_used_mb", None)
            data.setdefault("ram_total_mb", None)
            data.setdefault("gpus", [])
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
            return data
    except Exception as e:
        _host_metrics_failures += 1
        logger.debug("host metrics bridge fetch failed (attempt %d): %s",
                     _host_metrics_failures, e)
        if _host_metrics_failures >= _HOST_METRICS_FAIL_THRESHOLD:
            logger.warning("host metrics bridge unreachable after %d attempts; falling back to in-container psutil",
                           _HOST_METRICS_FAIL_THRESHOLD)
        return None


def collect_system_metrics() -> dict[str, Any]:
    """
    Collect all system metrics synchronously.
    Cached for _CACHE_TTL seconds to avoid repeated expensive calls.

    On macOS-in-Docker, prefers a host-side bridge (see module docstring) since
    in-container psutil only sees the Linux VM, not the real Mac.
    """
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    bridge_url = _get_host_metrics_url()
    if bridge_url:
        bridged = _collect_via_host_bridge(bridge_url)
        if bridged is not None:
            _cache = bridged
            _cache_ts = now
            return bridged
        # bridge failure — fall through to in-container psutil

    result = {
        **_collect_cpu(),
        **_collect_ram(),
        "gpus": _collect_gpus(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "in-container psutil",
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
