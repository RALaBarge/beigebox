"""
Tests for wiretap module and CLI.
"""

import json
import pytest
from pathlib import Path
from beigebox.wiretap import WireLog, _format_entry


@pytest.fixture
def wire_log(tmp_path):
    """Create a WireLog writing to a temp file."""
    log_path = str(tmp_path / "wire.jsonl")
    return WireLog(log_path)


def test_wire_log_writes_jsonl(wire_log):
    """WireLog writes valid JSONL entries."""
    wire_log.log(
        direction="inbound",
        role="user",
        content="hello world",
        model="qwen3:32b",
        conversation_id="abc123",
    )
    wire_log.log(
        direction="outbound",
        role="assistant",
        content="hi there!",
        model="qwen3:32b",
        conversation_id="abc123",
    )
    wire_log.close()

    lines = Path(wire_log.log_path).read_text().strip().split("\n")
    assert len(lines) == 2

    entry1 = json.loads(lines[0])
    assert entry1["dir"] == "inbound"
    assert entry1["role"] == "user"
    assert entry1["content"] == "hello world"
    assert entry1["model"] == "qwen3:32b"

    entry2 = json.loads(lines[1])
    assert entry2["dir"] == "outbound"
    assert entry2["role"] == "assistant"


def test_wire_log_truncates_long_content(wire_log):
    """Long content gets truncated in wire log."""
    long_content = "x" * 5000
    wire_log.log(direction="outbound", role="assistant", content=long_content)
    wire_log.close()

    line = Path(wire_log.log_path).read_text().strip()
    entry = json.loads(line)
    assert len(entry["content"]) < 5000
    assert "truncated" in entry["content"]
    assert entry["len"] == 5000  # Original length preserved


def test_format_entry_raw():
    """Raw format just dumps JSON."""
    entry = {"ts": "2026-01-01T00:00:00+00:00", "role": "user", "content": "test"}
    result = _format_entry(entry, raw=True)
    assert json.loads(result) == entry


def test_format_entry_fancy():
    """Formatted output contains role and content."""
    entry = {
        "ts": "2026-01-01T12:30:00+00:00",
        "dir": "inbound",
        "role": "user",
        "model": "qwen3:32b",
        "conv": "abc123",
        "len": 11,
        "content": "hello world",
    }
    result = _format_entry(entry, raw=False)
    assert "USER" in result
    assert "hello world" in result
    assert "qwen3:32b" in result
