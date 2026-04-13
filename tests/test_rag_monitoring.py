"""
Tests for RAG poisoning monitoring & quarantine system.

Covers:
- Quarantine table operations (log, search, stats, purge)
- CLI commands
- Metrics calculation
- VectorStore integration with quarantine logging
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock

from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.observability.poisoning_metrics import PoisoningMetrics


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sqlite_store(tmp_db):
    """Create a SQLiteStore instance."""
    return SQLiteStore(tmp_db)


class TestQuarantineDatabase:
    """Test quarantine table operations."""

    def test_log_quarantine(self, sqlite_store):
        """Test logging a quarantine record."""
        doc_id = "msg_123"
        embedding = [1.0, 2.0, 3.0, 4.0, 5.0]
        confidence = 0.95
        reason = "Embedding magnitude anomaly (z-score=3.5)"
        method = "magnitude"

        record_id = sqlite_store.log_quarantine(
            document_id=doc_id,
            embedding=embedding,
            confidence=confidence,
            reason=reason,
            method=method,
        )

        assert record_id is not None
        assert isinstance(record_id, int)

    def test_log_multiple_quarantines(self, sqlite_store):
        """Test logging multiple quarantine records."""
        for i in range(5):
            sqlite_store.log_quarantine(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 5,
                confidence=0.5 + (i * 0.1),
                reason=f"Reason {i}",
                method="magnitude",
            )

        records = sqlite_store.search_quarantined(filters="all", limit=100)
        assert len(records) == 5

    def test_search_quarantined_all(self, sqlite_store):
        """Test searching all quarantine records."""
        for i in range(3):
            sqlite_store.log_quarantine(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.6 + (i * 0.1),
                reason=f"Test reason {i}",
                method="magnitude",
            )

        records = sqlite_store.search_quarantined(filters="all", limit=100)
        assert len(records) == 3
        assert all("document_id" in r for r in records)
        assert all("confidence" in r for r in records)

    def test_search_quarantined_suspicious(self, sqlite_store):
        """Test searching for suspicious (high confidence) records."""
        # Log records with varying confidence
        sqlite_store.log_quarantine(
            document_id="msg_low",
            embedding=[1.0] * 10,
            confidence=0.3,
            reason="Low confidence",
            method="magnitude",
        )
        sqlite_store.log_quarantine(
            document_id="msg_high",
            embedding=[2.0] * 10,
            confidence=0.95,
            reason="High confidence",
            method="magnitude",
        )
        sqlite_store.log_quarantine(
            document_id="msg_very_high",
            embedding=[3.0] * 10,
            confidence=0.99,
            reason="Very high confidence",
            method="magnitude",
        )

        records = sqlite_store.search_quarantined(filters="suspicious", limit=100)
        # Should only get > 0.8
        assert len(records) == 2
        assert all(r["confidence"] > 0.8 for r in records)

    def test_search_quarantined_recent(self, sqlite_store):
        """Test searching for recent records (24h)."""
        # Note: recent filter checks timestamp >= cutoff
        # All newly inserted records should be "recent"
        sqlite_store.log_quarantine(
            document_id="msg_recent",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Recent",
            method="magnitude",
        )

        records = sqlite_store.search_quarantined(filters="recent", limit=100)
        assert len(records) == 1

    def test_get_quarantine_stats(self, sqlite_store):
        """Test statistics calculation."""
        # Log records with various confidence levels
        for i in range(5):
            sqlite_store.log_quarantine(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.5 + (i * 0.1),  # 0.5, 0.6, 0.7, 0.8, 0.9
                reason="Test reason",
                method="magnitude",
            )

        stats = sqlite_store.get_quarantine_stats()

        assert stats["total"] == 5
        assert stats["high_confidence"] == 1  # 0.9 > 0.8
        assert stats["medium_confidence"] == 4  # rest are 0.5-0.8
        assert stats["avg_confidence"] > 0  # Should be ~0.7
        assert stats["reasons"]["Test reason"] == 5
        assert stats["methods"]["magnitude"] == 5

    def test_purge_quarantine(self, sqlite_store):
        """Test purging old records."""
        # Add a test record (will have current timestamp)
        sqlite_store.log_quarantine(
            document_id="msg_new",
            embedding=[1.0] * 10,
            confidence=0.9,
            reason="New",
            method="magnitude",
        )

        # Check it's there
        records = sqlite_store.search_quarantined(filters="all")
        assert len(records) == 1

        # Dry run with days=-1 (delete records older than tomorrow, i.e., everything)
        count = sqlite_store.purge_quarantine(days=-1, dry_run=True)
        assert count >= 1  # Should find records to delete

        # Check still there
        records = sqlite_store.search_quarantined(filters="all")
        assert len(records) == 1

        # Actually purge with days=-1
        count = sqlite_store.purge_quarantine(days=-1, dry_run=False)
        assert count >= 1

        # Should be gone
        records = sqlite_store.search_quarantined(filters="all")
        assert len(records) == 0

    def test_quarantine_timestamp_format(self, sqlite_store):
        """Test that timestamps are properly formatted."""
        sqlite_store.log_quarantine(
            document_id="msg_ts",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Test",
            method="magnitude",
        )

        records = sqlite_store.search_quarantined(filters="all")
        assert len(records) == 1
        # Timestamp should be ISO format
        ts = records[0]["timestamp"]
        assert "T" in ts  # ISO format includes T
        assert "Z" in ts  # Should be UTC


class TestPoisoningMetrics:
    """Test metrics calculation."""

    def test_metrics_init(self, sqlite_store):
        """Test metrics initialization."""
        metrics = PoisoningMetrics(sqlite_store)
        assert metrics.sqlite_store is not None

    def test_get_metrics_empty(self, sqlite_store):
        """Test metrics with empty database."""
        metrics = PoisoningMetrics(sqlite_store)
        m = metrics.get_metrics()

        assert m["quarantine_count_total"] == 0
        assert m["quarantine_count_24h"] == 0
        assert m["quarantine_confidence_avg"] == 0.0

    def test_get_metrics_with_data(self, sqlite_store):
        """Test metrics with quarantine data."""
        # Add some quarantine records
        for i in range(10):
            sqlite_store.log_quarantine(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.5 + (i * 0.04),  # 0.5 to 0.86
                reason="Test" if i < 5 else "Other",
                method="magnitude",
            )

        metrics = PoisoningMetrics(sqlite_store)
        m = metrics.get_metrics()

        assert m["quarantine_count_total"] == 10
        assert m["quarantine_count_24h"] == 10
        assert m["quarantine_confidence_avg"] > 0
        assert 0 <= m["quarantine_high_confidence_pct"] <= 100

    def test_get_prometheus_format(self, sqlite_store):
        """Test Prometheus format output."""
        # Add some data
        sqlite_store.log_quarantine(
            document_id="msg_1",
            embedding=[1.0] * 10,
            confidence=0.9,
            reason="Test reason",
            method="magnitude",
        )

        metrics = PoisoningMetrics(sqlite_store)
        prometheus = metrics.get_prometheus_format()

        assert "quarantine_count_total" in prometheus
        assert "# HELP" in prometheus
        assert "gauge" in prometheus
        # Should be valid metric lines
        assert prometheus.count("\n") > 5

    def test_get_json_metrics(self, sqlite_store):
        """Test JSON metrics output."""
        sqlite_store.log_quarantine(
            document_id="msg_1",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Test",
            method="magnitude",
        )

        metrics = PoisoningMetrics(sqlite_store)
        j = metrics.get_json_metrics()

        assert "timestamp" in j
        assert "metrics" in j
        assert j["metrics"]["quarantine_count_total"] == 1

        # Should be JSON-serializable
        json_str = json.dumps(j)
        assert len(json_str) > 0


class TestCLICommands:
    """Test CLI command functions."""

    def test_quarantine_list_empty(self, sqlite_store, capsys):
        """Test list command with no records."""
        from beigebox.cli import cmd_quarantine_list

        args = Mock()
        args.filter = "all"
        args.limit = 100

        with patch("beigebox.config.get_config") as mock_cfg, \
             patch("beigebox.config.get_storage_paths") as mock_paths, \
             patch("beigebox.storage.sqlite_store.SQLiteStore", return_value=sqlite_store):

            mock_cfg.return_value = {}
            mock_paths.return_value = ("/tmp/test.db", "/tmp/chroma")

            cmd_quarantine_list(args)
            captured = capsys.readouterr()
            assert "No quarantined embeddings" in captured.out

    def test_quarantine_stats(self, sqlite_store, capsys):
        """Test stats command."""
        from beigebox.cli import cmd_quarantine_stats

        # Add some data
        for i in range(3):
            sqlite_store.log_quarantine(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.6 + (i * 0.1),
                reason=f"Reason {i}",
                method="magnitude",
            )

        args = Mock()

        with patch("beigebox.config.get_config") as mock_cfg, \
             patch("beigebox.config.get_storage_paths") as mock_paths, \
             patch("beigebox.storage.sqlite_store.SQLiteStore", return_value=sqlite_store):

            mock_cfg.return_value = {}
            mock_paths.return_value = ("/tmp/test.db", "/tmp/chroma")

            cmd_quarantine_stats(args)
            captured = capsys.readouterr()
            assert "Total quarantined: 3" in captured.out or "3" in captured.out

    def test_quarantine_purge_dry_run(self, sqlite_store, capsys):
        """Test purge command with dry-run."""
        from beigebox.cli import cmd_quarantine_purge

        sqlite_store.log_quarantine(
            document_id="msg_old",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Old record",
            method="magnitude",
        )

        args = Mock()
        args.days = 0
        args.dry_run = True

        with patch("beigebox.config.get_config") as mock_cfg, \
             patch("beigebox.config.get_storage_paths") as mock_paths, \
             patch("beigebox.storage.sqlite_store.SQLiteStore", return_value=sqlite_store):

            mock_cfg.return_value = {}
            mock_paths.return_value = ("/tmp/test.db", "/tmp/chroma")

            cmd_quarantine_purge(args)
            captured = capsys.readouterr()
            assert "DRY RUN" in captured.out


class TestVectorStoreIntegration:
    """Test VectorStore integration with quarantine logging."""

    def test_vector_store_logs_quarantine(self, tmp_db):
        """Test that VectorStore logs to quarantine when detector flags."""
        from beigebox.storage.vector_store import VectorStore
        from beigebox.storage.backends import make_backend
        from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

        sqlite_store = SQLiteStore(tmp_db)

        # Create a detector that flags embeddings
        detector = Mock(spec=RAGPoisoningDetector)
        detector.is_poisoned.return_value = (True, 0.95, "Test poisoning")

        # Create VectorStore with detector and sqlite_store
        with tempfile.TemporaryDirectory() as chroma_dir:
            vs = VectorStore(
                embedding_model="nomic-embed-text",
                embedding_url="http://localhost:11434",
                backend=make_backend("chromadb", path=chroma_dir),
                poisoning_detector=detector,
                sqlite_store=sqlite_store,
            )

            # Mock the embedding call
            with patch.object(vs, "_get_embedding", return_value=[1.0] * 100):
                # Store a message
                vs.store_message(
                    message_id="test_msg",
                    conversation_id="conv_1",
                    role="user",
                    content="Test content",
                    model="test_model",
                    timestamp="2024-01-01T00:00:00Z",
                )

            # Check that quarantine was logged
            records = sqlite_store.search_quarantined(filters="all")
            assert len(records) == 1
            assert records[0]["document_id"] == "test_msg"
            assert records[0]["confidence"] == 0.95

    def test_vector_store_without_quarantine(self, tmp_db):
        """Test VectorStore without quarantine logging."""
        from beigebox.storage.vector_store import VectorStore
        from beigebox.storage.backends import make_backend

        sqlite_store = SQLiteStore(tmp_db)

        # Create VectorStore without sqlite_store
        with tempfile.TemporaryDirectory() as chroma_dir:
            vs = VectorStore(
                embedding_model="nomic-embed-text",
                embedding_url="http://localhost:11434",
                backend=make_backend("chromadb", path=chroma_dir),
                sqlite_store=None,  # No quarantine
            )

            # Should initialize without errors
            assert vs.sqlite_store is None


class TestMetricsEndpoint:
    """Test metrics endpoint integration."""

    def test_metrics_json_format(self, sqlite_store):
        """Test that metrics can be returned as JSON."""
        metrics = PoisoningMetrics(sqlite_store)
        j = metrics.get_json_metrics()

        # Should be JSON-serializable
        json_str = json.dumps(j)
        parsed = json.loads(json_str)
        assert "timestamp" in parsed
        assert "metrics" in parsed

    def test_metrics_prometheus_format(self, sqlite_store):
        """Test that metrics can be returned as Prometheus text."""
        metrics = PoisoningMetrics(sqlite_store)
        prometheus = metrics.get_prometheus_format()

        # Should contain required Prometheus elements
        assert "# HELP" in prometheus or "# TYPE" in prometheus
        assert "quarantine_count_total" in prometheus
        # Should be valid text format
        assert isinstance(prometheus, str)
        assert len(prometheus) > 50
