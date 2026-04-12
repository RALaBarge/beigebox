"""
Memory Validator — high-level orchestrator for conversation integrity.

Coordinates signing, verification, quarantine, re-signing, and reporting
across the full conversation lifecycle.  Sits on top of the lower-level
ConversationIntegrityValidator (HMAC-SHA256 primitives) and KeyManager.

Usage:
    validator = MemoryValidator(config)
    # sign on write
    sig = validator.sign(message_dict, user_id)
    # verify on read
    result = validator.verify_conversation(conv_id, messages, user_id)
    # full audit
    report = validator.audit(conv_id, user_id, store)
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from beigebox.security.memory_integrity import (
    ConversationIntegrityValidator,
    IntegrityAuditLog,
)
from beigebox.security.key_management import KeyManager

logger = logging.getLogger(__name__)

# Fields included in the HMAC signature (must match _extract_signable_fields
# in sqlite_store.py).
SIGNABLE_FIELDS = frozenset({
    "id", "conversation_id", "role", "content", "model",
    "timestamp", "token_count",
})


class MemoryValidationResult:
    """Structured result from a validation pass."""

    __slots__ = (
        "valid", "tampered_entries", "unsigned_entries",
        "total_checked", "elapsed_ms", "integrity_report",
    )

    def __init__(
        self,
        valid: bool = True,
        tampered_entries: list[str] | None = None,
        unsigned_entries: list[str] | None = None,
        total_checked: int = 0,
        elapsed_ms: float = 0.0,
    ):
        self.valid = valid
        self.tampered_entries: list[str] = tampered_entries or []
        self.unsigned_entries: list[str] = unsigned_entries or []
        self.total_checked = total_checked
        self.elapsed_ms = elapsed_ms
        self.integrity_report = self._build_report()

    def _build_report(self) -> dict:
        return {
            "valid": self.valid,
            "tampered_entries": self.tampered_entries,
            "unsigned_entries": self.unsigned_entries,
            "total_checked": self.total_checked,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "tampered_count": len(self.tampered_entries),
            "unsigned_count": len(self.unsigned_entries),
        }

    def to_dict(self) -> dict:
        return self.integrity_report


class MemoryValidator:
    """
    High-level memory integrity orchestrator.

    Config keys (from security.memory_integrity):
        enabled         — master switch (default True)
        mode            — log_only | quarantine | strict
        key_source      — env | file | keyring
        key_path        — path for file-based key
        dev_mode        — if True, gracefully degrade when key missing
        quarantine_tampered — if True, mark tampered messages read-only
        alert_threshold — number of tampered msgs before escalation
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.enabled: bool = self.config.get("enabled", True)
        self.mode: str = self.config.get("mode", "log_only")
        self.quarantine_tampered: bool = self.config.get("quarantine_tampered", False)
        self.alert_threshold: int = self.config.get("alert_threshold", 5)
        self.dev_mode: bool = self.config.get("dev_mode", False)

        self._validator: Optional[ConversationIntegrityValidator] = None
        self._key: Optional[bytes] = None

        if not self.enabled:
            logger.info("MemoryValidator disabled by config")
            return

        self._init_key()

    # ------------------------------------------------------------------
    # Key management
    # ------------------------------------------------------------------

    def _init_key(self) -> None:
        """Load or generate the signing key."""
        key_source = self.config.get("key_source", "env")
        key_path = self.config.get("key_path", "~/.beigebox/memory.key")

        try:
            self._key = KeyManager.load_key(
                key_source=key_source,
                key_path=key_path,
                dev_mode=self.dev_mode,
            )
        except Exception as e:
            logger.error("MemoryValidator key load failed: %s", e)
            if not self.dev_mode:
                raise
            return

        if self._key is None:
            logger.warning("MemoryValidator: no key available (dev_mode=%s)", self.dev_mode)
            return

        self._validator = ConversationIntegrityValidator(self._key)
        logger.info(
            "MemoryValidator initialized (mode=%s, key_source=%s)",
            self.mode, key_source,
        )

    @property
    def is_active(self) -> bool:
        """True when validator is enabled and has a valid key."""
        return self.enabled and self._validator is not None

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign(self, message: dict, user_id: str) -> Optional[str]:
        """
        Sign a message dict, returning the HMAC-SHA256 hex digest.

        Returns None if validator is not active (disabled or no key).
        """
        if not self.is_active:
            return None
        signable = {k: v for k, v in message.items() if k in SIGNABLE_FIELDS}
        return self._validator.sign_message(signable, user_id)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_message(self, message: dict, user_id: str, stored_hmac: str) -> bool:
        """
        Verify a single message against its stored signature.

        Uses constant-time comparison to prevent timing attacks.
        Returns True if signature is valid.
        """
        if not self.is_active:
            return True  # pass-through when disabled
        signable = {k: v for k, v in message.items() if k in SIGNABLE_FIELDS}
        return self._validator.verify_message(signable, user_id, stored_hmac)

    def verify_conversation(
        self,
        conversation_id: str,
        messages: list[dict],
        user_id: str,
    ) -> MemoryValidationResult:
        """
        Verify all messages in a conversation.

        Returns a MemoryValidationResult with per-message details.
        Logs violations via IntegrityAuditLog and emits Tap events.
        """
        t0 = time.monotonic()

        if not self.is_active:
            return MemoryValidationResult(
                valid=True,
                total_checked=len(messages),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        tampered: list[str] = []
        unsigned: list[str] = []

        for msg in messages:
            msg_id = msg.get("id", "unknown")
            stored_sig = msg.get("message_hmac")

            if not stored_sig:
                unsigned.append(msg_id)
                continue

            if not self.verify_message(msg, user_id, stored_sig):
                tampered.append(msg_id)
                IntegrityAuditLog.log_violation(
                    conversation_id, msg_id, user_id,
                    "signature_mismatch", self.mode,
                )

        elapsed = (time.monotonic() - t0) * 1000

        valid = len(tampered) == 0
        result = MemoryValidationResult(
            valid=valid,
            tampered_entries=tampered,
            unsigned_entries=unsigned,
            total_checked=len(messages),
            elapsed_ms=elapsed,
        )

        # Log summary
        if tampered:
            logger.warning(
                "Conversation %s: %d tampered messages detected (mode=%s)",
                conversation_id, len(tampered), self.mode,
            )
            self._emit_tap_event(conversation_id, "integrity_violation", {
                "tampered_count": len(tampered),
                "tampered_ids": tampered[:10],
                "mode": self.mode,
            })
        else:
            IntegrityAuditLog.log_validation_pass(conversation_id, len(messages))

        # Check alert threshold
        if len(tampered) >= self.alert_threshold:
            logger.critical(
                "ALERT: Conversation %s exceeds tamper threshold (%d >= %d)",
                conversation_id, len(tampered), self.alert_threshold,
            )
            self._emit_tap_event(conversation_id, "integrity_alert", {
                "tampered_count": len(tampered),
                "threshold": self.alert_threshold,
                "mode": self.mode,
            })

        return result

    # ------------------------------------------------------------------
    # Audit (full session validation with optional re-signing)
    # ------------------------------------------------------------------

    def audit_conversation(
        self,
        conversation_id: str,
        messages: list[dict],
        user_id: str,
        quarantine: bool = False,
    ) -> dict:
        """
        Full audit of a conversation: verify + optional quarantine.

        Returns a comprehensive integrity report dict.
        """
        result = self.verify_conversation(conversation_id, messages, user_id)
        report = result.to_dict()
        report["conversation_id"] = conversation_id
        report["audited_at"] = datetime.now(timezone.utc).isoformat()
        report["mode"] = self.mode
        report["quarantine_applied"] = False

        if quarantine and result.tampered_entries:
            report["quarantine_applied"] = True
            report["quarantined_messages"] = result.tampered_entries

        return report

    def resign_conversation(
        self,
        messages: list[dict],
        user_id: str,
    ) -> list[tuple[str, str]]:
        """
        Re-sign all messages in a conversation (e.g., after key rotation).

        Returns list of (message_id, new_signature) tuples.
        """
        if not self.is_active:
            return []

        results = []
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id:
                continue
            new_sig = self.sign(msg, user_id)
            if new_sig:
                results.append((msg_id, new_sig))
        return results

    # ------------------------------------------------------------------
    # Tap event emission
    # ------------------------------------------------------------------

    def _emit_tap_event(
        self,
        conversation_id: str,
        event_type: str,
        meta: dict,
    ) -> None:
        """
        Emit a structured wire event for observability.

        Best-effort: failures here never propagate to the caller.
        """
        try:
            from beigebox.wiretap import log_event
            log_event(
                event_type=event_type,
                source="memory_validator",
                content=json.dumps(meta),
                conv_id=conversation_id,
                meta=meta,
            )
        except ImportError:
            # Wiretap not available (e.g., in tests)
            logger.debug("Tap event skipped (wiretap not available): %s", event_type)
        except Exception as e:
            logger.debug("Tap event emission failed: %s", e)
