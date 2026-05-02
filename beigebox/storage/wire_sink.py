"""Wire event sink ABC and built-in implementations."""

import abc
import asyncio
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


class PostgresWireSink(WireSink):
    """Wire events fanned out to a postgres-backed WireEventRepo.

    Same write interface as SqliteWireSink, but the underlying BaseDB
    talks to postgres and the writes go over the network. To avoid
    stalling the FastAPI event loop on every wire event, ``write()``
    detects whether it's running inside an asyncio event loop and
    offloads the synchronous repo.log() call to ``run_in_executor``.
    Falls back to the direct synchronous path when called from CLI /
    test contexts (no running loop).

    Schema lifecycle: the caller must call ``repo.create_tables()``
    before attaching this sink to a WireLog. The repo's table layout
    matches the sqlite ``wire_events`` shape; postgres handles the
    ``meta TEXT`` column as TEXT (callers serialize with json.dumps).
    Future enhancement: switch ``meta`` to JSONB on postgres for indexed
    nested-field queries — out of scope for this commit.
    """

    def __init__(self, repo):
        self._repo = repo

    def _log_sync(self, event: dict) -> None:
        """Direct synchronous repo.log call — used when no loop is running."""
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
            logger.warning("PostgresWireSink.write failed: %s", e)

    def write(self, event: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log_sync(event)
            return
        # Fire-and-forget on the executor so we don't block the event loop.
        # The completion callback logs but doesn't propagate failures —
        # the JSONL + SQLite sinks already captured the event.
        fut = loop.run_in_executor(None, self._log_sync, event)
        fut.add_done_callback(
            lambda f: f.exception() and logger.debug(
                "PostgresWireSink async write completed with exception: %s",
                f.exception(),
            )
        )


def make_sink(type: str, **kwargs) -> WireSink:
    """Factory for WireSink implementations.

    Types:
        "null"     — NullWireSink, no kwargs needed
        "jsonl"    — JsonlWireSink; requires path=, optional max_lines=,
                     rotation_enabled=
        "sqlite"   — SqliteWireSink; requires repo=<WireEventRepo instance>
        "postgres" — PostgresWireSink; requires repo=<WireEventRepo backed
                     by a postgres BaseDB>. Repo's create_tables() must
                     be called before first write.
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
    if type == "postgres":
        repo = kwargs.get("repo") or kwargs.get("store")
        if repo is None:
            raise ValueError("make_sink('postgres') requires repo=<WireEventRepo>")
        return PostgresWireSink(repo)
    raise ValueError(f"Unknown sink type: {type!r}")
