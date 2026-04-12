"""
Memory Validator Tool — exposes conversation integrity checks to the operator.

Commands:
  validate_session session_id=<id> [user_id=<uid>]
      Validate all messages in a session/conversation.

  validate_range session_id=<id> start=<n> end=<n> [user_id=<uid>]
      Validate a slice of messages (0-indexed).

  validate_message session_id=<id> message_id=<mid> [user_id=<uid>]
      Validate a single message by ID.

  audit session_id=<id> [user_id=<uid>] [quarantine=true]
      Full audit with optional quarantine of tampered messages.

  resign session_id=<id> [user_id=<uid>]
      Re-sign all messages (use after key rotation).

  status
      Show whether memory integrity is active, mode, and key status.
"""

import json
import logging
from typing import Optional

from beigebox.security.memory_validator import MemoryValidator

logger = logging.getLogger(__name__)


class MemoryValidatorTool:
    """
    Operator-facing tool for memory integrity validation.

    Wraps MemoryValidator and provides a string-in/string-out interface
    compatible with the BeigeBox tool registry.
    """

    name = "memory_validator"
    description = (
        "Validate conversation integrity via HMAC-SHA256 signatures. "
        "Commands: "
        "validate_session session_id=<id> [user_id=<uid>] — verify all messages; "
        "validate_range session_id=<id> start=<n> end=<n> [user_id=<uid>] — verify slice; "
        "validate_message session_id=<id> message_id=<mid> [user_id=<uid>] — verify one; "
        "audit session_id=<id> [user_id=<uid>] [quarantine=true] — full audit report; "
        "resign session_id=<id> [user_id=<uid>] — re-sign after key rotation; "
        "status — show integrity system status."
    )

    def __init__(
        self,
        validator: MemoryValidator,
        store=None,
    ):
        """
        Args:
            validator: MemoryValidator instance
            store: SQLiteStore instance (needed for DB access)
        """
        self.validator = validator
        self.store = store

    def run(self, input_text: str) -> str:
        """Parse command from agent and execute."""
        try:
            parts = input_text.strip().split()
            if not parts:
                return json.dumps({
                    "error": "Empty command. Use: validate_session, validate_range, "
                             "validate_message, audit, resign, status"
                })

            command = parts[0].lower()
            kwargs = self._parse_kwargs(parts[1:])

            dispatch = {
                "validate_session": self._cmd_validate_session,
                "validate_range": self._cmd_validate_range,
                "validate_message": self._cmd_validate_message,
                "audit": self._cmd_audit,
                "resign": self._cmd_resign,
                "status": self._cmd_status,
            }

            handler = dispatch.get(command)
            if not handler:
                return json.dumps({
                    "error": f"Unknown command: {command}",
                    "available": list(dispatch.keys()),
                })

            result = handler(kwargs)
            return json.dumps(result, default=str)

        except Exception as e:
            logger.warning("MemoryValidatorTool error: %s", e)
            return json.dumps({"error": str(e)})

    @staticmethod
    def _parse_kwargs(parts: list[str]) -> dict:
        """Parse key=value pairs from command parts."""
        kwargs = {}
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                # Coerce booleans
                if value.lower() in ("true", "yes", "1"):
                    value = True
                elif value.lower() in ("false", "no", "0"):
                    value = False
                # Coerce integers
                elif value.isdigit():
                    value = int(value)
                kwargs[key] = value
        return kwargs

    def _get_messages(self, session_id: str, user_id: str | None = None) -> tuple[list[dict], str | None]:
        """
        Retrieve messages from store.

        Returns (messages, user_id) — user_id may come from the conversation record.
        """
        if not self.store:
            raise RuntimeError("No store configured — cannot access conversations")

        messages, integrity_status = self.store.get_conversation(session_id, user_id=user_id)
        return messages, user_id

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _cmd_validate_session(self, kwargs: dict) -> dict:
        session_id = kwargs.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        user_id = kwargs.get("user_id", "system")
        messages, _ = self._get_messages(session_id, user_id)

        if not messages:
            return {"error": f"No messages found for session {session_id}"}

        result = self.validator.verify_conversation(session_id, messages, user_id)
        return result.to_dict()

    def _cmd_validate_range(self, kwargs: dict) -> dict:
        session_id = kwargs.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        start = kwargs.get("start", 0)
        end = kwargs.get("end")
        user_id = kwargs.get("user_id", "system")

        messages, _ = self._get_messages(session_id, user_id)
        if not messages:
            return {"error": f"No messages found for session {session_id}"}

        # Apply range
        if end is not None:
            messages = messages[start:end + 1]
        else:
            messages = messages[start:]

        result = self.validator.verify_conversation(session_id, messages, user_id)
        report = result.to_dict()
        report["range"] = {"start": start, "end": end}
        return report

    def _cmd_validate_message(self, kwargs: dict) -> dict:
        session_id = kwargs.get("session_id")
        message_id = kwargs.get("message_id")
        if not session_id or not message_id:
            return {"error": "session_id and message_id are required"}

        user_id = kwargs.get("user_id", "system")
        messages, _ = self._get_messages(session_id, user_id)

        # Find the target message
        target = None
        for msg in messages:
            if msg.get("id") == message_id:
                target = msg
                break

        if not target:
            return {"error": f"Message {message_id} not found in session {session_id}"}

        stored_sig = target.get("message_hmac")
        if not stored_sig:
            return {
                "valid": False,
                "message_id": message_id,
                "issue": "missing_signature",
            }

        is_valid = self.validator.verify_message(target, user_id, stored_sig)
        return {
            "valid": is_valid,
            "message_id": message_id,
            "issue": None if is_valid else "signature_mismatch",
        }

    def _cmd_audit(self, kwargs: dict) -> dict:
        session_id = kwargs.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        user_id = kwargs.get("user_id", "system")
        quarantine = kwargs.get("quarantine", False)

        messages, _ = self._get_messages(session_id, user_id)
        if not messages:
            return {"error": f"No messages found for session {session_id}"}

        report = self.validator.audit_conversation(
            session_id, messages, user_id, quarantine=quarantine,
        )

        # If quarantine requested, mark tampered messages in DB
        if quarantine and report.get("quarantined_messages") and self.store:
            self._apply_quarantine(report["quarantined_messages"])

        # Update integrity_checked_at on the conversation
        if self.store:
            self._update_checked_at(session_id)

        return report

    def _cmd_resign(self, kwargs: dict) -> dict:
        session_id = kwargs.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        user_id = kwargs.get("user_id", "system")
        messages, _ = self._get_messages(session_id, user_id)

        if not messages:
            return {"error": f"No messages found for session {session_id}"}

        new_sigs = self.validator.resign_conversation(messages, user_id)

        # Update signatures in DB
        if self.store and new_sigs:
            self._update_signatures(new_sigs)

        return {
            "resigned_count": len(new_sigs),
            "session_id": session_id,
        }

    def _cmd_status(self, kwargs: dict) -> dict:
        return {
            "enabled": self.validator.enabled,
            "active": self.validator.is_active,
            "mode": self.validator.mode,
            "quarantine_tampered": self.validator.quarantine_tampered,
            "alert_threshold": self.validator.alert_threshold,
            "dev_mode": self.validator.dev_mode,
            "store_available": self.store is not None,
        }

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _apply_quarantine(self, message_ids: list[str]) -> None:
        """Mark tampered messages in the database."""
        if not self.store:
            return
        try:
            with self.store._connect() as conn:
                for msg_id in message_ids:
                    conn.execute(
                        "UPDATE messages SET tamper_detected = 1 WHERE id = ?",
                        (msg_id,),
                    )
            logger.info("Quarantined %d messages", len(message_ids))
        except Exception as e:
            logger.warning("Quarantine update failed: %s", e)

    def _update_signatures(self, sigs: list[tuple[str, str]]) -> None:
        """Batch-update HMAC signatures in the database."""
        if not self.store:
            return
        try:
            with self.store._connect() as conn:
                for msg_id, new_sig in sigs:
                    conn.execute(
                        "UPDATE messages SET message_hmac = ?, tamper_detected = 0 WHERE id = ?",
                        (new_sig, msg_id),
                    )
            logger.info("Updated %d message signatures", len(sigs))
        except Exception as e:
            logger.warning("Signature update failed: %s", e)

    def _update_checked_at(self, conversation_id: str) -> None:
        """Update integrity_checked_at timestamp on a conversation."""
        if not self.store:
            return
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE conversations SET integrity_checked_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
        except Exception as e:
            logger.warning("integrity_checked_at update failed: %s", e)
