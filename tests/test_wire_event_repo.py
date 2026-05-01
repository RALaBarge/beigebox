"""Tests for WireEventRepo (BaseDB-backed wire-events storage)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from beigebox.storage.db import make_db
from beigebox.storage.repos import make_wire_event_repo


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = make_db("sqlite", path=path)
    r = make_wire_event_repo(db)
    r.create_tables()
    yield r
    db.close()
    Path(path).unlink(missing_ok=True)


class TestLog:
    def test_log_minimal(self, repo):
        repo.log(event_type="message", source="proxy")
        rows = repo.query(n=10)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "message"
        assert rows[0]["source"] == "proxy"
        assert rows[0]["ts"]  # ISO timestamp populated

    def test_log_full_payload(self, repo):
        repo.log(
            event_type="tool_call",
            source="proxy",
            content="hello world",
            role="assistant",
            model="gpt-4o",
            conv_id="c1",
            run_id="r1",
            turn_id="t1",
            tool_id="tl1",
            meta={"latency_ms": 42.5, "tokens": 17},
        )
        rows = repo.query(n=10)
        assert len(rows) == 1
        r = rows[0]
        assert r["event_type"] == "tool_call"
        assert r["model"] == "gpt-4o"
        assert r["conv_id"] == "c1"
        # meta is decoded back to a dict on read
        assert r["meta"] == {"latency_ms": 42.5, "tokens": 17}

    def test_log_truncates_long_content(self, repo):
        long_content = "x" * 5000
        repo.log(event_type="message", source="proxy", content=long_content)
        rows = repo.query(n=1)
        assert "[...3000 chars truncated...]" in rows[0]["content"]
        assert len(rows[0]["content"]) < 5000


class TestQuery:
    def _seed(self, repo):
        repo.log(event_type="message", source="proxy", role="user", conv_id="c1")
        repo.log(event_type="message", source="proxy", role="assistant", conv_id="c1", run_id="r1")
        repo.log(event_type="tool_call", source="operator", role="tool", run_id="r1")
        repo.log(event_type="message", source="proxy", role="user", conv_id="c2")

    def test_query_all_newest_first(self, repo):
        self._seed(repo)
        rows = repo.query(n=10)
        assert len(rows) == 4
        # newest-first: id descending
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids, reverse=True)

    def test_query_filter_event_type(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, event_type="tool_call")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "tool_call"

    def test_query_filter_source(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, source="operator")
        assert len(rows) == 1
        assert rows[0]["source"] == "operator"

    def test_query_filter_conv_id(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, conv_id="c1")
        assert len(rows) == 2
        assert all(r["conv_id"] == "c1" for r in rows)

    def test_query_filter_run_id(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, run_id="r1")
        assert len(rows) == 2
        assert all(r["run_id"] == "r1" for r in rows)

    def test_query_filter_role(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, role="user")
        assert len(rows) == 2
        assert all(r["role"] == "user" for r in rows)

    def test_query_combined_filters(self, repo):
        self._seed(repo)
        rows = repo.query(n=10, conv_id="c1", role="user")
        assert len(rows) == 1
        assert rows[0]["conv_id"] == "c1"
        assert rows[0]["role"] == "user"

    def test_query_respects_limit(self, repo):
        for _ in range(5):
            repo.log(event_type="message", source="proxy")
        rows = repo.query(n=2)
        assert len(rows) == 2

    def test_query_empty_table(self, repo):
        assert repo.query(n=10) == []


class TestSchema:
    def test_create_tables_idempotent(self, repo):
        repo.create_tables()
        repo.create_tables()
        repo.log(event_type="message", source="proxy")
        assert len(repo.query(n=1)) == 1
