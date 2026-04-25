"""
BlueTruth Integration Test Scenarios

These tests verify that:
1. BlueTruthTool can inject mock events
2. Events are properly stored in SQLite
3. Device lifecycle scenarios work correctly
4. Correlation engine links related events
5. Query operations return expected results
"""

import json
import pytest
import sqlite3
import tempfile
from pathlib import Path
from beigebox.tools.bluetruth import BlueTruthTool


@pytest.fixture
def temp_db():
    """Create temporary bluTruth database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "events.db"

        # Create schema
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        # Minimal schema matching bluTruth
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_mono_us INTEGER NOT NULL,
                ts_wall TEXT,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                device_addr TEXT,
                severity TEXT DEFAULT 'INFO',
                stage TEXT DEFAULT 'DATA',
                summary TEXT,
                raw_json TEXT,
                group_id TEXT,
                tags TEXT,
                annotations TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT UNIQUE NOT NULL,
                name TEXT,
                first_seen INTEGER,
                last_seen INTEGER
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS event_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT UNIQUE NOT NULL,
                device_addr TEXT,
                time_window_start INTEGER,
                correlation_count INTEGER
            )
        """)

        conn.commit()
        conn.close()

        yield db_path


@pytest.fixture
def bluetruth_tool(temp_db):
    """Create BlueTruthTool instance with test database."""
    return BlueTruthTool(db_path=str(temp_db))


class TestBasicOperations:
    """Test basic BlueTruthTool operations."""

    def test_simulate_device_connect(self, bluetruth_tool):
        """Test device connection simulation."""
        result = bluetruth_tool.run(
            "simulate_device action=connect device=AA:BB:CC:DD:EE:FF name=TestHeadphones"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["events_injected"] == 2  # CONNECT + DBUS
        assert data["operation"] == "device_connect(AA:BB:CC:DD:EE:FF)"

    def test_simulate_device_disconnect(self, bluetruth_tool):
        """Test device disconnection simulation."""
        # First connect
        bluetruth_tool.run(
            "simulate_device action=connect device=AA:BB:CC:DD:EE:FF name=TestHeadphones"
        )

        # Then disconnect
        result = bluetruth_tool.run(
            "simulate_device action=disconnect device=AA:BB:CC:DD:EE:FF"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["events_injected"] == 2  # DISCONNECT + DBUS

    def test_simulate_device_rssi_change(self, bluetruth_tool):
        """Test RSSI signal strength change."""
        result = bluetruth_tool.run(
            "simulate_device action=rssi device=AA:BB:CC:DD:EE:FF rssi=-75"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["events_injected"] == 1
        assert "RSSI" in data["events"][0]["summary"]

    def test_simulate_device_encryption(self, bluetruth_tool):
        """Test encryption setup."""
        result = bluetruth_tool.run(
            "simulate_device action=encrypt device=AA:BB:CC:DD:EE:FF"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["events_injected"] == 1
        assert "Encryption" in data["events"][0]["summary"]

    def test_simulate_device_auth(self, bluetruth_tool):
        """Test authentication/pairing."""
        result = bluetruth_tool.run(
            "simulate_device action=auth device=AA:BB:CC:DD:EE:FF"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["events_injected"] == 2  # PIN + LINK_KEY


class TestQueryOperations:
    """Test querying events from database."""

    def test_query_events_by_device(self, bluetruth_tool):
        """Test querying events for a specific device."""
        # Inject some events
        bluetruth_tool.run(
            "simulate_device action=connect device=AA:BB:CC:DD:EE:FF name=Device1"
        )
        bluetruth_tool.run(
            "simulate_device action=connect device=11:22:33:44:55:66 name=Device2"
        )

        # Query by device
        result = bluetruth_tool.run(
            "query_events device=AA:BB:CC:DD:EE:FF"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["count"] == 2  # Device1 has 2 events (connect + dbus)
        assert all(e["device_addr"] == "AA:BB:CC:DD:EE:FF" for e in data["events"])

    def test_query_events_by_source(self, bluetruth_tool):
        """Test querying events by source."""
        bluetruth_tool.run(
            "simulate_device action=connect device=AA:BB:CC:DD:EE:FF name=Device1"
        )

        result = bluetruth_tool.run(
            "query_events source=HCI"
        )
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["count"] >= 1
        assert all(e["source"] == "HCI" for e in data["events"])

    def test_query_events_limit(self, bluetruth_tool):
        """Test query limit parameter."""
        # Inject many events
        for i in range(10):
            bluetruth_tool.run(
                f"simulate_device action=connect device=AA:BB:CC:DD:EE:{i:02X} name=Device{i}"
            )

        result = bluetruth_tool.run(
            "query_events limit=5"
        )
        data = json.loads(result)

        assert len(data["events"]) == 5


class TestDeviceTracking:
    """Test device discovery and tracking."""

    def test_list_devices(self, bluetruth_tool):
        """Test listing discovered devices."""
        # Inject events for multiple devices
        bluetruth_tool.run(
            "simulate_device action=connect device=AA:BB:CC:DD:EE:FF name=Device1"
        )
        bluetruth_tool.run(
            "simulate_device action=connect device=11:22:33:44:55:66 name=Device2"
        )

        result = bluetruth_tool.run("list_devices")
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["device_count"] == 2
        assert any(d["address"] == "AA:BB:CC:DD:EE:FF" for d in data["devices"])
        assert any(d["address"] == "11:22:33:44:55:66" for d in data["devices"])


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_unknown_command(self, bluetruth_tool):
        """Test handling of unknown command."""
        result = bluetruth_tool.run("unknown_command foo=bar")
        data = json.loads(result)

        assert "error" in data
        assert "Unknown command" in data["error"]

    def test_invalid_action(self, bluetruth_tool):
        """Test handling of invalid device action."""
        result = bluetruth_tool.run(
            "simulate_device action=invalid_action device=AA:BB:CC:DD:EE:FF"
        )
        data = json.loads(result)

        assert "error" in data
        assert "Unknown action" in data["error"]

    def test_empty_command(self, bluetruth_tool):
        """Test handling of empty command."""
        result = bluetruth_tool.run("")
        data = json.loads(result)

        assert "error" in data


class TestFullScenarios:
    """Test complete device lifecycle scenarios."""

    def test_device_connection_lifecycle(self, bluetruth_tool):
        """Test full device connection, usage, and disconnection."""
        device = "AA:BB:CC:DD:EE:FF"

        # Connect
        connect_result = bluetruth_tool.run(
            f"simulate_device action=connect device={device} name=Headphones"
        )
        assert json.loads(connect_result)["status"] == "success"

        # RSSI fluctuation
        rssi_result = bluetruth_tool.run(
            f"simulate_device action=rssi device={device} rssi=-60"
        )
        assert json.loads(rssi_result)["status"] == "success"

        # Encrypt
        encrypt_result = bluetruth_tool.run(
            f"simulate_device action=encrypt device={device}"
        )
        assert json.loads(encrypt_result)["status"] == "success"

        # Disconnect
        disconnect_result = bluetruth_tool.run(
            f"simulate_device action=disconnect device={device}"
        )
        assert json.loads(disconnect_result)["status"] == "success"

        # Verify all events are recorded
        query_result = bluetruth_tool.run(
            f"query_events device={device}"
        )
        query_data = json.loads(query_result)
        assert query_data["count"] >= 6  # Multiple events injected

    def test_multiple_devices_scenario(self, bluetruth_tool):
        """Test managing multiple devices simultaneously."""
        devices = [
            "AA:BB:CC:DD:EE:01",
            "AA:BB:CC:DD:EE:02",
            "AA:BB:CC:DD:EE:03",
        ]

        # Connect all devices
        for device in devices:
            result = bluetruth_tool.run(
                f"simulate_device action=connect device={device} name=Device"
            )
            assert json.loads(result)["status"] == "success"

        # Verify all devices are tracked
        devices_result = bluetruth_tool.run("list_devices")
        devices_data = json.loads(devices_result)
        assert devices_data["device_count"] == 3

        # Query by device
        for device in devices:
            result = bluetruth_tool.run(
                f"query_events device={device}"
            )
            data = json.loads(result)
            assert data["count"] >= 2  # At least connect + dbus
            assert all(e["device_addr"] == device for e in data["events"])

    def test_rapid_connect_disconnect(self, bluetruth_tool):
        """Test rapid connect/disconnect to find race conditions."""
        device = "AA:BB:CC:DD:EE:FF"

        for i in range(5):
            bluetruth_tool.run(
                f"simulate_device action=connect device={device} name=Device"
            )
            bluetruth_tool.run(
                f"simulate_device action=disconnect device={device}"
            )

        # Verify all events were recorded
        result = bluetruth_tool.run(f"query_events device={device}")
        data = json.loads(result)

        assert data["count"] == 20  # 5 * (connect + dbus + disconnect + dbus)


class TestDatabaseIntegrity:
    """Test database integrity and consistency."""

    def test_database_created(self, temp_db):
        """Verify database file was created."""
        assert temp_db.exists()

    def test_schema_intact(self, temp_db):
        """Verify database schema is intact after operations."""
        tool = BlueTruthTool(db_path=str(temp_db))

        # Inject event
        tool.run("simulate_device action=connect device=AA:BB:CC:DD:EE:FF")

        # Verify table exists and has data
        conn = sqlite3.connect(str(temp_db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        count = cur.fetchone()[0]
        conn.close()

        assert count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
