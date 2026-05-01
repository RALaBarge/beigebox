"""
Tests for ApiKeyRepo — the first per-entity repo on top of BaseDB.

Uses MemoryDB (in-process SQLite) so there are no on-disk side effects.
bcrypt rounds are kept at 4 in fixtures where we control key creation,
but the repo's default (12) is exercised in the integration test.
"""
from __future__ import annotations

import pytest

from beigebox.storage.db import make_db, BaseDB
from beigebox.storage.repos import make_api_key_repo, ApiKeyRepo


# ─── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def db() -> BaseDB:
    db = make_db("memory")
    # api_keys has no FK to users in the repo DDL (cross-entity dep omitted),
    # so we can set up just the api_keys table.
    yield db
    db.close()


@pytest.fixture
def repo(db: BaseDB) -> ApiKeyRepo:
    r = make_api_key_repo(db)
    r.create_tables()
    return r


# ─── basic CRUD ────────────────────────────────────────────────────────────

def test_create_returns_id_and_plain_key(repo: ApiKeyRepo):
    key_id, plain_key = repo.create("user-1", name="test")
    assert key_id
    assert plain_key
    assert len(plain_key) > 20  # token_urlsafe(32) → ~43 chars


def test_list_for_user_shows_created_key(repo: ApiKeyRepo):
    key_id, _ = repo.create("user-1", name="my-key")
    keys = repo.list_for_user("user-1")
    assert len(keys) == 1
    assert keys[0]["id"] == key_id
    assert keys[0]["name"] == "my-key"
    assert keys[0]["active"] == 1


def test_list_for_user_empty_for_unknown_user(repo: ApiKeyRepo):
    assert repo.list_for_user("nobody") == []


def test_list_for_user_returns_only_own_keys(repo: ApiKeyRepo):
    repo.create("user-a", name="a-key")
    repo.create("user-b", name="b-key")
    assert len(repo.list_for_user("user-a")) == 1
    assert len(repo.list_for_user("user-b")) == 1


# ─── revocation ────────────────────────────────────────────────────────────

def test_revoke_deactivates_key(repo: ApiKeyRepo):
    key_id, _ = repo.create("user-1")
    assert repo.revoke(key_id, "user-1") is True
    keys = repo.list_for_user("user-1")
    assert keys[0]["active"] == 0


def test_revoke_wrong_user_returns_false(repo: ApiKeyRepo):
    key_id, _ = repo.create("user-1")
    assert repo.revoke(key_id, "user-2") is False
    # Key still active
    assert repo.list_for_user("user-1")[0]["active"] == 1


def test_revoke_nonexistent_key_returns_false(repo: ApiKeyRepo):
    assert repo.revoke("no-such-key", "user-1") is False


# ─── verify (bcrypt round-trip) ────────────────────────────────────────────

def test_verify_valid_key_returns_user_id(repo: ApiKeyRepo):
    key_id, plain_key = repo.create("user-1")
    result = repo.verify(plain_key)
    assert result == "user-1"


def test_verify_wrong_key_returns_none(repo: ApiKeyRepo):
    repo.create("user-1")
    assert repo.verify("definitely-not-the-right-key") is None


def test_verify_revoked_key_returns_none(repo: ApiKeyRepo):
    key_id, plain_key = repo.create("user-1")
    repo.revoke(key_id, "user-1")
    assert repo.verify(plain_key) is None


def test_verify_updates_last_used(repo: ApiKeyRepo, db: BaseDB):
    key_id, plain_key = repo.create("user-1")
    # last_used should be NULL before first verify
    row = db.fetchone("SELECT last_used FROM api_keys WHERE id=?", (key_id,))
    assert row["last_used"] is None
    repo.verify(plain_key)
    row = db.fetchone("SELECT last_used FROM api_keys WHERE id=?", (key_id,))
    assert row["last_used"] is not None


# ─── factory ───────────────────────────────────────────────────────────────

def test_make_api_key_repo_returns_api_key_repo():
    db = make_db("memory")
    repo = make_api_key_repo(db)
    assert isinstance(repo, ApiKeyRepo)
    db.close()


def test_create_tables_is_idempotent(repo: ApiKeyRepo):
    # Calling twice should not raise
    repo.create_tables()
    repo.create_tables()
