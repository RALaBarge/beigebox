"""Tests for WireSink ABC and built-in implementations."""

import json
import pytest
from pathlib import Path

from beigebox.storage.wire_sink import (
    JsonlWireSink,
    NullWireSink,
    SqliteWireSink,
    WireSink,
    make_sink,
)


def test_null_sink_is_wire_sink():
    sink = NullWireSink()
    assert isinstance(sink, WireSink)


def test_null_sink_write_does_nothing():
    sink = NullWireSink()
    sink.write({"role": "user", "content": "hello"})  # must not raise
    sink.close()


def test_jsonl_sink_writes_line(tmp_path):
    path = tmp_path / "wire.jsonl"
    sink = JsonlWireSink(path=path)
    event = {"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00+00:00"}
    sink.write(event)
    sink.close()

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["role"] == "user"
    assert parsed["content"] == "hi"


def test_jsonl_sink_appends_multiple(tmp_path):
    path = tmp_path / "wire.jsonl"
    sink = JsonlWireSink(path=path)
    for i in range(5):
        sink.write({"n": i})
    sink.close()

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[4])["n"] == 4


def test_jsonl_sink_rotation(tmp_path):
    path = tmp_path / "wire.jsonl"
    sink = JsonlWireSink(path=path, max_lines=3, rotation_enabled=True)
    for i in range(4):
        sink.write({"n": i})
    sink.close()

    # After 4 writes with max_lines=3, rotation should have fired
    rotated = path.with_suffix(".jsonl.1")
    assert rotated.exists(), "rotated file should exist"
    # The new file should have at least one line (the 4th write)
    fresh_lines = path.read_text().strip().splitlines()
    assert len(fresh_lines) >= 1


def test_jsonl_sink_no_rotation_when_disabled(tmp_path):
    path = tmp_path / "wire.jsonl"
    sink = JsonlWireSink(path=path, max_lines=2, rotation_enabled=False)
    for i in range(5):
        sink.write({"n": i})
    sink.close()

    rotated = path.with_suffix(".jsonl.1")
    assert not rotated.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 5


def test_jsonl_sink_is_wire_sink(tmp_path):
    sink = JsonlWireSink(path=tmp_path / "w.jsonl")
    assert isinstance(sink, WireSink)


def test_sqlite_sink_delegates_to_repo():
    calls = []

    class FakeRepo:
        def log(self, **kwargs):
            calls.append(kwargs)

    sink = SqliteWireSink(FakeRepo())
    sink.write({
        "event_type": "message",
        "source": "proxy",
        "content": "hello",
        "role": "user",
        "model": "gpt-4o",
        "conv_id": "abc123",
        "run_id": None,
        "turn_id": None,
        "tool_id": None,
        "meta": None,
    })

    assert len(calls) == 1
    assert calls[0]["role"] == "user"
    assert calls[0]["content"] == "hello"
    assert calls[0]["conv_id"] == "abc123"


def test_sqlite_sink_swallows_repo_error():
    class BrokenRepo:
        def log(self, **kwargs):
            raise RuntimeError("db gone")

    sink = SqliteWireSink(BrokenRepo())
    sink.write({"event_type": "message", "source": "proxy"})  # must not raise


def test_make_sink_null():
    sink = make_sink("null")
    assert isinstance(sink, NullWireSink)


def test_make_sink_jsonl(tmp_path):
    sink = make_sink("jsonl", path=tmp_path / "w.jsonl")
    assert isinstance(sink, JsonlWireSink)
    sink.write({"x": 1})
    sink.close()


def test_make_sink_sqlite():
    class FakeRepo:
        def log(self, **kwargs):
            pass

    sink = make_sink("sqlite", repo=FakeRepo())
    assert isinstance(sink, SqliteWireSink)


def test_make_sink_sqlite_legacy_store_kwarg():
    """Backward-compat: store= kwarg still accepted (alias for repo=)."""
    class FakeRepo:
        def log(self, **kwargs):
            pass

    sink = make_sink("sqlite", store=FakeRepo())
    assert isinstance(sink, SqliteWireSink)


def test_make_sink_sqlite_requires_repo():
    with pytest.raises(ValueError, match="repo"):
        make_sink("sqlite")


def test_make_sink_unknown_type():
    with pytest.raises(ValueError, match="Unknown sink type"):
        make_sink("redis")
