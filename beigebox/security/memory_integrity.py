"""
Memory Integrity Validation for conversation poisoning prevention.

Implements HMAC-SHA256 signing and verification of conversation messages
to detect any tampering with stored data.

Algorithm:
- Canonical JSON representation (sorted keys, no whitespace)
- HMAC-SHA256 signature computed per-message
- Verification on read-time (100% detection of modifications)
- Performance budget: <5ms per conversation validation
"""

import hashlib
import hmac
import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ConversationIntegrityValidator:
    """
    Validates conversation integrity using HMAC-SHA256 signatures.

    Prevents conversation poisoning attacks by detecting any modification
    to stored messages (false context, role escalation, timeline manipulation).
    """

    def __init__(self, secret_key: bytes):
        """
        Initialize validator with signing key.

        Args:
            secret_key: 32-byte secret key for HMAC (typically from env or keyring)

        Raises:
            ValueError: If secret_key is not 32 bytes
        """
        if not isinstance(secret_key, bytes) or len(secret_key) != 32:
            raise ValueError("secret_key must be exactly 32 bytes")
        self.secret_key = secret_key

    def sign_message(self, message: dict, user_id: str) -> str:
        """
        Generate HMAC-SHA256 signature for a message.

        Uses canonical JSON representation to ensure order-independent hashing.
        Includes user_id to prevent cross-user message spoofing.

        Args:
            message: Message dict to sign (e.g., {id, role, content, model, timestamp})
            user_id: User ID (included in signature to prevent cross-user attacks)

        Returns:
            Hex-encoded HMAC-SHA256 signature (64 characters)
        """
        # Create canonical representation: sorted keys, no whitespace
        canonical = json.dumps(
            message,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True
        )

        # Include user_id and conversation_id in signature to prevent:
        # - Cross-user message injection
        # - Message theft across conversations
        conv_id = message.get("conversation_id", "")
        signing_input = f"{user_id}|{conv_id}|{canonical}"

        # HMAC-SHA256
        h = hmac.new(self.secret_key, signing_input.encode("utf-8"), hashlib.sha256)
        return h.hexdigest()

    def verify_message(self, message: dict, user_id: str, signature: str) -> bool:
        """
        Verify HMAC signature for a message.

        Compares stored signature against freshly-computed signature.
        Uses constant-time comparison to prevent timing attacks.

        Args:
            message: Message dict to verify
            user_id: User ID that owns the conversation
            signature: Stored signature to check against

        Returns:
            True if signature matches, False otherwise
        """
        try:
            expected = self.sign_message(message, user_id)
            # Use hmac.compare_digest for constant-time comparison
            return hmac.compare_digest(expected, signature)
        except Exception as e:
            logger.warning("Signature verification failed: %s", e)
            return False

    def validate_conversation(self, messages: list[dict], user_id: str,
                             signatures: dict[str, str]) -> Tuple[bool, list[str]]:
        """
        Validate all messages in a conversation.

        Performs batch verification on entire conversation.
        Returns detailed list of any tampering detected.

        Args:
            messages: List of message dicts from database
            user_id: User ID for the conversation
            signatures: Dict mapping message_id → HMAC signature

        Returns:
            Tuple of (valid: bool, issues: list[str])
            - valid: True if all signatures match, False if any mismatch
            - issues: List of message IDs with invalid signatures, or []
        """
        issues = []

        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id:
                logger.warning("Message missing ID field")
                issues.append("unknown_no_id")
                continue

            stored_sig = signatures.get(msg_id)
            if not stored_sig:
                # No signature stored (backward compatibility: unsigned messages are suspect)
                logger.warning("Message %s has no stored signature", msg_id)
                issues.append(msg_id)
                continue

            if not self.verify_message(msg, user_id, stored_sig):
                logger.warning("Message %s failed integrity check", msg_id)
                issues.append(msg_id)

        return len(issues) == 0, issues

    @staticmethod
    def is_valid_signature_format(sig: str) -> bool:
        """
        Quick validation that a stored signature is in expected format.

        Args:
            sig: Claimed HMAC-SHA256 signature

        Returns:
            True if it's a 64-character hex string (SHA256 output)
        """
        if not isinstance(sig, str):
            return False
        # SHA256 produces 32 bytes = 64 hex chars
        if len(sig) != 64:
            return False
        try:
            int(sig, 16)  # Check it's valid hex
            return True
        except ValueError:
            return False


class IntegrityAuditLog:
    """
    Structured logging for integrity violations.

    Emits Tap events for monitoring and alerting on tamper detection.
    """

    @staticmethod
    def log_violation(
        conversation_id: str,
        message_id: str,
        user_id: Optional[str],
        issue: str,
        mode: str = "log_only"
    ) -> None:
        """
        Log an integrity violation.

        Args:
            conversation_id: Which conversation was affected
            message_id: Which message failed verification
            user_id: Which user owns the conversation
            issue: Brief description (e.g., "signature_mismatch", "missing_signature")
            mode: How violation is handled (log_only, quarantine, strict)
        """
        logger.error(
            "Integrity violation detected: conv_id=%s msg_id=%s user_id=%s issue=%s mode=%s",
            conversation_id, message_id, user_id, issue, mode
        )

    @staticmethod
    def log_validation_pass(conversation_id: str, message_count: int) -> None:
        """Log successful validation of a conversation."""
        logger.debug(
            "Conversation integrity verified: conv_id=%s message_count=%d",
            conversation_id, message_count
        )
