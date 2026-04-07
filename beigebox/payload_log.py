"""
PayloadLog — full outbound context logger.

Writes one JSONL line per LLM call with the complete payload sent to the
backend (model, messages, options) and the assembled response. Only active
when payload_log_enabled: true in runtime_config.yaml.

This module is a plain file writer, not a hot-path component. It is called
exclusively through log_payload_event() in beigebox.logging, which handles
the gate check and the Tap bus summary event before delegating here.

Enable:   runtime_config.yaml  →  payload_log_enabled: true
Path:     config.yaml          →  payload_log.path  (default ./data/payload.jsonl)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_lock = Lock()
_file = None
_path: str = "./data/payload.jsonl"


def configure(path: str) -> None:
    """Set the output path. Called once at startup."""
    global _path
    _path = path


def write_payload(
    *,
    source: str,
    payload: dict | None = None,
    response: str | None = None,
    model: str = "",
    backend: str = "",
    conversation_id: str = "",
    latency_ms: float = 0.0,
) -> None:
    """Append one record to payload.jsonl. Never raises."""
    global _file
    try:
        record: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "backend": backend,
            "model": model,
            "conversation_id": conversation_id,
            "latency_ms": round(latency_ms, 1),
        }
        if payload:
            record["payload"] = payload
        if response is not None:
            record["response"] = response
        with _lock:
            if _file is None:
                Path(_path).parent.mkdir(parents=True, exist_ok=True)
                _file = open(_path, "a", buffering=1)
            _file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("payload_log write failed: %s", exc)


def close() -> None:
    """Flush and close the log file. Called at server shutdown."""
    global _file
    with _lock:
        if _file:
            try:
                _file.close()
            except Exception:
                pass
            _file = None
