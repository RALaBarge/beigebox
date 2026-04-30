"""MemoryDB — in-process SQLite for tests + ephemeral workloads."""
from __future__ import annotations

from beigebox.storage.db.sqlite import SqliteDB


class MemoryDB(SqliteDB):
    """In-memory SQLite. Same surface as :class:`SqliteDB`; lives only for the
    lifetime of the process.

    Useful for unit tests (no on-disk side effects) and for ephemeral runs
    (CI, one-shot scripts) where persistence isn't wanted.

    Each instance is its own isolated database; data is not shared between
    ``MemoryDB()`` instances even within the same process.
    """

    def __init__(self) -> None:
        # ``:memory:`` gives every connection a private database. WAL mode
        # doesn't apply to in-memory; foreign_keys ON is still useful for
        # parity with the on-disk impl.
        super().__init__(":memory:", wal=False, foreign_keys=True)
