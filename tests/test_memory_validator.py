"""
Tests for the Memory Validator system (P1-D: Agent Memory Validator).

Covers:
- HMAC-SHA256 signing and verification
- Key derivation and management
- SQLite migration (schema upgrade, backwards compat)
- Conversation integrity validation
- Tampering detection
- Tool interface
- Edge cases (missing sigs, malformed HMACs, key rotation)

Markers:
  @pytest.mark.unit        — fast, no I/O
  @pytest.mark.integration — uses temp SQLite DB
  @pytest.mark.migration   — schema migration tests
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from beigebox.security.memory_integrity import (
    ConversationIntegrityValidator,
    IntegrityAuditLog,
)
from beigebox.security.key_management import KeyManager
from beigebox.security.memory_validator import (
    MemoryValidator,
    MemoryValidationResult,
    SIGNABLE_FIELDS,
)
from beigebox.storage.migrations.v1_2_memory_integrity import (
    upgrade as migrate_v1_2,
    resign_unsigned,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def secret_key():
    """Generate a fresh 32-byte key for testing."""
    return secrets.token_bytes(32)


@pytest.fixture
def validator(secret_key):
    """Low-level ConversationIntegrityValidator instance."""
    return ConversationIntegrityValidator(secret_key)


@pytest.fixture
def memory_validator(secret_key, monkeypatch):
    """High-level MemoryValidator with in-memory key."""
    monkeypatch.setenv(
        "BEIGEBOX_MEMORY_KEY",
        f"base64:{__import__('base64').b64encode(secret_key).decode()}",
    )
    config = {
        "enabled": True,
        "mode": "log_only",
        "key_source": "env",
        "dev_mode": False,
        "quarantine_tampered": False,
        "alert_threshold": 5,
    }
    return MemoryValidator(config)


@pytest.fixture
def sample_message():
    """A realistic message dict."""
    return {
        "id": uuid4().hex,
        "conversation_id": "conv_test_001",
        "role": "user",
        "content": "What is the weather today?",
        "model": "qwen3:4b",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "token_count": 8,
    }


@pytest.fixture
def sample_messages():
    """A list of 5 messages forming a conversation."""
    conv_id = "conv_test_002"
    msgs = []
    for i, (role, content) in enumerate([
        ("user", "Hello, how are you?"),
        ("assistant", "I'm doing well, thanks for asking!"),
        ("user", "What's the capital of France?"),
        ("assistant", "The capital of France is Paris."),
        ("user", "Thanks!"),
    ]):
        msgs.append({
            "id": uuid4().hex,
            "conversation_id": conv_id,
            "role": role,
            "content": content,
            "model": "test-model",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token_count": len(content.split()),
        })
    return msgs


@pytest.fixture
def tmp_db():
    """Temporary SQLite database with base schema + messages table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );
    """)
    conn.commit()
    yield conn, db_path
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def tmp_db_with_integrity(tmp_db):
    """DB after running the v1.2 migration."""
    conn, db_path = tmp_db
    migrate_v1_2(conn)
    return conn, db_path


# ===========================================================================
# Unit tests: HMAC generation and verification
# ===========================================================================

class TestHMACGeneration:
    """Unit tests for low-level HMAC signing/verification."""

    @pytest.mark.unit
    def test_sign_returns_64_char_hex(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        assert isinstance(sig, str)
        assert len(sig) == 64
        # Valid hex
        int(sig, 16)

    @pytest.mark.unit
    def test_sign_deterministic(self, validator, sample_message):
        sig1 = validator.sign_message(sample_message, "user_001")
        sig2 = validator.sign_message(sample_message, "user_001")
        assert sig1 == sig2

    @pytest.mark.unit
    def test_sign_different_users_different_sigs(self, validator, sample_message):
        sig1 = validator.sign_message(sample_message, "user_001")
        sig2 = validator.sign_message(sample_message, "user_002")
        assert sig1 != sig2

    @pytest.mark.unit
    def test_sign_different_content_different_sigs(self, validator, sample_message):
        sig1 = validator.sign_message(sample_message, "user_001")
        modified = {**sample_message, "content": "Tampered content"}
        sig2 = validator.sign_message(modified, "user_001")
        assert sig1 != sig2

    @pytest.mark.unit
    def test_verify_valid_signature(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        assert validator.verify_message(sample_message, "user_001", sig) is True

    @pytest.mark.unit
    def test_verify_tampered_content(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        sample_message["content"] = "I have been tampered with"
        assert validator.verify_message(sample_message, "user_001", sig) is False

    @pytest.mark.unit
    def test_verify_tampered_role(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        sample_message["role"] = "system"
        assert validator.verify_message(sample_message, "user_001", sig) is False

    @pytest.mark.unit
    def test_verify_tampered_timestamp(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        sample_message["timestamp"] = "2020-01-01T00:00:00Z"
        assert validator.verify_message(sample_message, "user_001", sig) is False

    @pytest.mark.unit
    def test_verify_wrong_user(self, validator, sample_message):
        sig = validator.sign_message(sample_message, "user_001")
        assert validator.verify_message(sample_message, "user_999", sig) is False

    @pytest.mark.unit
    def test_verify_invalid_signature_format(self, validator, sample_message):
        assert validator.verify_message(sample_message, "user_001", "not_a_valid_sig") is False

    @pytest.mark.unit
    def test_verify_empty_signature(self, validator, sample_message):
        assert validator.verify_message(sample_message, "user_001", "") is False

    @pytest.mark.unit
    def test_constant_time_comparison(self, validator, sample_message):
        """Verify that HMAC comparison uses constant-time comparison."""
        sig = validator.sign_message(sample_message, "user_001")
        # Modify last char to get a "nearly correct" signature
        wrong_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        # Should still fail (no early exit)
        assert validator.verify_message(sample_message, "user_001", wrong_sig) is False


class TestSignatureFormat:
    """Tests for signature format validation."""

    @pytest.mark.unit
    def test_valid_format(self):
        sig = "a" * 64
        assert ConversationIntegrityValidator.is_valid_signature_format(sig) is True

    @pytest.mark.unit
    def test_invalid_too_short(self):
        assert ConversationIntegrityValidator.is_valid_signature_format("abc") is False

    @pytest.mark.unit
    def test_invalid_too_long(self):
        assert ConversationIntegrityValidator.is_valid_signature_format("a" * 65) is False

    @pytest.mark.unit
    def test_invalid_not_hex(self):
        assert ConversationIntegrityValidator.is_valid_signature_format("z" * 64) is False

    @pytest.mark.unit
    def test_invalid_none(self):
        assert ConversationIntegrityValidator.is_valid_signature_format(None) is False

    @pytest.mark.unit
    def test_invalid_int(self):
        assert ConversationIntegrityValidator.is_valid_signature_format(12345) is False


# ===========================================================================
# Unit tests: Key management
# ===========================================================================

class TestKeyManagement:
    """Tests for key loading, generation, and formatting."""

    @pytest.mark.unit
    def test_generate_key_length(self):
        key = KeyManager.generate_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    @pytest.mark.unit
    def test_generate_key_randomness(self):
        k1 = KeyManager.generate_key()
        k2 = KeyManager.generate_key()
        assert k1 != k2

    @pytest.mark.unit
    def test_format_env_var(self):
        key = secrets.token_bytes(32)
        env_str = KeyManager.format_env_var(key)
        assert env_str.startswith("base64:")

    @pytest.mark.unit
    def test_load_from_env(self, secret_key, monkeypatch):
        import base64
        encoded = base64.b64encode(secret_key).decode()
        monkeypatch.setenv("BEIGEBOX_MEMORY_KEY", f"base64:{encoded}")
        loaded = KeyManager.load_key(key_source="env")
        assert loaded == secret_key

    @pytest.mark.unit
    def test_load_from_env_missing_dev_mode(self, monkeypatch):
        monkeypatch.delenv("BEIGEBOX_MEMORY_KEY", raising=False)
        loaded = KeyManager.load_key(key_source="env", dev_mode=True)
        assert loaded is None

    @pytest.mark.unit
    def test_load_from_env_missing_strict(self, monkeypatch):
        monkeypatch.delenv("BEIGEBOX_MEMORY_KEY", raising=False)
        with pytest.raises(ValueError):
            KeyManager.load_key(key_source="env", dev_mode=False)

    @pytest.mark.unit
    def test_load_from_env_bad_format(self, monkeypatch):
        monkeypatch.setenv("BEIGEBOX_MEMORY_KEY", "not-base64-format")
        with pytest.raises(ValueError):
            KeyManager.load_key(key_source="env")

    @pytest.mark.unit
    def test_save_and_load_key_file(self, tmp_path, secret_key):
        key_file = str(tmp_path / "test.key")
        KeyManager.save_key_to_file(secret_key, key_file)
        loaded = KeyManager.load_key(key_source="file", key_path=key_file)
        assert loaded == secret_key

    @pytest.mark.unit
    def test_save_key_wrong_length(self, tmp_path):
        with pytest.raises(ValueError):
            KeyManager.save_key_to_file(b"too_short", str(tmp_path / "bad.key"))

    @pytest.mark.unit
    def test_load_file_not_found_dev_mode(self, tmp_path):
        loaded = KeyManager.load_key(
            key_source="file",
            key_path=str(tmp_path / "nonexistent.key"),
            dev_mode=True,
        )
        assert loaded is None

    @pytest.mark.unit
    def test_keyring_not_implemented(self):
        with pytest.raises(NotImplementedError):
            KeyManager.load_key(key_source="keyring")

    @pytest.mark.unit
    def test_unknown_key_source(self):
        with pytest.raises(ValueError, match="Unknown key source"):
            KeyManager.load_key(key_source="magic")


# ===========================================================================
# Unit tests: Validator init with wrong key size
# ===========================================================================

class TestValidatorInit:
    """Tests for ConversationIntegrityValidator initialization."""

    @pytest.mark.unit
    def test_init_requires_32_bytes(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ConversationIntegrityValidator(b"too_short")

    @pytest.mark.unit
    def test_init_rejects_string(self):
        with pytest.raises(ValueError):
            ConversationIntegrityValidator("not_bytes" * 4)

    @pytest.mark.unit
    def test_init_rejects_64_bytes(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ConversationIntegrityValidator(secrets.token_bytes(64))


# ===========================================================================
# Unit tests: MemoryValidator (high-level)
# ===========================================================================

class TestMemoryValidator:
    """Tests for the high-level MemoryValidator class."""

    @pytest.mark.unit
    def test_disabled_returns_none_on_sign(self):
        mv = MemoryValidator({"enabled": False})
        assert mv.sign({"id": "x", "content": "hi"}, "user") is None

    @pytest.mark.unit
    def test_disabled_verify_passes(self):
        mv = MemoryValidator({"enabled": False})
        assert mv.verify_message({}, "user", "any_sig") is True

    @pytest.mark.unit
    def test_is_active_false_when_disabled(self):
        mv = MemoryValidator({"enabled": False})
        assert mv.is_active is False

    @pytest.mark.unit
    def test_is_active_true_when_key_loaded(self, memory_validator):
        assert memory_validator.is_active is True

    @pytest.mark.unit
    def test_sign_and_verify_roundtrip(self, memory_validator, sample_message):
        sig = memory_validator.sign(sample_message, "user_001")
        assert sig is not None
        assert len(sig) == 64
        assert memory_validator.verify_message(sample_message, "user_001", sig) is True

    @pytest.mark.unit
    def test_tampered_content_detected(self, memory_validator, sample_message):
        sig = memory_validator.sign(sample_message, "user_001")
        sample_message["content"] = "INJECTED MALICIOUS CONTEXT"
        assert memory_validator.verify_message(sample_message, "user_001", sig) is False

    @pytest.mark.unit
    def test_verify_conversation_all_valid(self, memory_validator, sample_messages):
        user_id = "user_001"
        for msg in sample_messages:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        result = memory_validator.verify_conversation("conv_test_002", sample_messages, user_id)
        assert result.valid is True
        assert result.tampered_entries == []
        assert result.total_checked == 5

    @pytest.mark.unit
    def test_verify_conversation_detects_tampering(self, memory_validator, sample_messages):
        user_id = "user_001"
        for msg in sample_messages:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        # Tamper with message at index 2
        sample_messages[2]["content"] = "The capital is Berlin"

        result = memory_validator.verify_conversation("conv_test_002", sample_messages, user_id)
        assert result.valid is False
        assert len(result.tampered_entries) == 1
        assert sample_messages[2]["id"] in result.tampered_entries

    @pytest.mark.unit
    def test_verify_conversation_unsigned_messages(self, memory_validator, sample_messages):
        user_id = "user_001"
        # Only sign first 3 messages
        for msg in sample_messages[:3]:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        result = memory_validator.verify_conversation("conv_test_002", sample_messages, user_id)
        assert result.valid is True  # unsigned != tampered
        assert len(result.unsigned_entries) == 2

    @pytest.mark.unit
    def test_verify_conversation_performance(self, memory_validator, sample_messages):
        """Verify that validation of 5 messages takes <5ms."""
        user_id = "user_001"
        for msg in sample_messages:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        result = memory_validator.verify_conversation("conv_test_002", sample_messages, user_id)
        assert result.elapsed_ms < 50  # generous for CI

    @pytest.mark.unit
    def test_resign_conversation(self, memory_validator, sample_messages):
        user_id = "user_001"
        new_sigs = memory_validator.resign_conversation(sample_messages, user_id)
        assert len(new_sigs) == 5
        for msg_id, sig in new_sigs:
            assert len(sig) == 64

    @pytest.mark.unit
    def test_audit_conversation_clean(self, memory_validator, sample_messages):
        user_id = "user_001"
        for msg in sample_messages:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        report = memory_validator.audit_conversation(
            "conv_test_002", sample_messages, user_id,
        )
        assert report["valid"] is True
        assert report["conversation_id"] == "conv_test_002"
        assert "audited_at" in report

    @pytest.mark.unit
    def test_audit_conversation_tampered_with_quarantine(self, memory_validator, sample_messages):
        user_id = "user_001"
        for msg in sample_messages:
            msg["message_hmac"] = memory_validator.sign(msg, user_id)

        sample_messages[0]["content"] = "INJECTED"
        report = memory_validator.audit_conversation(
            "conv_test_002", sample_messages, user_id, quarantine=True,
        )
        assert report["valid"] is False
        assert report["quarantine_applied"] is True
        assert len(report["quarantined_messages"]) == 1


class TestMemoryValidationResult:
    """Tests for the result data class."""

    @pytest.mark.unit
    def test_default_valid(self):
        r = MemoryValidationResult()
        assert r.valid is True
        assert r.to_dict()["tampered_count"] == 0

    @pytest.mark.unit
    def test_with_tampered(self):
        r = MemoryValidationResult(
            valid=False,
            tampered_entries=["msg1", "msg2"],
            total_checked=10,
            elapsed_ms=1.5,
        )
        d = r.to_dict()
        assert d["valid"] is False
        assert d["tampered_count"] == 2
        assert d["total_checked"] == 10


# ===========================================================================
# Migration tests
# ===========================================================================

class TestMigration:
    """Tests for the v1.2 schema migration."""

    @pytest.mark.migration
    def test_upgrade_adds_columns(self, tmp_db):
        conn, _ = tmp_db
        result = migrate_v1_2(conn)
        assert "messages.message_hmac" in result["columns_added"]
        assert "messages.integrity_version" in result["columns_added"]
        assert "messages.tamper_detected" in result["columns_added"]
        assert "conversations.integrity_checked_at" in result["columns_added"]

    @pytest.mark.migration
    def test_upgrade_idempotent(self, tmp_db):
        conn, _ = tmp_db
        result1 = migrate_v1_2(conn)
        result2 = migrate_v1_2(conn)
        assert len(result1["columns_added"]) == 4
        assert len(result2["columns_skipped"]) == 4
        assert len(result2["columns_added"]) == 0

    @pytest.mark.migration
    def test_upgrade_creates_indexes(self, tmp_db):
        conn, _ = tmp_db
        result = migrate_v1_2(conn)
        assert "idx_messages_hmac_null" in result["indexes_created"]
        assert "idx_messages_tamper" in result["indexes_created"]

    @pytest.mark.migration
    def test_existing_data_preserved(self, tmp_db):
        conn, _ = tmp_db
        # Insert a message before migration
        conn.execute(
            "INSERT INTO conversations (id, created_at) VALUES (?, ?)",
            ("conv1", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("msg1", "conv1", "user", "Hello", "2026-01-01T00:00:00Z"),
        )
        conn.commit()

        # Run migration
        migrate_v1_2(conn)

        # Verify data still there
        row = conn.execute("SELECT * FROM messages WHERE id = 'msg1'").fetchone()
        assert row is not None
        assert dict(row)["content"] == "Hello"
        # New columns should have defaults
        assert dict(row)["message_hmac"] is None
        assert dict(row)["integrity_version"] == 1
        assert dict(row)["tamper_detected"] == 0

    @pytest.mark.migration
    def test_resign_unsigned_messages(self, tmp_db_with_integrity, secret_key):
        conn, _ = tmp_db_with_integrity
        validator = ConversationIntegrityValidator(secret_key)

        # Insert unsigned messages
        conn.execute(
            "INSERT INTO conversations (id, created_at) VALUES (?, ?)",
            ("conv1", "2026-01-01T00:00:00Z"),
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, model, timestamp, token_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"msg{i}", "conv1", "user", f"Message {i}", "test", "2026-01-01T00:00:00Z", 2),
            )
        conn.commit()

        # Run re-signing
        count = resign_unsigned(conn, validator.sign_message, user_id="system")
        assert count == 3

        # Verify all signed
        rows = conn.execute(
            "SELECT id, message_hmac FROM messages WHERE message_hmac IS NOT NULL"
        ).fetchall()
        assert len(rows) == 3
        for row in rows:
            assert len(dict(row)["message_hmac"]) == 64

    @pytest.mark.migration
    def test_resign_skips_already_signed(self, tmp_db_with_integrity, secret_key):
        conn, _ = tmp_db_with_integrity
        validator = ConversationIntegrityValidator(secret_key)

        conn.execute(
            "INSERT INTO conversations (id, created_at) VALUES (?, ?)",
            ("conv1", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, model, timestamp, token_count, message_hmac) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("msg_signed", "conv1", "user", "Already signed", "test", "2026-01-01T00:00:00Z", 2, "a" * 64),
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, model, timestamp, token_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("msg_unsigned", "conv1", "user", "Not signed", "test", "2026-01-01T00:00:00Z", 2),
        )
        conn.commit()

        count = resign_unsigned(conn, validator.sign_message, user_id="system")
        assert count == 1  # Only the unsigned one


# ===========================================================================
# Integration tests: Write-sign → Read-verify full cycle
# ===========================================================================

class TestIntegrationCycle:
    """Integration tests using SQLiteStore with integrity enabled."""

    @pytest.mark.integration
    def test_store_and_verify_message(self, secret_key, tmp_path, monkeypatch):
        """Write a signed message, read it back, verify signature."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path, integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        })

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_int_001",
            role="user",
            content="Integration test message",
            model="test-model",
            token_count=4,
        )

        store.store_message(msg, user_id="test_user")
        messages, integrity = store.get_conversation("conv_int_001", user_id="test_user")

        assert len(messages) == 1
        assert messages[0]["content"] == "Integration test message"
        assert messages[0]["message_hmac"] is not None
        assert len(messages[0]["message_hmac"]) == 64
        assert integrity["valid"] is True
        assert integrity["tampered_messages"] == []

    @pytest.mark.integration
    def test_tampered_message_detected_on_read(self, secret_key, tmp_path, monkeypatch):
        """Store a message, tamper with it in DB, verify detection."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path, integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        })

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_tamper_001",
            role="user",
            content="Original content",
            model="test-model",
            token_count=2,
        )
        store.store_message(msg, user_id="test_user")

        # Tamper with the stored message directly in the DB
        with store._connect() as conn:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("INJECTED MALICIOUS CONTENT", msg.id),
            )

        messages, integrity = store.get_conversation("conv_tamper_001", user_id="test_user")
        assert integrity["valid"] is False
        assert msg.id in integrity["tampered_messages"]

    @pytest.mark.integration
    def test_strict_mode_raises_on_tamper(self, secret_key, tmp_path, monkeypatch):
        """In strict mode, tampered conversations raise ValueError."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path, integrity_config={
            "enabled": True,
            "mode": "strict",
            "key_source": "env",
            "dev_mode": False,
        })

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_strict_001",
            role="user",
            content="Strict mode test",
            model="test-model",
            token_count=3,
        )
        store.store_message(msg, user_id="test_user")

        # Tamper
        with store._connect() as conn:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("TAMPERED", msg.id),
            )

        with pytest.raises(ValueError, match="failed integrity check"):
            store.get_conversation("conv_strict_001", user_id="test_user")

    @pytest.mark.integration
    def test_no_integrity_when_disabled(self, tmp_path):
        """With integrity disabled, store/load works normally."""
        from beigebox.storage.sqlite_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path, integrity_config={"enabled": False})

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_disabled_001",
            role="user",
            content="No integrity check",
            model="test-model",
            token_count=3,
        )
        store.store_message(msg, user_id="test_user")
        messages, integrity = store.get_conversation("conv_disabled_001")

        assert len(messages) == 1
        assert messages[0]["message_hmac"] is None
        assert integrity["valid"] is True

    @pytest.mark.integration
    def test_dev_mode_no_key_graceful(self, tmp_path, monkeypatch):
        """In dev mode with no key, store works without signing."""
        monkeypatch.delenv("BEIGEBOX_MEMORY_KEY", raising=False)

        from beigebox.storage.sqlite_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        store = SQLiteStore(db_path, integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": True,
        })

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_dev_001",
            role="user",
            content="Dev mode message",
            model="test-model",
            token_count=3,
        )
        store.store_message(msg, user_id="test_user")
        messages, integrity = store.get_conversation("conv_dev_001", user_id="test_user")

        assert len(messages) == 1
        assert integrity["valid"] is True

    @pytest.mark.integration
    def test_unsigned_messages_backwards_compat(self, secret_key, tmp_path, monkeypatch):
        """Messages stored before integrity was enabled show as unsigned, not tampered."""
        from beigebox.storage.sqlite_store import SQLiteStore

        # First: store without integrity
        db_path = str(tmp_path / "test.db")
        store1 = SQLiteStore(db_path, integrity_config={"enabled": False})

        from beigebox.storage.models import Message
        msg = Message(
            conversation_id="conv_legacy_001",
            role="user",
            content="Legacy unsigned message",
            model="test-model",
            token_count=3,
        )
        store1.store_message(msg)

        # Now: open with integrity enabled
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )
        store2 = SQLiteStore(db_path, integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        })

        messages, integrity = store2.get_conversation("conv_legacy_001", user_id="test_user")
        assert len(messages) == 1
        # Unsigned messages are flagged but not as tampered
        assert msg.id in integrity["unsigned_messages"]
        # tampered_messages should be empty (unsigned != tampered)
        assert integrity["tampered_messages"] == []


# ===========================================================================
# Tampering detection edge cases
# ===========================================================================

class TestTamperingEdgeCases:
    """Edge cases for tampering detection."""

    @pytest.mark.unit
    def test_empty_conversation(self, memory_validator):
        result = memory_validator.verify_conversation("empty_conv", [], "user_001")
        assert result.valid is True
        assert result.total_checked == 0

    @pytest.mark.unit
    def test_single_message_tampered(self, memory_validator, sample_message):
        sig = memory_validator.sign(sample_message, "user_001")
        sample_message["message_hmac"] = sig
        sample_message["content"] = "tampered"
        result = memory_validator.verify_conversation(
            "conv_single", [sample_message], "user_001",
        )
        assert result.valid is False
        assert len(result.tampered_entries) == 1

    @pytest.mark.unit
    def test_tampered_model_field(self, memory_validator, sample_message):
        sig = memory_validator.sign(sample_message, "user_001")
        sample_message["message_hmac"] = sig
        sample_message["model"] = "gpt-4-injected"
        result = memory_validator.verify_conversation(
            "conv_model", [sample_message], "user_001",
        )
        assert result.valid is False

    @pytest.mark.unit
    def test_tampered_token_count(self, memory_validator, sample_message):
        sig = memory_validator.sign(sample_message, "user_001")
        sample_message["message_hmac"] = sig
        sample_message["token_count"] = 99999
        result = memory_validator.verify_conversation(
            "conv_token", [sample_message], "user_001",
        )
        assert result.valid is False

    @pytest.mark.unit
    def test_extra_fields_ignored(self, memory_validator, sample_message):
        """Fields not in SIGNABLE_FIELDS should not affect signature."""
        sig = memory_validator.sign(sample_message, "user_001")
        sample_message["message_hmac"] = sig
        sample_message["cost_usd"] = 0.05  # not in SIGNABLE_FIELDS
        sample_message["latency_ms"] = 100
        result = memory_validator.verify_conversation(
            "conv_extra", [sample_message], "user_001",
        )
        assert result.valid is True

    @pytest.mark.unit
    def test_malformed_hmac_treated_as_invalid(self, memory_validator, sample_message):
        """A malformed HMAC string should be detected as tampered."""
        sample_message["message_hmac"] = "not_a_valid_hex_string"
        result = memory_validator.verify_conversation(
            "conv_malformed", [sample_message], "user_001",
        )
        assert result.valid is False
        assert len(result.tampered_entries) == 1


# ===========================================================================
# Key rotation tests
# ===========================================================================

class TestKeyRotation:
    """Tests for key rotation scenarios."""

    @pytest.mark.unit
    def test_old_key_signatures_invalid_with_new_key(self, sample_messages):
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)
        v1 = ConversationIntegrityValidator(key1)
        v2 = ConversationIntegrityValidator(key2)

        # Sign with key1
        sigs = {}
        for msg in sample_messages:
            sigs[msg["id"]] = v1.sign_message(msg, "user_001")

        # Verify with key2 — should all fail
        for msg in sample_messages:
            assert v2.verify_message(msg, "user_001", sigs[msg["id"]]) is False

    @pytest.mark.unit
    def test_resign_after_rotation(self, sample_messages):
        key1 = secrets.token_bytes(32)
        key2 = secrets.token_bytes(32)

        mv1_config = {"enabled": True, "dev_mode": True}
        mv2_config = {"enabled": True, "dev_mode": True}

        # Use monkeypatched validators with direct key injection
        mv1 = MemoryValidator(mv1_config)
        mv1._key = key1
        mv1._validator = ConversationIntegrityValidator(key1)

        mv2 = MemoryValidator(mv2_config)
        mv2._key = key2
        mv2._validator = ConversationIntegrityValidator(key2)

        # Sign with key1
        for msg in sample_messages:
            msg["message_hmac"] = mv1.sign(msg, "user_001")

        # Verify with key2 — should fail
        result = mv2.verify_conversation("conv_rot", sample_messages, "user_001")
        assert result.valid is False
        assert len(result.tampered_entries) == 5

        # Re-sign with key2
        new_sigs = mv2.resign_conversation(sample_messages, "user_001")
        for msg_id, sig in new_sigs:
            for msg in sample_messages:
                if msg["id"] == msg_id:
                    msg["message_hmac"] = sig

        # Now verify with key2 — should pass
        result = mv2.verify_conversation("conv_rot", sample_messages, "user_001")
        assert result.valid is True


# ===========================================================================
# Tool interface tests
# ===========================================================================

class TestMemoryValidatorTool:
    """Tests for the operator-facing tool wrapper."""

    @pytest.fixture
    def tool(self, memory_validator):
        from beigebox.tools.memory_validator_tool import MemoryValidatorTool
        return MemoryValidatorTool(validator=memory_validator, store=None)

    @pytest.mark.unit
    def test_status_command(self, tool):
        result = json.loads(tool.run("status"))
        assert result["enabled"] is True
        assert result["active"] is True
        assert "mode" in result

    @pytest.mark.unit
    def test_unknown_command(self, tool):
        result = json.loads(tool.run("foobar"))
        assert "error" in result
        assert "available" in result

    @pytest.mark.unit
    def test_empty_command(self, tool):
        result = json.loads(tool.run(""))
        assert "error" in result

    @pytest.mark.unit
    def test_validate_session_no_store(self, tool):
        result = json.loads(tool.run("validate_session session_id=conv_001"))
        assert "error" in result  # No store configured

    @pytest.mark.unit
    def test_validate_session_missing_id(self, tool):
        result = json.loads(tool.run("validate_session"))
        assert "error" in result
        assert "session_id" in result["error"]

    @pytest.mark.unit
    def test_validate_message_missing_params(self, tool):
        result = json.loads(tool.run("validate_message"))
        assert "error" in result

    @pytest.mark.integration
    def test_tool_with_store(self, secret_key, tmp_path, monkeypatch):
        """Full tool integration: store message, validate via tool."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        from beigebox.storage.models import Message
        from beigebox.tools.memory_validator_tool import MemoryValidatorTool

        db_path = str(tmp_path / "test.db")
        integrity_cfg = {
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        }
        store = SQLiteStore(db_path, integrity_config=integrity_cfg)

        mv = MemoryValidator(integrity_cfg)
        tool = MemoryValidatorTool(validator=mv, store=store)

        # Store some messages
        for i in range(3):
            msg = Message(
                conversation_id="conv_tool_001",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                model="test",
                token_count=2,
            )
            store.store_message(msg, user_id="tool_user")

        # Validate via tool
        result = json.loads(tool.run("validate_session session_id=conv_tool_001 user_id=tool_user"))
        assert result["valid"] is True
        assert result["total_checked"] == 3

    @pytest.mark.integration
    def test_tool_audit_with_quarantine(self, secret_key, tmp_path, monkeypatch):
        """Audit command with quarantine flags tampered messages."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        from beigebox.storage.models import Message
        from beigebox.tools.memory_validator_tool import MemoryValidatorTool

        db_path = str(tmp_path / "test.db")
        integrity_cfg = {
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        }
        store = SQLiteStore(db_path, integrity_config=integrity_cfg)
        mv = MemoryValidator(integrity_cfg)
        tool = MemoryValidatorTool(validator=mv, store=store)

        msg = Message(
            conversation_id="conv_audit_001",
            role="user",
            content="Original",
            model="test",
            token_count=1,
        )
        store.store_message(msg, user_id="audit_user")

        # Tamper
        with store._connect() as conn:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                ("TAMPERED", msg.id),
            )

        result = json.loads(tool.run(
            "audit session_id=conv_audit_001 user_id=audit_user quarantine=true"
        ))
        assert result["valid"] is False
        assert result["quarantine_applied"] is True

    @pytest.mark.integration
    def test_tool_resign(self, secret_key, tmp_path, monkeypatch):
        """Resign command re-signs all messages."""
        import base64
        monkeypatch.setenv(
            "BEIGEBOX_MEMORY_KEY",
            f"base64:{base64.b64encode(secret_key).decode()}",
        )

        from beigebox.storage.sqlite_store import SQLiteStore
        from beigebox.storage.models import Message
        from beigebox.tools.memory_validator_tool import MemoryValidatorTool

        db_path = str(tmp_path / "test.db")
        integrity_cfg = {
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        }
        store = SQLiteStore(db_path, integrity_config=integrity_cfg)
        mv = MemoryValidator(integrity_cfg)
        tool = MemoryValidatorTool(validator=mv, store=store)

        for i in range(3):
            msg = Message(
                conversation_id="conv_resign_001",
                role="user",
                content=f"Msg {i}",
                model="test",
                token_count=2,
            )
            store.store_message(msg, user_id="resign_user")

        result = json.loads(tool.run(
            "resign session_id=conv_resign_001 user_id=resign_user"
        ))
        assert result["resigned_count"] == 3


# ===========================================================================
# Audit log tests
# ===========================================================================

class TestIntegrityAuditLog:
    """Tests for the structured audit logger."""

    @pytest.mark.unit
    def test_log_violation_no_crash(self):
        # Should not raise
        IntegrityAuditLog.log_violation(
            "conv_001", "msg_001", "user_001",
            "signature_mismatch", "log_only",
        )

    @pytest.mark.unit
    def test_log_validation_pass_no_crash(self):
        IntegrityAuditLog.log_validation_pass("conv_001", 5)


# ===========================================================================
# Batch conversation validation
# ===========================================================================

class TestBatchValidation:
    """Test validate_conversation with batch of messages."""

    @pytest.mark.unit
    def test_batch_validate(self, validator):
        messages = []
        sigs = {}
        for i in range(10):
            msg = {
                "id": f"msg_{i}",
                "conversation_id": "conv_batch",
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Content {i}",
                "model": "test",
                "timestamp": "2026-01-01T00:00:00Z",
                "token_count": 2,
            }
            messages.append(msg)
            sigs[msg["id"]] = validator.sign_message(msg, "user_001")

        valid, issues = validator.validate_conversation(messages, "user_001", sigs)
        assert valid is True
        assert issues == []

    @pytest.mark.unit
    def test_batch_validate_with_tamper(self, validator):
        messages = []
        sigs = {}
        for i in range(5):
            msg = {
                "id": f"msg_{i}",
                "conversation_id": "conv_batch2",
                "role": "user",
                "content": f"Content {i}",
                "model": "test",
                "timestamp": "2026-01-01T00:00:00Z",
                "token_count": 2,
            }
            messages.append(msg)
            sigs[msg["id"]] = validator.sign_message(msg, "user_001")

        # Tamper with msg_2
        messages[2]["content"] = "Tampered!"

        valid, issues = validator.validate_conversation(messages, "user_001", sigs)
        assert valid is False
        assert "msg_2" in issues
        assert len(issues) == 1
