"""
Tests for RAG poisoning monitoring & quarantine system.

Covers:
- QuarantineRepo operations (log, search, stats, purge)
- CLI commands (drive _open_quarantine_repo)
- PoisoningMetrics calculation
- VectorStore integration with quarantine logging
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from beigebox.storage.db import make_db
from beigebox.storage.repos import make_quarantine_repo
from beigebox.observability.poisoning_metrics import PoisoningMetrics


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def quarantine(tmp_db):
    """Create a QuarantineRepo backed by a fresh SQLite db."""
    db = make_db("sqlite", path=tmp_db)
    repo = make_quarantine_repo(db)
    repo.create_tables()
    yield repo
    db.close()


class TestQuarantineRepo:
    """Test QuarantineRepo operations."""

    def test_log(self, quarantine):
        """Test logging a quarantine record."""
        record_id = quarantine.log(
            document_id="msg_123",
            embedding=[1.0, 2.0, 3.0, 4.0, 5.0],
            confidence=0.95,
            reason="Embedding magnitude anomaly (z-score=3.5)",
            method="magnitude",
        )

        assert record_id is not None
        assert isinstance(record_id, int)

    def test_log_multiple(self, quarantine):
        """Test logging multiple quarantine records."""
        for i in range(5):
            quarantine.log(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 5,
                confidence=0.5 + (i * 0.1),
                reason=f"Reason {i}",
                method="magnitude",
            )

        records = quarantine.search(filters="all", limit=100)
        assert len(records) == 5

    def test_search_all(self, quarantine):
        """Test searching all quarantine records."""
        for i in range(3):
            quarantine.log(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.6 + (i * 0.1),
                reason=f"Test reason {i}",
                method="magnitude",
            )

        records = quarantine.search(filters="all", limit=100)
        assert len(records) == 3
        assert all("document_id" in r for r in records)
        assert all("confidence" in r for r in records)

    def test_search_suspicious(self, quarantine):
        """Test searching for suspicious (high confidence) records."""
        quarantine.log(
            document_id="msg_low",
            embedding=[1.0] * 10,
            confidence=0.3,
            reason="Low confidence",
            method="magnitude",
        )
        quarantine.log(
            document_id="msg_high",
            embedding=[2.0] * 10,
            confidence=0.95,
            reason="High confidence",
            method="magnitude",
        )
        quarantine.log(
            document_id="msg_very_high",
            embedding=[3.0] * 10,
            confidence=0.99,
            reason="Very high confidence",
            method="magnitude",
        )

        records = quarantine.search(filters="suspicious", limit=100)
        assert len(records) == 2
        assert all(r["confidence"] > 0.8 for r in records)

    def test_search_recent(self, quarantine):
        """Test searching for recent records (24h)."""
        quarantine.log(
            document_id="msg_recent",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Recent",
            method="magnitude",
        )

        records = quarantine.search(filters="recent", limit=100)
        assert len(records) == 1

    def test_get_by_id(self, quarantine):
        """Test fetching a single record by id."""
        record_id = quarantine.log(
            document_id="msg_solo",
            embedding=[1.0] * 5,
            confidence=0.85,
            reason="solo",
            method="magnitude",
        )
        rec = quarantine.get_by_id(record_id)
        assert rec is not None
        assert rec["document_id"] == "msg_solo"
        assert rec["confidence"] == 0.85
        assert quarantine.get_by_id(99999) is None

    def test_get_stats(self, quarantine):
        """Test statistics calculation."""
        for i in range(5):
            quarantine.log(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.5 + (i * 0.1),  # 0.5, 0.6, 0.7, 0.8, 0.9
                reason="Test reason",
                method="magnitude",
            )

        stats = quarantine.get_stats()

        assert stats["total"] == 5
        assert stats["high_confidence"] == 1  # 0.9 > 0.8
        assert stats["medium_confidence"] == 4  # rest are 0.5..0.8
        assert stats["avg_confidence"] > 0
        assert stats["reasons"]["Test reason"] == 5
        assert stats["methods"]["magnitude"] == 5

    def test_purge(self, quarantine):
        """Test purging old records."""
        quarantine.log(
            document_id="msg_new",
            embedding=[1.0] * 10,
            confidence=0.9,
            reason="New",
            method="magnitude",
        )

        records = quarantine.search(filters="all")
        assert len(records) == 1

        # Dry run with days=-1 (cutoff in the future → matches everything)
        count = quarantine.purge(days=-1, dry_run=True)
        assert count >= 1

        records = quarantine.search(filters="all")
        assert len(records) == 1  # dry run did not delete

        count = quarantine.purge(days=-1, dry_run=False)
        assert count >= 1

        records = quarantine.search(filters="all")
        assert len(records) == 0

    def test_timestamp_format(self, quarantine):
        """Test that timestamps are properly formatted."""
        quarantine.log(
            document_id="msg_ts",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Test",
            method="magnitude",
        )

        records = quarantine.search(filters="all")
        assert len(records) == 1
        ts = records[0]["timestamp"]
        assert "T" in ts  # ISO format includes T
        assert "Z" in ts  # UTC


class TestPoisoningMetrics:
    """Test metrics calculation."""

    def test_metrics_init(self, quarantine):
        metrics = PoisoningMetrics(quarantine)
        assert metrics.quarantine is not None

    def test_get_metrics_empty(self, quarantine):
        metrics = PoisoningMetrics(quarantine)
        m = metrics.get_metrics()

        assert m["quarantine_count_total"] == 0
        assert m["quarantine_count_24h"] == 0
        assert m["quarantine_confidence_avg"] == 0.0

    def test_get_metrics_with_data(self, quarantine):
        for i in range(10):
            quarantine.log(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.5 + (i * 0.04),  # 0.5..0.86
                reason="Test" if i < 5 else "Other",
                method="magnitude",
            )

        metrics = PoisoningMetrics(quarantine)
        m = metrics.get_metrics()

        assert m["quarantine_count_total"] == 10
        assert m["quarantine_count_24h"] == 10
        assert m["quarantine_confidence_avg"] > 0
        assert 0 <= m["quarantine_high_confidence_pct"] <= 100

    def test_get_prometheus_format(self, quarantine):
        quarantine.log(
            document_id="msg_1",
            embedding=[1.0] * 10,
            confidence=0.9,
            reason="Test reason",
            method="magnitude",
        )

        metrics = PoisoningMetrics(quarantine)
        prometheus = metrics.get_prometheus_format()

        assert "quarantine_count_total" in prometheus
        assert "# HELP" in prometheus
        assert "gauge" in prometheus
        assert prometheus.count("\n") > 5

    def test_get_json_metrics(self, quarantine):
        quarantine.log(
            document_id="msg_1",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Test",
            method="magnitude",
        )

        metrics = PoisoningMetrics(quarantine)
        j = metrics.get_json_metrics()

        assert "timestamp" in j
        assert "metrics" in j
        assert j["metrics"]["quarantine_count_total"] == 1

        json_str = json.dumps(j)
        assert len(json_str) > 0


class TestCLICommands:
    """Test CLI command functions."""

    def test_quarantine_list_empty(self, quarantine, capsys):
        """Test list command with no records."""
        from beigebox.cli import cmd_quarantine_list

        args = Mock()
        args.filter = "all"
        args.limit = 100

        # Patch the helper so the CLI uses our test repo instead of opening a fresh one.
        # (db, repo) tuple — db.close() is a no-op for the in-test repo.
        fake_db = Mock()
        with patch("beigebox.cli._open_quarantine_repo", return_value=(fake_db, quarantine)):
            cmd_quarantine_list(args)
            captured = capsys.readouterr()
            assert "No quarantined embeddings" in captured.out

    def test_quarantine_stats(self, quarantine, capsys):
        """Test stats command."""
        from beigebox.cli import cmd_quarantine_stats

        for i in range(3):
            quarantine.log(
                document_id=f"msg_{i}",
                embedding=[float(i)] * 10,
                confidence=0.6 + (i * 0.1),
                reason=f"Reason {i}",
                method="magnitude",
            )

        args = Mock()
        fake_db = Mock()
        with patch("beigebox.cli._open_quarantine_repo", return_value=(fake_db, quarantine)):
            cmd_quarantine_stats(args)
            captured = capsys.readouterr()
            assert "Total quarantined: 3" in captured.out or "3" in captured.out

    def test_quarantine_purge_dry_run(self, quarantine, capsys):
        """Test purge command with dry-run."""
        from beigebox.cli import cmd_quarantine_purge

        quarantine.log(
            document_id="msg_old",
            embedding=[1.0] * 10,
            confidence=0.8,
            reason="Old record",
            method="magnitude",
        )

        args = Mock()
        args.days = 0
        args.dry_run = True

        fake_db = Mock()
        with patch("beigebox.cli._open_quarantine_repo", return_value=(fake_db, quarantine)):
            cmd_quarantine_purge(args)
            captured = capsys.readouterr()
            assert "DRY RUN" in captured.out


class TestVectorStoreIntegration:
    """Test VectorStore integration with quarantine logging."""

    def test_vector_store_logs_quarantine(self, quarantine):
        """Test that VectorStore logs to quarantine when detector flags."""
        from beigebox.storage.vector_store import VectorStore
        from beigebox.storage.backends import make_backend
        from beigebox.security.rag_poisoning_detector import RAGPoisoningDetector

        detector = Mock(spec=RAGPoisoningDetector)
        detector.is_poisoned.return_value = (True, 0.95, "Test poisoning")

        vs = VectorStore(
            embedding_model="nomic-embed-text",
            embedding_url="http://localhost:11434",
            backend=make_backend("memory"),
            poisoning_detector=detector,
            quarantine=quarantine,
        )

        with patch.object(vs, "_get_embedding", return_value=[1.0] * 100):
            vs.store_message(
                message_id="test_msg",
                conversation_id="conv_1",
                role="user",
                content="Test content",
                model="test_model",
                timestamp="2024-01-01T00:00:00Z",
            )

        records = quarantine.search(filters="all")
        assert len(records) == 1
        assert records[0]["document_id"] == "test_msg"
        assert records[0]["confidence"] == 0.95

    def test_vector_store_without_quarantine(self):
        """Test VectorStore without a quarantine repo wired up."""
        from beigebox.storage.vector_store import VectorStore
        from beigebox.storage.backends import make_backend

        vs = VectorStore(
            embedding_model="nomic-embed-text",
            embedding_url="http://localhost:11434",
            backend=make_backend("memory"),
            quarantine=None,
        )

        assert vs.quarantine is None


class TestMetricsEndpoint:
    """Test metrics endpoint integration."""

    def test_metrics_json_format(self, quarantine):
        metrics = PoisoningMetrics(quarantine)
        j = metrics.get_json_metrics()

        json_str = json.dumps(j)
        parsed = json.loads(json_str)
        assert "timestamp" in parsed
        assert "metrics" in parsed

    def test_metrics_prometheus_format(self, quarantine):
        metrics = PoisoningMetrics(quarantine)
        prometheus = metrics.get_prometheus_format()

        assert "# HELP" in prometheus or "# TYPE" in prometheus
        assert "quarantine_count_total" in prometheus
        assert isinstance(prometheus, str)
        assert len(prometheus) > 50
