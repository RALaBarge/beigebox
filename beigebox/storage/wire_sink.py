"""Wire event sink ABC and built-in implementations."""

import abc
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WireSink(abc.ABC):
    """Sink for wire events. Impls receive a structured dict and persist it."""

    @abc.abstractmethod
    def write(self, event: dict) -> None: ...

    def close(self) -> None:
        pass


class NullWireSink(WireSink):
    """No-op sink — useful for tests or when a sink is disabled."""

    def write(self, event: dict) -> None:
        pass


class JsonlWireSink(WireSink):
    """Appends wire events as JSONL lines to a file, with rotation support."""

    def __init__(
        self,
        path: str | Path,
        max_lines: int = 100_000,
        rotation_enabled: bool = True,
    ):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._max_lines = max_lines
        self._rotation_enabled = rotation_enabled
        self._line_count = 0
        self._line_count_loaded = False

    def _ensure_open(self) -> None:
        if self._file is None:
            # buffering=1 = line-buffered so each write flushes immediately
            self._file = open(self._path, "a", buffering=1)
            if not self._line_count_loaded:
                try:
                    self._line_count = sum(1 for _ in open(self._path))
                except (FileNotFoundError, OSError):
                    self._line_count = 0
                self._line_count_loaded = True

    def _rotate_if_needed(self) -> None:
        if not self._rotation_enabled or self._line_count < self._max_lines:
            return
        if self._file:
            self._file.close()
            self._file = None
        rotated = self._path.with_suffix(".jsonl.1")
        if rotated.exists():
            rotated.unlink()
        self._path.rename(rotated)
        self._line_count = 0
        self._ensure_open()

    def write(self, event: dict) -> None:
        self._ensure_open()
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._line_count += 1
        self._rotate_if_needed()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None


class SqliteWireSink(WireSink):
    """Writes wire events to the wire_events SQLite table via a WireEventRepo."""

    def __init__(self, repo):
        self._repo = repo

    def write(self, event: dict) -> None:
        try:
            self._repo.log(
                event_type=event.get("event_type", "message"),
                source=event.get("source", "proxy"),
                content=event.get("content", ""),
                role=event.get("role", ""),
                model=event.get("model", ""),
                conv_id=event.get("conv_id"),
                run_id=event.get("run_id"),
                turn_id=event.get("turn_id"),
                tool_id=event.get("tool_id"),
                meta=event.get("meta"),
            )
        except Exception as e:
            logger.warning("SqliteWireSink.write failed: %s", e)


def make_sink(type: str, **kwargs) -> WireSink:
    """Factory for WireSink implementations.

    Types:
        "null"  — NullWireSink, no kwargs needed
        "jsonl" — JsonlWireSink; requires path=, optional max_lines=, rotation_enabled=
        "sqlite"— SqliteWireSink; requires repo=<WireEventRepo instance>
    """
    if type == "null":
        return NullWireSink()
    if type == "jsonl":
        return JsonlWireSink(**kwargs)
    if type == "sqlite":
        repo = kwargs.get("repo") or kwargs.get("store")
        if repo is None:
            raise ValueError("make_sink('sqlite') requires repo=<WireEventRepo>")
        return SqliteWireSink(repo)
    raise ValueError(f"Unknown sink type: {type!r}")
