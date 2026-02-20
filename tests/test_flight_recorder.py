"""
Tests for flight recorder.
Run with: pytest tests/test_flight_recorder.py
"""

import pytest
import time

from beigebox.flight_recorder import FlightRecord, FlightRecorderStore


# ---------------------------------------------------------------------------
# FlightRecord
# ---------------------------------------------------------------------------

def test_record_basic_timeline():
    """FlightRecord captures ordered events with elapsed time."""
    rec = FlightRecord(conversation_id="conv1", model="llama3.2")
    rec.log("Request Received", tokens=10)
    time.sleep(0.01)
    rec.log("Routing Complete", model="llama3.2")
    time.sleep(0.01)
    rec.log("Backend Response", latency_ms=500)
    rec.close()

    assert rec.id
    assert rec.conversation_id == "conv1"
    assert len(rec.events) == 4  # 3 logged + 1 "Complete"
    assert rec.events[0]["stage"] == "Request Received"
    assert rec.events[-1]["stage"] == "Complete"

    # Elapsed times should be increasing
    for i in range(1, len(rec.events)):
        assert rec.events[i]["elapsed_ms"] >= rec.events[i - 1]["elapsed_ms"]


def test_record_total_ms():
    """total_ms returns elapsed from first to last event."""
    rec = FlightRecord()
    rec.log("Start")
    time.sleep(0.02)
    rec.log("End")

    assert rec.total_ms >= 15  # At least 15ms (some slack for CI)


def test_record_summary_breakdown():
    """Summary computes per-stage breakdown with percentages."""
    rec = FlightRecord()
    rec.log("Start")
    time.sleep(0.01)
    rec.log("Routing")
    time.sleep(0.02)
    rec.log("Backend")
    rec.close()

    s = rec.summary()
    assert "total_ms" in s
    assert "breakdown" in s
    assert "Backend" in s["breakdown"] or "Routing" in s["breakdown"]


def test_record_to_json():
    """to_json exports all fields."""
    rec = FlightRecord(conversation_id="c1", model="test")
    rec.log("Start")
    rec.close()

    j = rec.to_json()
    assert j["id"] == rec.id
    assert j["conversation_id"] == "c1"
    assert j["model"] == "test"
    assert len(j["events"]) == 2
    assert "summary" in j


def test_record_render_text():
    """render_text produces readable output."""
    rec = FlightRecord(conversation_id="c1", model="test")
    rec.log("Request Received", tokens=10)
    rec.log("Routing Complete", model="test")
    rec.close()

    text = rec.render_text()
    assert "FLIGHT RECORD" in text
    assert "Request Received" in text
    assert "TOTAL:" in text


def test_record_closed_ignores_further_logs():
    """Closed records silently ignore additional log calls."""
    rec = FlightRecord()
    rec.log("Start")
    rec.close()
    count = len(rec.events)
    rec.log("Should Not Appear")
    assert len(rec.events) == count


def test_record_details_filtering():
    """None values are filtered from event details."""
    rec = FlightRecord()
    rec.log("Test", model="x", tools=None, confidence=0.9)

    details = rec.events[0]["details"]
    assert "model" in details
    assert "confidence" in details
    assert "tools" not in details


# ---------------------------------------------------------------------------
# FlightRecorderStore
# ---------------------------------------------------------------------------

def test_store_basic():
    """Store accepts and retrieves records."""
    store = FlightRecorderStore(max_records=100)
    rec = FlightRecord(conversation_id="c1")
    rec.log("Start")
    rec.close()
    store.store(rec)

    assert store.count == 1
    assert store.get(rec.id) is rec


def test_store_recent():
    """recent() returns most recent N records."""
    store = FlightRecorderStore(max_records=100)
    for i in range(5):
        rec = FlightRecord(conversation_id=f"c{i}")
        rec.log("Start")
        rec.close()
        store.store(rec)

    recent = store.recent(n=3)
    assert len(recent) == 3
    assert recent[-1].conversation_id == "c4"


def test_store_eviction_on_max():
    """Store evicts oldest when max_records is reached."""
    store = FlightRecorderStore(max_records=3)
    ids = []
    for i in range(5):
        rec = FlightRecord()
        rec.log("Start")
        rec.close()
        store.store(rec)
        ids.append(rec.id)

    assert store.count == 3
    # First two should be evicted
    assert store.get(ids[0]) is None
    assert store.get(ids[1]) is None
    # Last three should remain
    assert store.get(ids[2]) is not None
    assert store.get(ids[4]) is not None


def test_store_get_nonexistent():
    """get() returns None for unknown IDs."""
    store = FlightRecorderStore()
    assert store.get("nonexistent") is None
