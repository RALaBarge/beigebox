"""Tests for UserRepo (BaseDB-backed user storage)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from beigebox.storage.db import make_db
from beigebox.storage.repos import make_user_repo


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = make_db("sqlite", path=path)
    r = make_user_repo(db)
    r.create_tables()
    yield r
    db.close()
    Path(path).unlink(missing_ok=True)


class TestUpsert:
    def test_insert_returns_uuid(self, repo):
        uid = repo.upsert(provider="github", sub="gh-1", email="a@b.com", name="A", picture="")
        assert uid
        assert len(uid) == 36  # UUID4 string length
        assert "-" in uid

    def test_second_upsert_returns_same_id(self, repo):
        uid1 = repo.upsert(provider="github", sub="gh-1", email="a@b.com", name="A", picture="")
        uid2 = repo.upsert(provider="github", sub="gh-1", email="c@d.com", name="C", picture="pic")
        assert uid1 == uid2

    def test_update_overwrites_email_name_picture(self, repo):
        uid = repo.upsert(provider="github", sub="gh-1", email="a@b.com", name="A", picture="")
        repo.upsert(provider="github", sub="gh-1", email="c@d.com", name="C", picture="newpic")
        rec = repo.get(uid)
        assert rec["email"] == "c@d.com"
        assert rec["name"] == "C"
        assert rec["picture"] == "newpic"

    def test_different_provider_or_sub_creates_new_row(self, repo):
        uid_a = repo.upsert(provider="github", sub="gh-1", email="a@b", name="A", picture="")
        uid_b = repo.upsert(provider="github", sub="gh-2", email="b@b", name="B", picture="")
        uid_c = repo.upsert(provider="google",  sub="gh-1", email="c@b", name="C", picture="")
        assert uid_a != uid_b
        assert uid_a != uid_c
        assert uid_b != uid_c


class TestGet:
    def test_get_known(self, repo):
        uid = repo.upsert(provider="github", sub="gh-1", email="a@b", name="A", picture="")
        rec = repo.get(uid)
        assert rec is not None
        assert rec["id"] == uid
        assert rec["provider"] == "github"
        assert rec["sub"] == "gh-1"

    def test_get_unknown_returns_none(self, repo):
        assert repo.get("does-not-exist") is None


class TestUpdatePassword:
    def test_update_password_returns_true(self, repo):
        uid = repo.upsert(provider="password", sub="admin", email="a@b", name="Admin", picture="")
        ok = repo.update_password(uid, "$2b$12$fakeBcryptHash")
        assert ok is True

        rec = repo.get(uid)
        assert rec["password_hash"] == "$2b$12$fakeBcryptHash"

    def test_update_password_unknown_user_silently_succeeds(self, repo):
        # UPDATE matching no rows is not an error; True is the right return.
        ok = repo.update_password("nonexistent", "hash")
        assert ok is True


class TestSchema:
    def test_create_tables_idempotent(self, repo):
        # Calling create_tables twice should not raise.
        repo.create_tables()
        repo.create_tables()
        # Confirm the table is queryable after re-running DDL.
        uid = repo.upsert(provider="github", sub="gh-1", email="a@b", name="A", picture="")
        assert repo.get(uid) is not None
