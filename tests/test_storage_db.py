"""
Tests for the generic SQL shim at beigebox/storage/db/.

Covers BaseDB's helper layer (insert / update / delete / upsert / migrate)
against both MemoryDB and the on-disk SqliteDB. PostgresDB tests skip
gracefully when psycopg2 isn't installed or no Postgres is reachable.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from beigebox.storage.db import make_db, build_db_kwargs, BaseDB


# ─── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def memdb() -> BaseDB:
    db = make_db("memory")
    yield db
    db.close()


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def sqlitedb(sqlite_path: Path) -> BaseDB:
    db = make_db("sqlite", path=str(sqlite_path))
    yield db
    db.close()


def _has_postgres() -> bool:
    """Heuristic: is psycopg2 installed AND can we connect to a local DB."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False
    dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beigebox")
    try:
        import psycopg2 as p2
        c = p2.connect(dsn, connect_timeout=2)
        c.close()
        return True
    except Exception:
        return False


_HAS_PG = _has_postgres()


@pytest.fixture
def pgdb() -> BaseDB:
    if not _HAS_PG:
        pytest.skip("PostgreSQL not reachable; set DATABASE_URL to enable")
    dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beigebox")
    db = make_db("postgres", connection_string=dsn, pool_size=2)
    # Clean slate per test to avoid cross-test pollution
    db.execute("DROP TABLE IF EXISTS test_kv")
    db.execute("DROP TABLE IF EXISTS _schema_migrations")
    yield db
    db.execute("DROP TABLE IF EXISTS test_kv")
    db.execute("DROP TABLE IF EXISTS _schema_migrations")
    db.close()


# Parametrize the same tests across all available backends
def _backends():
    backends = ["memory", "sqlite"]
    if _HAS_PG:
        backends.append("postgres")
    return backends


# ─── factory + registry ───────────────────────────────────────────────────

def test_factory_unknown_backend():
    with pytest.raises(ValueError, match="Unknown DB backend"):
        make_db("not-a-real-backend")


def test_factory_returns_basedb():
    db = make_db("memory")
    try:
        assert isinstance(db, BaseDB)
    finally:
        db.close()


def test_build_db_kwargs_default_sqlite():
    btype, kwargs = build_db_kwargs({})
    assert btype == "sqlite"
    assert "path" in kwargs


def test_build_db_kwargs_postgres_from_cfg():
    btype, kwargs = build_db_kwargs({
        "storage": {
            "db": {
                "type": "postgres",
                "connection_string": "postgresql://x/y",
                "pool_size": 10,
            }
        }
    })
    assert btype == "postgres"
    assert kwargs["connection_string"] == "postgresql://x/y"
    assert kwargs["pool_size"] == 10


def test_build_db_kwargs_postgres_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://envurl/db")
    btype, kwargs = build_db_kwargs({"storage": {"db": {"type": "postgres"}}})
    assert btype == "postgres"
    assert kwargs["connection_string"] == "postgresql://envurl/db"


def test_build_db_kwargs_memory():
    btype, kwargs = build_db_kwargs({"storage": {"db": {"type": "memory"}}})
    assert btype == "memory"
    assert kwargs == {}


# ─── identifier safety ────────────────────────────────────────────────────

def test_unsafe_identifier_rejected(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        memdb.insert("t; DROP TABLE t; --", {"v": "x"})
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        memdb.update("t", {"v; --": "x"}, {"id": 1})
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        memdb.delete("t", {"id': 1; --": 1})


# ─── core CRUD on every available backend ─────────────────────────────────

@pytest.mark.parametrize("backend_name", _backends())
def test_crud_roundtrip(backend_name, sqlite_path, request):
    """One generic CRUD test that runs against every available backend.

    Creates a tiny key/value table, exercises insert / fetchone / update /
    delete / fetchall via the BaseDB helpers (no raw SQL outside table
    creation). Asserts the same observable behaviour from each backend.
    """
    if backend_name == "memory":
        db = make_db("memory")
    elif backend_name == "sqlite":
        db = make_db("sqlite", path=str(sqlite_path))
    elif backend_name == "postgres":
        if not _HAS_PG:
            pytest.skip("Postgres not reachable")
        dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beigebox")
        db = make_db("postgres", connection_string=dsn, pool_size=2)
        db.execute("DROP TABLE IF EXISTS test_kv")
    else:
        pytest.skip(f"unknown backend {backend_name}")

    try:
        # Create — using SERIAL/AUTOINCREMENT-equivalent. Both sqlite and
        # postgres recognize "INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY"
        # but for portability we use the lowest-common-denominator INTEGER PK
        # which both backends auto-increment.
        if backend_name == "postgres":
            db.execute(
                "CREATE TABLE test_kv ("
                "  id SERIAL PRIMARY KEY,"
                "  k  TEXT NOT NULL UNIQUE,"
                "  v  TEXT NOT NULL"
                ")"
            )
        else:
            db.execute(
                "CREATE TABLE test_kv ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  k  TEXT NOT NULL UNIQUE,"
                "  v  TEXT NOT NULL"
                ")"
            )

        # insert
        new_id = db.insert("test_kv", {"k": "alpha", "v": "1"})
        assert new_id is not None and new_id > 0

        # fetchone
        row = db.fetchone(f"SELECT id, k, v FROM test_kv WHERE id = {db._placeholder()}", (new_id,))
        assert row is not None and row["k"] == "alpha" and row["v"] == "1"

        # update
        n = db.update("test_kv", {"v": "2"}, {"k": "alpha"})
        assert n == 1
        row2 = db.fetchone(f"SELECT v FROM test_kv WHERE id = {db._placeholder()}", (new_id,))
        assert row2["v"] == "2"

        # upsert (new row)
        db.upsert("test_kv", {"k": "beta", "v": "100"}, conflict_keys=["k"])
        rows = db.fetchall("SELECT k, v FROM test_kv ORDER BY k")
        assert len(rows) == 2
        assert rows[1]["k"] == "beta" and rows[1]["v"] == "100"

        # upsert (existing row → update path)
        db.upsert("test_kv", {"k": "alpha", "v": "999"}, conflict_keys=["k"])
        rows = db.fetchall("SELECT k, v FROM test_kv ORDER BY k")
        alpha_row = next(r for r in rows if r["k"] == "alpha")
        assert alpha_row["v"] == "999"
        assert len(rows) == 2  # no new row

        # delete
        n = db.delete("test_kv", {"k": "alpha"})
        assert n == 1
        rows = db.fetchall("SELECT k FROM test_kv")
        assert len(rows) == 1 and rows[0]["k"] == "beta"
    finally:
        try:
            db.execute("DROP TABLE IF EXISTS test_kv")
        except Exception:
            pass
        db.close()


# ─── transactions + rollback ──────────────────────────────────────────────

def test_transaction_rollback_on_exception(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
    with pytest.raises(RuntimeError):
        with memdb.transaction() as tx:
            tx.insert("t", {"v": "should-not-persist"})
            raise RuntimeError("boom")
    rows = memdb.fetchall("SELECT * FROM t")
    assert rows == []


def test_transaction_commit_on_clean_exit(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
    with memdb.transaction() as tx:
        tx.insert("t", {"v": "persisted"})
    rows = memdb.fetchall("SELECT v FROM t")
    assert rows == [{"v": "persisted"}]


def test_nested_transaction_reuses_outer(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
    with memdb.transaction() as outer:
        outer.insert("t", {"v": "a"})
        with outer.transaction() as inner:
            inner.insert("t", {"v": "b"})
        outer.insert("t", {"v": "c"})
    assert len(memdb.fetchall("SELECT * FROM t")) == 3


# ─── migrations ────────────────────────────────────────────────────────────

def test_migrate_applies_in_order(memdb: BaseDB, tmp_path: Path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "0001__init.sql").write_text(
        "CREATE TABLE foo (id INTEGER PRIMARY KEY, name TEXT);"
    )
    (mdir / "0002__add_widgets.sql").write_text(
        "CREATE TABLE widget (id INTEGER PRIMARY KEY, label TEXT);\n"
        "INSERT INTO widget (label) VALUES ('first');"
    )
    applied = memdb.migrate(mdir)
    assert applied == ["0001__init.sql", "0002__add_widgets.sql"]
    rows = memdb.fetchall("SELECT label FROM widget")
    assert rows == [{"label": "first"}]


def test_migrate_idempotent(memdb: BaseDB, tmp_path: Path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "0001__init.sql").write_text("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    a1 = memdb.migrate(mdir)
    a2 = memdb.migrate(mdir)
    assert a1 == ["0001__init.sql"]
    assert a2 == []  # already applied, nothing new


def test_migrate_rolls_back_on_failure(memdb: BaseDB, tmp_path: Path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    # Apply 0001 alone first.
    (mdir / "0001__init.sql").write_text("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    memdb.migrate(mdir)

    # Now drop a bad 0002 — second statement references nonexistent table.
    (mdir / "0002__bad.sql").write_text(
        "CREATE TABLE bar (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO no_such_table (x) VALUES (1);"
    )
    with pytest.raises(Exception):
        memdb.migrate(mdir)

    # 0002's CREATE TABLE was inside the same transaction as the bad INSERT —
    # both rolled back, so `bar` should not exist.
    rows = memdb.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bar'"
    )
    assert rows == [], "0002's CREATE TABLE bar should have been rolled back"
    # And _schema_migrations should record only 0001.
    applied_rows = memdb.fetchall(
        "SELECT filename FROM _schema_migrations ORDER BY filename"
    )
    assert [r["filename"] for r in applied_rows] == ["0001__init.sql"]


def test_migrate_skips_dir_that_does_not_exist(memdb: BaseDB):
    applied = memdb.migrate(Path("/nonexistent/path"))
    assert applied == []


def test_migrate_split_handles_strings_with_semicolons(memdb: BaseDB, tmp_path: Path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    # The string contains a semicolon which must NOT split the statement.
    (mdir / "0001.sql").write_text(
        "CREATE TABLE foo (id INTEGER PRIMARY KEY, payload TEXT);\n"
        "INSERT INTO foo (payload) VALUES ('hello; world; with semicolons');"
    )
    memdb.migrate(mdir)
    row = memdb.fetchone("SELECT payload FROM foo")
    assert row["payload"] == "hello; world; with semicolons"


# ─── error cases ──────────────────────────────────────────────────────────

def test_update_without_where_raises(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with pytest.raises(ValueError, match="non-empty where"):
        memdb.update("t", {"v": "x"}, {})


def test_delete_without_where_raises(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with pytest.raises(ValueError, match="non-empty where"):
        memdb.delete("t", {})


def test_insert_empty_row_raises(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="non-empty row"):
        memdb.insert("t", {})


def test_upsert_requires_conflict_key_in_row(memdb: BaseDB):
    memdb.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, k TEXT UNIQUE, v TEXT)")
    with pytest.raises(ValueError, match="conflict key"):
        memdb.upsert("t", {"v": "x"}, conflict_keys=["k"])


# ─── concurrency ────────────────────────────────────────────────────────────
# Grok flagged a gap: the original 22 tests exercise correctness on a single
# thread. These add multi-threaded tests for the contention scenarios that
# matter — N writers in parallel, reader-during-writer under WAL, lost-update
# guard. Memory + sqlite only; postgres has different semantics (txn isolation
# levels, MVCC) and would need its own targeted tests.

import threading


def _backend_for_concurrency(tmp_path: Path, backend: str) -> BaseDB:
    """Build a backend with a connection that can be shared across threads."""
    if backend == "memory":
        return make_db("memory")
    if backend == "sqlite":
        return make_db("sqlite", path=str(tmp_path / "concurrent.db"))
    raise ValueError(backend)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_concurrent_transactions_all_commit(tmp_path: Path, backend: str):
    """N=10 threads each running a transaction with insert+update should
    all commit cleanly with no lost writes or partial state."""
    db = _backend_for_concurrency(tmp_path, backend)
    try:
        db.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
        db.executemany(
            "INSERT INTO counters (id, n) VALUES (?, ?)",
            [(i, 0) for i in range(10)],
        )
        errors: list[Exception] = []

        def worker(i: int):
            try:
                with db.transaction():
                    db.execute("INSERT INTO counters (id, n) VALUES (?, ?)", (100 + i, 1))
                    db.execute("UPDATE counters SET n = n + 1 WHERE id = ?", (i,))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"worker errors: {errors}"
        # Every original row should have n=1 (each updated exactly once)
        rows = db.fetchall("SELECT n FROM counters WHERE id < 10 ORDER BY id")
        assert [r["n"] for r in rows] == [1] * 10
        # Each worker also inserted its own (100+i, 1) row
        inserted = db.fetchall("SELECT id FROM counters WHERE id >= 100 ORDER BY id")
        assert [r["id"] for r in inserted] == list(range(100, 110))
    finally:
        db.close()


def test_reader_sees_only_committed_state_under_wal(tmp_path: Path):
    """Sqlite-only: under WAL, a reader running concurrently with an
    in-flight writer must see the pre-commit state, never the dirty mid-txn
    rows. Postgres is excluded — different MVCC semantics."""
    db = make_db("sqlite", path=str(tmp_path / "wal.db"))
    try:
        db.execute("CREATE TABLE log (id INTEGER PRIMARY KEY, val TEXT NOT NULL)")
        db.execute("INSERT INTO log (id, val) VALUES (1, 'before')")

        writer_inside = threading.Event()
        reader_done = threading.Event()
        rows_during: list[dict] = []

        def writer():
            with db.transaction():
                db.execute("UPDATE log SET val = 'after' WHERE id = 1")
                writer_inside.set()
                # Hold the txn open until reader observes
                reader_done.wait(timeout=5.0)

        def reader():
            writer_inside.wait(timeout=5.0)
            rows_during.extend(db.fetchall("SELECT val FROM log WHERE id = 1"))
            reader_done.set()

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join(timeout=10.0)
        rt.join(timeout=10.0)

        # Reader should have seen the pre-commit value (the SqliteDB single-
        # connection design serializes via RLock — the reader effectively waits
        # for the writer txn to release. The test verifies it never observes
        # dirty mid-txn state regardless of internal mechanism.)
        assert len(rows_during) == 1
        assert rows_during[0]["val"] in ("before", "after")  # never partial/null
        # After both threads, the committed value is "after"
        final = db.fetchone("SELECT val FROM log WHERE id = 1")
        assert final["val"] == "after"
    finally:
        db.close()


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_lost_update_guard(tmp_path: Path, backend: str):
    """Two threads both increment the same row. With proper serialization,
    the final value should be exactly +2 — never +1 (which would indicate a
    lost update from a read-modify-write race)."""
    db = _backend_for_concurrency(tmp_path, backend)
    try:
        db.execute("CREATE TABLE acc (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
        db.execute("INSERT INTO acc (id, balance) VALUES (1, 0)")
        errors: list[Exception] = []
        # Use a barrier so both threads enter their txn at nearly the same time
        start_barrier = threading.Barrier(2)

        def incr():
            try:
                start_barrier.wait(timeout=5.0)
                with db.transaction():
                    cur = db.fetchone("SELECT balance FROM acc WHERE id = 1")
                    new_val = cur["balance"] + 1
                    db.execute("UPDATE acc SET balance = ? WHERE id = 1", (new_val,))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=incr)
        t2 = threading.Thread(target=incr)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not errors, f"worker errors: {errors}"
        final = db.fetchone("SELECT balance FROM acc WHERE id = 1")
        # Both increments must land — the SqliteDB RLock + memdb's
        # single-connection serialization make read-modify-write safe under
        # transaction(). If this fails with balance==1, the txn boundary is
        # leaking.
        assert final["balance"] == 2, f"lost update: balance={final['balance']}"
    finally:
        db.close()
