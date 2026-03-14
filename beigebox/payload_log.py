"""
PayloadLog — full outbound context logger for debugging.

Writes one JSONL line per LLM call containing the complete payload sent to the
backend (model, full messages array, temperature, options, everything) and the
response received. Useful for inspecting exactly what context is being passed
at each step without guessing from truncated wiretap entries.

Enable hot-reload in runtime_config.yaml:
    runtime:
      payload_log_enabled: true

Configure path in config.yaml:
    payload_log:
      path: ./data/payload.jsonl
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


class PayloadLog:
    """
    Append-only JSONL log of full LLM payloads.

    Each record:
        ts              ISO-8601 timestamp
        source          "proxy" | "proxy_stream" | "operator"
        backend         backend URL or name
        model           model string
        conversation_id for correlation with wiretap
        payload         complete outbound body (messages, options, etc.)
        response        assembled response content (str)
        latency_ms      wall-clock time for the backend call
    """

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._file = None

    def _ensure_open(self):
        if self._file is None:
            self._file = open(self._path, "a", buffering=1)

    def log(
        self,
        *,
        source: str,
        payload: dict,
        response: str | dict | None = None,
        backend: str = "",
        model: str = "",
        conversation_id: str = "",
        latency_ms: float = 0.0,
        extra: dict | None = None,
    ) -> None:
        """Write one payload record. Never raises — swallows errors silently."""
        try:
            record: dict = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "backend": backend,
                "model": model,
                "conversation_id": conversation_id,
                "latency_ms": round(latency_ms, 1),
                "payload": payload,
            }
            if response is not None:
                record["response"] = response
            if extra:
                record.update(extra)

            with self._lock:
                self._ensure_open()
                self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("PayloadLog write failed: %s", exc)

    def close(self) -> None:
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None


# ── Module-level singleton ─────────────────────────────────────────────────
# Created once from config at import time. Hot-toggle is done by the caller
# checking runtime_config before calling .log() — no restart needed.

_instance: PayloadLog | None = None


def get_payload_log(cfg: dict | None = None) -> PayloadLog:
    """Return (or create) the module-level PayloadLog singleton."""
    global _instance
    if _instance is None:
        path = "./data/payload.jsonl"
        if cfg is not None:
            path = cfg.get("payload_log", {}).get("path", path)
        _instance = PayloadLog(path)
    return _instance
