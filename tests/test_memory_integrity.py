"""
Tests for memory integrity validation (HMAC-SHA256 signatures).

Tests cover:
- HMAC generation and verification
- Signature mismatch detection
- Key rotation scenarios
- Integration with SQLiteStore
- Backward compatibility with unsigned messages
- Performance (< 5ms per conversation validation)
"""

import pytest
import time
import secrets
from pathlib import Path

from beigebox.security.memory_integrity import ConversationIntegrityValidator, IntegrityAuditLog
from beigebox.security.key_management import KeyManager
from beigebox.storage.sqlite_store import SQLiteStore
from beigebox.storage.models import Message


class TestConversationIntegrityValidator:
    """Unit tests for ConversationIntegrityValidator."""

    @pytest.fixture
    def validator(self):
        """Create a validator with test key."""
        key = secrets.token_bytes(32)
        return ConversationIntegrityValidator(key)

    @pytest.fixture
    def test_message(self):
        """Create a test message dict."""
        return {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "role": "user",
            "content": "Hello, world!",
            "model": "gpt-4",
            "timestamp": "2025-04-12T10:00:00Z",
            "token_count": 5,
        }

    def test_init_valid_key(self):
        """Test validator initialization with valid 32-byte key."""
        key = secrets.token_bytes(32)
        validator = ConversationIntegrityValidator(key)
        assert validator.secret_key == key

    def test_init_invalid_key_size(self):
        """Test validator initialization rejects wrong key size."""
        with pytest.raises(ValueError, match="32 bytes"):
            ConversationIntegrityValidator(b"too_short")

    def test_init_invalid_key_type(self):
        """Test validator initialization rejects non-bytes keys."""
        with pytest.raises(ValueError, match="32 bytes"):
            ConversationIntegrityValidator("not_bytes")

    def test_sign_message(self, validator, test_message):
        """Test HMAC signature generation."""
        sig = validator.sign_message(test_message, user_id="user_1")
        # SHA256 produces 64-char hex string
        assert isinstance(sig, str)
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_message_deterministic(self, validator, test_message):
        """Test that signing same message produces same signature."""
        sig1 = validator.sign_message(test_message, user_id="user_1")
        sig2 = validator.sign_message(test_message, user_id="user_1")
        assert sig1 == sig2

    def test_sign_message_order_independent(self, validator):
        """Test that message key order doesn't affect signature."""
        msg1 = {
            "id": "msg_1",
            "role": "user",
            "content": "test",
            "conversation_id": "conv_1",
        }
        msg2 = {
            "conversation_id": "conv_1",
            "content": "test",
            "id": "msg_1",
            "role": "user",
        }
        sig1 = validator.sign_message(msg1, user_id="user_1")
        sig2 = validator.sign_message(msg2, user_id="user_1")
        assert sig1 == sig2

    def test_sign_message_content_sensitive(self, validator, test_message):
        """Test that changing content changes signature."""
        sig1 = validator.sign_message(test_message, user_id="user_1")

        msg_modified = test_message.copy()
        msg_modified["content"] = "Goodbye!"
        sig2 = validator.sign_message(msg_modified, user_id="user_1")

        assert sig1 != sig2

    def test_sign_message_user_sensitive(self, validator, test_message):
        """Test that signature depends on user_id."""
        sig1 = validator.sign_message(test_message, user_id="user_1")
        sig2 = validator.sign_message(test_message, user_id="user_2")
        assert sig1 != sig2

    def test_verify_message_valid(self, validator, test_message):
        """Test signature verification with valid signature."""
        sig = validator.sign_message(test_message, user_id="user_1")
        assert validator.verify_message(test_message, user_id="user_1", signature=sig) is True

    def test_verify_message_invalid_signature(self, validator, test_message):
        """Test signature verification fails with wrong signature."""
        invalid_sig = "0" * 64  # All zeros
        assert validator.verify_message(test_message, user_id="user_1", signature=invalid_sig) is False

    def test_verify_message_wrong_user(self, validator, test_message):
        """Test signature verification fails with wrong user_id."""
        sig = validator.sign_message(test_message, user_id="user_1")
        assert validator.verify_message(test_message, user_id="user_2", signature=sig) is False

    def test_verify_message_modified_content(self, validator, test_message):
        """Test signature verification fails after message modification."""
        sig = validator.sign_message(test_message, user_id="user_1")

        msg_modified = test_message.copy()
        msg_modified["content"] = "Modified!"
        assert validator.verify_message(msg_modified, user_id="user_1", signature=sig) is False

    def test_validate_conversation_empty(self, validator):
        """Test validation of empty conversation."""
        valid, issues = validator.validate_conversation([], user_id="user_1", signatures={})
        assert valid is True
        assert issues == []

    def test_validate_conversation_all_valid(self, validator):
        """Test validation of conversation with all valid signatures."""
        msg1 = {"id": "msg_1", "content": "test1"}
        msg2 = {"id": "msg_2", "content": "test2"}

        sig1 = validator.sign_message(msg1, user_id="user_1")
        sig2 = validator.sign_message(msg2, user_id="user_1")

        valid, issues = validator.validate_conversation(
            [msg1, msg2],
            user_id="user_1",
            signatures={"msg_1": sig1, "msg_2": sig2}
        )
        assert valid is True
        assert issues == []

    def test_validate_conversation_missing_signature(self, validator):
        """Test validation detects missing signatures."""
        msg1 = {"id": "msg_1", "content": "test1"}
        msg2 = {"id": "msg_2", "content": "test2"}

        sig1 = validator.sign_message(msg1, user_id="user_1")

        valid, issues = validator.validate_conversation(
            [msg1, msg2],
            user_id="user_1",
            signatures={"msg_1": sig1}  # msg2 signature missing
        )
        assert valid is False
        assert "msg_2" in issues

    def test_validate_conversation_tampering(self, validator):
        """Test validation detects message tampering."""
        msg1 = {"id": "msg_1", "content": "original"}
        sig = validator.sign_message(msg1, user_id="user_1")

        # Tamper with message
        msg1_tampered = msg1.copy()
        msg1_tampered["content"] = "tampered"

        valid, issues = validator.validate_conversation(
            [msg1_tampered],
            user_id="user_1",
            signatures={"msg_1": sig}
        )
        assert valid is False
        assert "msg_1" in issues

    def test_is_valid_signature_format(self):
        """Test signature format validation."""
        valid_sig = "a" * 64
        assert ConversationIntegrityValidator.is_valid_signature_format(valid_sig) is True

        assert ConversationIntegrityValidator.is_valid_signature_format("too_short") is False
        assert ConversationIntegrityValidator.is_valid_signature_format("x" * 64) is False  # Not hex
        assert ConversationIntegrityValidator.is_valid_signature_format(123) is False  # Not string


class TestKeyManager:
    """Tests for key management."""

    def test_generate_key(self):
        """Test key generation produces 32-byte key."""
        key = KeyManager.generate_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_generate_key_unique(self):
        """Test key generation produces different keys each time."""
        key1 = KeyManager.generate_key()
        key2 = KeyManager.generate_key()
        assert key1 != key2

    def test_format_env_var(self):
        """Test environment variable formatting."""
        key = secrets.token_bytes(32)
        env_var = KeyManager.format_env_var(key)
        assert env_var.startswith("base64:")
        assert len(env_var) == 7 + 44  # "base64:" (7) + base64(32 bytes) (44)

    def test_format_env_var_invalid_key(self):
        """Test environment variable formatting rejects invalid keys."""
        with pytest.raises(ValueError, match="32 bytes"):
            KeyManager.format_env_var(b"too_short")

    def test_save_key_to_file(self, tmp_path):
        """Test saving key to file."""
        key = secrets.token_bytes(32)
        key_file = tmp_path / "memory.key"

        KeyManager.save_key_to_file(key, str(key_file))

        # Verify file exists
        assert key_file.exists()

        # Verify content is hex
        with open(key_file) as f:
            content = f.read()
        assert content == key.hex()

        # Verify permissions are 0600
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_load_from_file(self, tmp_path):
        """Test loading key from file."""
        key_original = secrets.token_bytes(32)
        key_file = tmp_path / "memory.key"

        KeyManager.save_key_to_file(key_original, str(key_file))
        key_loaded = KeyManager._load_from_file(str(key_file))

        assert key_loaded == key_original

    def test_load_from_file_missing(self):
        """Test loading from missing file fails."""
        with pytest.raises(ValueError, match="not found"):
            KeyManager._load_from_file("/nonexistent/path", dev_mode=False)

    def test_load_from_file_dev_mode(self):
        """Test loading from missing file returns None in dev mode."""
        key = KeyManager._load_from_file("/nonexistent/path", dev_mode=True)
        assert key is None

    def test_load_key_env(self, monkeypatch, tmp_path):
        """Test loading key from environment variable."""
        key_original = secrets.token_bytes(32)
        env_var = KeyManager.format_env_var(key_original)

        monkeypatch.setenv("BEIGEBOX_MEMORY_KEY", env_var)
        key_loaded = KeyManager.load_key(key_source="env")

        assert key_loaded == key_original

    def test_load_key_file(self, tmp_path):
        """Test loading key from file."""
        key_original = secrets.token_bytes(32)
        key_file = tmp_path / "memory.key"

        KeyManager.save_key_to_file(key_original, str(key_file))
        key_loaded = KeyManager.load_key(key_source="file", key_path=str(key_file))

        assert key_loaded == key_original


@pytest.mark.integration
class TestSQLiteStoreIntegrity:
    """Integration tests for SQLiteStore with integrity validation."""

    @pytest.fixture
    def store_with_integrity(self, tmp_path):
        """Create SQLiteStore with integrity enabled."""
        key = secrets.token_bytes(32)
        key_hex = key.hex()

        # Mock the key loading
        import os
        os.environ["BEIGEBOX_MEMORY_KEY"] = f"base64:{KeyManager.format_env_var(key).split(':')[1]}"

        db_path = tmp_path / "test.db"
        config = {
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
            "dev_mode": False,
        }
        store = SQLiteStore(str(db_path), integrity_config=config)
        return store

    def test_store_with_integrity_enabled(self, store_with_integrity):
        """Test that store initializes with integrity validator."""
        assert store_with_integrity.integrity_validator is not None
        assert store_with_integrity.integrity_mode == "log_only"

    def test_store_message_with_signature(self, store_with_integrity):
        """Test storing message with HMAC signature."""
        msg = Message(
            conversation_id="conv_1",
            role="user",
            content="Test message",
            model="gpt-4",
        )

        store_with_integrity.store_message(msg, user_id="user_1")

        # Retrieve message
        messages, integrity = store_with_integrity.get_conversation("conv_1", user_id="user_1")

        assert len(messages) == 1
        assert messages[0]["id"] == msg.id
        assert messages[0]["message_hmac"] is not None
        assert integrity["valid"] is True

    def test_get_conversation_tamper_detection(self, store_with_integrity):
        """Test that tampering is detected on read."""
        msg = Message(
            conversation_id="conv_1",
            role="user",
            content="Original content",
            model="gpt-4",
        )

        store_with_integrity.store_message(msg, user_id="user_1")

        # Manually tamper with database
        import sqlite3
        conn = sqlite3.connect(store_with_integrity.db_path)
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("Tampered content", msg.id)
        )
        conn.commit()
        conn.close()

        # Retrieve and verify tampering is detected
        messages, integrity = store_with_integrity.get_conversation("conv_1", user_id="user_1")

        assert integrity["valid"] is False
        assert msg.id in integrity["tampered_messages"]

    def test_get_conversation_integrity_status(self, store_with_integrity):
        """Test integrity status returned from get_conversation."""
        msg1 = Message(conversation_id="conv_1", role="user", content="msg1", model="gpt-4")
        msg2 = Message(conversation_id="conv_1", role="assistant", content="msg2", model="gpt-4")

        store_with_integrity.store_message(msg1, user_id="user_1")
        store_with_integrity.store_message(msg2, user_id="user_1")

        messages, integrity = store_with_integrity.get_conversation("conv_1", user_id="user_1")

        assert len(messages) == 2
        assert integrity["valid"] is True
        assert integrity["tampered_messages"] == []
        assert integrity["unsigned_messages"] == []

    @pytest.mark.slow
    def test_integrity_validation_performance(self, store_with_integrity):
        """Test integrity validation completes in <5ms per conversation."""
        # Create conversation with 10 messages
        for i in range(10):
            msg = Message(
                conversation_id="perf_test",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
                model="gpt-4",
            )
            store_with_integrity.store_message(msg, user_id="user_1")

        # Time the validation
        start = time.perf_counter()
        messages, integrity = store_with_integrity.get_conversation("perf_test", user_id="user_1")
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 5, f"Validation took {elapsed_ms}ms (budget: 5ms)"
        assert integrity["valid"] is True


@pytest.mark.unit
class TestBackwardCompatibility:
    """Tests for backward compatibility with unsigned messages."""

    @pytest.fixture
    def store_no_integrity(self, tmp_path):
        """Create SQLiteStore without integrity validation."""
        db_path = tmp_path / "test.db"
        store = SQLiteStore(str(db_path), integrity_config={"enabled": False})
        return store

    def test_store_unsigned_message(self, store_no_integrity):
        """Test storing message without signature (backward compatibility)."""
        msg = Message(
            conversation_id="conv_1",
            role="user",
            content="Test message",
            model="gpt-4",
        )

        # Store without user_id (no signature)
        store_no_integrity.store_message(msg)

        messages, _ = store_no_integrity.get_conversation("conv_1")

        assert len(messages) == 1
        assert messages[0]["message_hmac"] is None

    def test_store_mixed_signed_unsigned(self, tmp_path):
        """Test conversation with mix of signed and unsigned messages."""
        # First store without integrity
        db_path = tmp_path / "test.db"
        store_unsigned = SQLiteStore(str(db_path), integrity_config={"enabled": False})

        msg1 = Message(conversation_id="conv_1", role="user", content="unsigned", model="gpt-4")
        store_unsigned.store_message(msg1)

        # Then enable integrity and store another message
        key = secrets.token_bytes(32)
        import os
        os.environ["BEIGEBOX_MEMORY_KEY"] = f"base64:{KeyManager.format_env_var(key).split(':')[1]}"

        store_signed = SQLiteStore(str(db_path), integrity_config={
            "enabled": True,
            "mode": "log_only",
            "key_source": "env",
        })

        msg2 = Message(conversation_id="conv_1", role="assistant", content="signed", model="gpt-4")
        store_signed.store_message(msg2, user_id="user_1")

        # Retrieve and check status
        messages, integrity = store_signed.get_conversation("conv_1", user_id="user_1")

        assert len(messages) == 2
        # msg1 is unsigned, msg2 is signed
        assert msg1.id in integrity["unsigned_messages"]
        # Conversation is not fully valid due to unsigned messages
        assert integrity["valid"] is False
