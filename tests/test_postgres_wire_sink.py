"""Tests for PostgresWireSink + per-sink fault isolation in WireLog.

The async-write path is covered with a fake repo since spinning up a
real postgres in CI is overkill for this unit. A separate file would
be needed if we wanted full integration coverage against a real DB.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from beigebox.storage.wire_sink import (
    PostgresWireSink,
    WireSink,
    make_sink,
)
from beigebox.wiretap import WireLog


class FakeRepo:
    """Records every log() call. Optionally raises on demand."""

    def __init__(self, *, raise_on_log: bool = False) -> None:
        self.calls: list[dict] = []
        self._raise = raise_on_log

    def log(self, **kwargs) -> None:
        if self._raise:
            raise RuntimeError("postgres connection refused")
        self.calls.append(kwargs)


# ── PostgresWireSink unit tests --------------------------------------------


class TestPostgresWireSink:
    def test_is_wire_sink(self):
        assert isinstance(PostgresWireSink(FakeRepo()), WireSink)

    def test_sync_path_when_no_loop(self):
        repo = FakeRepo()
        sink = PostgresWireSink(repo)
        sink.write({
            "event_type": "tool_call",
            "source": "tools",
            "content": "test",
            "role": "tool",
            "model": "",
            "meta": {"x": 1},
        })
        # No event loop → went down the sync path → repo.log was called inline
        assert len(repo.calls) == 1
        assert repo.calls[0]["event_type"] == "tool_call"
        assert repo.calls[0]["meta"] == {"x": 1}

    def test_sync_path_swallows_repo_error(self):
        repo = FakeRepo(raise_on_log=True)
        sink = PostgresWireSink(repo)
        # Must not raise — sink-level errors are swallowed and logged
        sink.write({"event_type": "x", "source": "s"})

    @pytest.mark.asyncio
    async def test_async_path_runs_in_executor(self):
        repo = FakeRepo()
        sink = PostgresWireSink(repo)
        sink.write({
            "event_type": "request_started",
            "source": "proxy",
            "content": "x",
            "role": "request",
            "model": "m",
            "meta": {"tokens": 42},
        })
        # The async path schedules a run_in_executor; let the loop drain
        # so the executor task actually runs.
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert len(repo.calls) == 1
        assert repo.calls[0]["event_type"] == "request_started"

    @pytest.mark.asyncio
    async def test_async_path_does_not_raise_on_repo_error(self):
        repo = FakeRepo(raise_on_log=True)
        sink = PostgresWireSink(repo)
        sink.write({"event_type": "x", "source": "s"})
        # Drain the executor
        for _ in range(5):
            await asyncio.sleep(0.01)
        # No exception should escape; repo got a chance to raise but nothing here

    def test_make_sink_postgres(self):
        sink = make_sink("postgres", repo=FakeRepo())
        assert isinstance(sink, PostgresWireSink)

    def test_make_sink_postgres_legacy_store_kwarg(self):
        sink = make_sink("postgres", store=FakeRepo())
        assert isinstance(sink, PostgresWireSink)

    def test_make_sink_postgres_requires_repo(self):
        with pytest.raises(ValueError, match="postgres"):
            make_sink("postgres")


# ── Per-sink fault isolation in WireLog.log() ----------------------------


class CapturingSink(WireSink):
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


class BrokenSink(WireSink):
    def write(self, event: dict) -> None:
        raise RuntimeError("sink broke")


@pytest.fixture
def wire_log_with_two_sinks():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "wire.jsonl"
        broken = BrokenSink()
        good = CapturingSink()
        wl = WireLog(str(log_path), sinks=[broken, good])
        yield wl, broken, good
        wl.close()


class TestPerSinkFaultIsolation:
    def test_one_broken_sink_does_not_block_others(self, wire_log_with_two_sinks):
        wl, broken, good = wire_log_with_two_sinks

        wl.log(
            direction="inbound",
            role="user",
            content="hi",
            model="m",
            conversation_id="c1",
        )

        # Despite the broken sink raising, the good sink must still
        # receive the event.
        assert len(good.events) == 1
        assert good.events[0]["content"] == "hi"
