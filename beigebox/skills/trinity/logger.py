"""Trinity security audit pipeline logging module."""
from __future__ import annotations

import datetime
import json
import sys
import traceback
from dataclasses import dataclass
from enum import IntEnum
from threading import Lock
from typing import Any, Optional


class TrinityLogLevel(IntEnum):
    """Log level enumeration."""
    OFF = 0
    INFO = 1
    DEBUG = 2
    TRACE = 3


@dataclass
class TrinityLogConfig:
    """Configuration for Trinity logging."""
    enabled: bool = False
    level: TrinityLogLevel = TrinityLogLevel.DEBUG
    log_prompts: bool = False
    log_responses: bool = False
    log_to_file: Optional[str] = None


class TrinityLogger:
    """Thread-safe structured logger for Trinity security audit pipeline.

    All methods are no-ops when config.enabled=False — zero overhead in production.
    Output is JSON lines (jsonl) for easy grep/jq analysis.

    Usage:
        from beigebox.skills.trinity.logger import TrinityLogger, TrinityLogConfig, TrinityLogLevel

        log = TrinityLogger("audit-abc123", TrinityLogConfig(
            enabled=True,
            level=TrinityLogLevel.DEBUG,
            log_responses=True,
            log_to_file="./data/trinity_debug.jsonl",
        ))
    """

    def __init__(self, audit_id: str, config: TrinityLogConfig) -> None:
        self.audit_id = audit_id
        self.config = config
        self._file_lock = Lock()

    def _should_log(self, level: TrinityLogLevel) -> bool:
        if not self.config.enabled:
            return False
        return level <= self.config.level

    def _emit(self, level: TrinityLogLevel, phase: str, msg: str, **ctx: Any) -> None:
        """Emit a structured log entry to stderr and optionally to file."""
        if not self._should_log(level):
            return

        log_entry: dict = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "audit_id": self.audit_id,
            "level": level.name,
            "phase": phase,
            "msg": msg,
        }
        log_entry.update(ctx)

        try:
            log_line = json.dumps(log_entry, default=str, separators=(",", ":"))
        except Exception:
            log_entry = {k: v for k, v in log_entry.items() if k in ("ts", "audit_id", "level", "phase", "msg")}
            log_entry["ctx_error"] = "non-serializable context omitted"
            log_line = json.dumps(log_entry, separators=(",", ":"))

        sys.stderr.write(log_line + "\n")
        sys.stderr.flush()

        if self.config.log_to_file:
            with self._file_lock:
                try:
                    with open(self.config.log_to_file, "a", encoding="utf-8") as f:
                        f.write(log_line + "\n")
                except OSError:
                    pass  # never crash the pipeline due to logging issues

    # ── Public API ────────────────────────────────────────────────────────────

    def info(self, msg: str, phase: str = "", **ctx: Any) -> None:
        self._emit(TrinityLogLevel.INFO, phase, msg, **ctx)

    def debug(self, msg: str, phase: str = "", **ctx: Any) -> None:
        self._emit(TrinityLogLevel.DEBUG, phase, msg, **ctx)

    def trace(self, msg: str, phase: str = "", **ctx: Any) -> None:
        self._emit(TrinityLogLevel.TRACE, phase, msg, **ctx)

    def warn(self, msg: str, phase: str = "", **ctx: Any) -> None:
        self._emit(TrinityLogLevel.INFO, phase, f"[WARN] {msg}", **ctx)

    def error(self, msg: str, phase: str = "", exc: Optional[BaseException] = None, **ctx: Any) -> None:
        if exc is not None:
            ctx["traceback"] = traceback.format_exc()
        self._emit(TrinityLogLevel.INFO, phase, f"[ERROR] {msg}", **ctx)

    def phase_banner(self, name: str) -> None:
        self.info(msg=f"=== PHASE: {name} ===")

    def llm_request(self, model_key: str, prompt: str, tokens_est: int, phase: str = "", **ctx: Any) -> None:
        prompt_val = prompt if self.config.log_prompts else prompt[:120] + ("..." if len(prompt) > 120 else "")
        self._emit(TrinityLogLevel.DEBUG, phase, "llm_request",
                   model_key=model_key, tokens_est=tokens_est, prompt=prompt_val, **ctx)

    def llm_response(self, model_key: str, content: str, tokens_used: int, phase: str = "", **ctx: Any) -> None:
        if tokens_used == 0:
            self._emit(TrinityLogLevel.INFO, phase, "[WARN] llm_response tokens_used=0 — model may not have generated tokens",
                       model_key=model_key, content_length=len(content))
        content_val = content if self.config.log_responses else content[:120] + ("..." if len(content) > 120 else "")
        self._emit(TrinityLogLevel.DEBUG, phase, "llm_response",
                   model_key=model_key, tokens_used=tokens_used, content=content_val, **ctx)

    def parse_fail(self, location: str, raw_content: str, exc: BaseException, phase: str = "") -> None:
        self._emit(TrinityLogLevel.INFO, phase, "[WARN] parse_fail",
                   location=location,
                   raw_content=raw_content[:300] + ("..." if len(raw_content) > 300 else ""),
                   exc_type=type(exc).__name__,
                   exc_msg=str(exc))

    def empty_result(self, location: str, reason: str, phase: str = "") -> None:
        self._emit(TrinityLogLevel.INFO, phase, "[WARN] empty_result",
                   location=location, reason=reason)

    def finding_extracted(self, finding_id: str, title: str, severity: str, model: str, phase: str = "") -> None:
        self._emit(TrinityLogLevel.INFO, phase, "finding_extracted",
                   finding_id=finding_id, title=title, severity=severity, model=model)
