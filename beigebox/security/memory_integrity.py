"""
Memory Integrity Validation for conversation poisoning prevention.

Implements HMAC-SHA256 signing and verification of conversation messages
to detect any tampering with stored data.

Algorithm:
- Canonical JSON representation (sorted keys, no whitespace)
- HMAC-SHA256 signature computed per-message
- Verification on read-time (100% detection of modifications)
- Performance budget: <5ms per conversation validation

Per-day subkey derivation:
  Each message is signed with an HKDF-derived subkey scoped to the date the
  message was signed. The signed input embeds that date so verification
  re-derives the same subkey from the master. This makes "rotate the
  current signing material daily" a one-line operational policy without
  re-signing history (verification keeps working as long as the master is
  available).

Hash-chained audit log:
  ``IntegrityAuditLog`` persists violation/pass events to a JSONL file
  where each entry references the previous entry's digest. An attacker
  who silently rewrites past events breaks the chain.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _derive_daily_subkey(master_key: bytes, signing_date: str) -> bytes:
    """HKDF-Expand: per-day subkey from a 32-byte master.

    Uses HMAC-SHA256 in a single-step expand (info = "bb-mem-integrity|<date>").
    Output is 32 bytes — same size as the master, fed back into HMAC-SHA256
    as the signing key.
    """
    info = f"bb-mem-integrity|{signing_date}".encode("utf-8")
    return hmac.new(master_key, info + b"\x01", hashlib.sha256).digest()


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

    def _signing_input(self, message: dict, user_id: str, signing_date: str) -> bytes:
        """Build the canonical bytes that the HMAC covers.

        Includes user_id, conversation_id, and signing_date so each is bound
        into the signature. ``signing_date`` is also stored on-row so
        verification can re-derive the subkey.
        """
        canonical = json.dumps(
            message,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        conv_id = message.get("conversation_id", "")
        return f"{user_id}|{conv_id}|{signing_date}|{canonical}".encode("utf-8")

    def sign_message(
        self,
        message: dict,
        user_id: str,
        signing_date: str | None = None,
    ) -> str:
        """
        Generate HMAC-SHA256 signature for a message using a daily subkey.

        Args:
            message: Message dict to sign (must contain conversation_id;
                ``signing_date`` is added if not already present)
            user_id: User ID — bound into signature to prevent cross-user spoofing
            signing_date: ISO date (YYYY-MM-DD). Defaults to today UTC.
                Stored back into ``message["signing_date"]`` for round-trip verify.

        Returns:
            Hex-encoded HMAC-SHA256 signature (64 characters)
        """
        if signing_date is None:
            signing_date = message.get("signing_date") or date.today().isoformat()
        message["signing_date"] = signing_date

        subkey = _derive_daily_subkey(self.secret_key, signing_date)
        h = hmac.new(subkey, self._signing_input(message, user_id, signing_date), hashlib.sha256)
        return h.hexdigest()

    def verify_message(self, message: dict, user_id: str, signature: str) -> bool:
        """
        Verify HMAC signature for a message.

        Two-path verification for backward compatibility:
          1. If ``message["signing_date"]`` is present, re-derive the daily
             subkey and verify against the date-bound signing input.
          2. If no signing_date is present (legacy unsigned-date entries),
             fall back to the master-key-only signing input.

        Constant-time comparison either way.
        """
        try:
            signing_date = message.get("signing_date")
            if signing_date:
                subkey = _derive_daily_subkey(self.secret_key, signing_date)
                expected = hmac.new(
                    subkey,
                    self._signing_input(message, user_id, signing_date),
                    hashlib.sha256,
                ).hexdigest()
                return hmac.compare_digest(expected, signature)

            # Legacy path: master key, no date binding.
            canonical = json.dumps(
                {k: v for k, v in message.items() if k != "signing_date"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            conv_id = message.get("conversation_id", "")
            legacy_input = f"{user_id}|{conv_id}|{canonical}".encode("utf-8")
            expected = hmac.new(self.secret_key, legacy_input, hashlib.sha256).hexdigest()
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


_AUDIT_CHAIN_LOCK = threading.Lock()
_AUDIT_CHAIN_PATH_DEFAULT = Path.home() / ".beigebox" / "integrity_audit.jsonl"
_GENESIS_DIGEST = "0" * 64


def _audit_path() -> Path:
    # SECURITY: BEIGEBOX_AUDIT_LOG is operator-trusted (process env, not a
    # request body). The defense is filesystem permissions on the chosen
    # path, not in-process validation. An attacker who controls process
    # env already has a foothold; SafePath would only block legitimate
    # operator choices like "/var/log/beigebox/audit.jsonl". If the threat
    # model expands to LLM-controlled env, revisit.
    override = os.environ.get("BEIGEBOX_AUDIT_LOG")
    return Path(override) if override else _AUDIT_CHAIN_PATH_DEFAULT


def _last_digest(path: Path) -> str:
    """Return the digest of the last entry in the chain, or the genesis digest."""
    if not path.exists():
        return _GENESIS_DIGEST
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return _GENESIS_DIGEST
            chunk = 4096
            f.seek(max(0, size - chunk))
            tail = f.read().splitlines()
        for line in reversed(tail):
            if not line.strip():
                continue
            entry = json.loads(line.decode("utf-8"))
            return entry.get("digest", _GENESIS_DIGEST)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Audit chain unreadable, restarting from genesis: %s", exc)
    return _GENESIS_DIGEST


_chattr_warned = False


def _try_make_append_only(path: Path) -> None:
    """Best-effort: mark the file append-only via ``chattr +a`` on ext-family fs.

    This requires CAP_LINUX_IMMUTABLE (typically root). When it works, even a
    process running as the operator user cannot rewrite or truncate prior
    entries — only append. Silently no-op when chattr isn't available, the fs
    doesn't support it, or we lack the capability. Logs once per run.
    """
    global _chattr_warned
    try:
        import shutil as _shutil
        chattr = _shutil.which("chattr")
        if not chattr:
            return
        import subprocess
        r = subprocess.run(
            [chattr, "+a", str(path)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if r.returncode != 0 and not _chattr_warned:
            _chattr_warned = True
            logger.info(
                "Audit log append-only attribute not set (chattr +a failed: %s). "
                "File mode 0600 still enforced; consider running BeigeBox under "
                "a process supervisor with CAP_LINUX_IMMUTABLE.",
                (r.stderr or r.stdout or "").strip()[:120],
            )
    except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: F821
        if not _chattr_warned:
            _chattr_warned = True
            logger.debug("chattr +a unavailable: %s", exc)


def _append_chain_entry(event: dict) -> str:
    """Append a JSONL entry chained to the previous digest. Returns the new digest."""
    path = _audit_path()
    is_new = not path.exists()
    with _AUDIT_CHAIN_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = _last_digest(path)
        seq = _next_seq(path)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": seq,
            "prev_digest": prev,
            **event,
        }
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        entry["digest"] = digest
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if is_new:
            _try_make_append_only(path)
        _update_anchor(seq, digest)
    return digest


def _next_seq(path: Path) -> int:
    """Return seq+1 of the last entry, or 0 if the chain is empty."""
    if not path.exists():
        return 0
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return 0
            chunk = 4096
            f.seek(max(0, size - chunk))
            tail = f.read().splitlines()
        for line in reversed(tail):
            if not line.strip():
                continue
            entry = json.loads(line.decode("utf-8"))
            return int(entry.get("seq", -1)) + 1
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return 0


# ── Anchor: separate file recording (last_seq, last_digest) ────────────────
# Defends against truncation: an attacker who deletes the tail of the chain
# and re-signs from there can't match the anchor without write access to it.
# Operators should park the anchor on a different filesystem / mount / device
# (e.g. a syslog tap, an immutable bucket, or a remote write-only sink) for
# the strongest guarantee. Default location is ~/.beigebox/integrity_anchor.json.

_ANCHOR_PATH_DEFAULT = Path.home() / ".beigebox" / "integrity_anchor.json"


def _anchor_path() -> Path:
    # SECURITY: BEIGEBOX_AUDIT_ANCHOR is operator-trusted; see _audit_path
    # for the rationale on not adding SafePath here.
    override = os.environ.get("BEIGEBOX_AUDIT_ANCHOR")
    return Path(override) if override else _ANCHOR_PATH_DEFAULT


def _update_anchor(seq: int, digest: str) -> None:
    """Update the anchor file to (seq, digest). Best-effort."""
    path = _anchor_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seq": seq, "digest": digest,
                       "updated_at": datetime.now(timezone.utc).isoformat()},
                      f, sort_keys=True)
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        logger.debug("Anchor update failed: %s", exc)


def _read_anchor() -> dict | None:
    path = _anchor_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def verify_audit_chain(path: Path | str | None = None) -> Tuple[bool, list[str]]:
    """Walk the chain end-to-end. Returns (ok, list of failure descriptions).

    Three checks:
      1. Each entry's digest matches sha256(canonical entry minus digest).
      2. Each entry's prev_digest equals the prior entry's digest.
      3. Each entry's seq is monotonic (prev+1).
      4. Final (seq, digest) matches the anchor file when present —
         this is what makes truncation visible. An attacker who deletes
         the tail and re-signs forward from a midpoint will not match the
         anchor's recorded seq/digest unless they also have write access
         to the anchor (which should live on a different mount/host).
    """
    p = Path(path) if path else _audit_path()
    issues: list[str] = []
    if not p.exists():
        anchor = _read_anchor()
        if anchor is not None:
            issues.append(
                f"chain missing but anchor records seq={anchor.get('seq')} "
                f"digest={(anchor.get('digest') or '')[:8]}... — possible truncation"
            )
            return False, issues
        return True, issues

    expected_prev = _GENESIS_DIGEST
    expected_seq = 0
    last_seq = -1
    last_digest = _GENESIS_DIGEST
    with open(p, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                issues.append(f"line {lineno}: malformed JSON ({exc})")
                return False, issues

            stored_digest = entry.pop("digest", None)
            stored_prev = entry.get("prev_digest")
            stored_seq = entry.get("seq")
            if stored_prev != expected_prev:
                issues.append(
                    f"line {lineno}: prev_digest mismatch "
                    f"(expected {expected_prev[:8]}..., got {(stored_prev or '')[:8]}...)"
                )
                return False, issues

            # Sequence check (skip for legacy entries that predate seq).
            if stored_seq is not None and stored_seq != expected_seq:
                issues.append(
                    f"line {lineno}: seq mismatch (expected {expected_seq}, got {stored_seq})"
                )
                return False, issues

            canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if recomputed != stored_digest:
                issues.append(f"line {lineno}: digest mismatch (entry tampered)")
                return False, issues
            expected_prev = stored_digest
            last_digest = stored_digest
            if stored_seq is not None:
                last_seq = stored_seq
                expected_seq = stored_seq + 1

    # Anchor cross-check
    anchor = _read_anchor()
    if anchor is not None:
        a_seq = anchor.get("seq")
        a_digest = anchor.get("digest")
        if a_seq is not None and last_seq >= 0 and a_seq != last_seq:
            issues.append(
                f"anchor seq mismatch: chain ends at seq={last_seq} but anchor "
                f"records seq={a_seq} — possible truncation or missed write"
            )
            return False, issues
        if a_digest and a_digest != last_digest:
            issues.append(
                f"anchor digest mismatch: chain tail {last_digest[:8]}... vs "
                f"anchor {(a_digest or '')[:8]}..."
            )
            return False, issues

    return True, issues


class IntegrityAuditLog:
    """
    Structured logging for integrity events with a hash-chained on-disk record.

    Two layers:
      1. Stdlib logger output (existing behavior — picked up by ops tooling).
      2. JSONL chain at ``~/.beigebox/integrity_audit.jsonl`` (override with
         ``BEIGEBOX_AUDIT_LOG`` env). Each entry references the prior entry's
         digest. Use :func:`verify_audit_chain` to detect tampering.
    """

    @staticmethod
    def log_violation(
        conversation_id: str,
        message_id: str,
        user_id: Optional[str],
        issue: str,
        mode: str = "log_only"
    ) -> None:
        logger.error(
            "Integrity violation detected: conv_id=%s msg_id=%s user_id=%s issue=%s mode=%s",
            conversation_id, message_id, user_id, issue, mode
        )
        try:
            _append_chain_entry({
                "kind": "violation",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "user_id": user_id,
                "issue": issue,
                "mode": mode,
            })
        except OSError as exc:
            logger.warning("Audit chain append failed: %s", exc)

    @staticmethod
    def log_validation_pass(conversation_id: str, message_count: int) -> None:
        logger.debug(
            "Conversation integrity verified: conv_id=%s message_count=%d",
            conversation_id, message_count,
        )
        try:
            _append_chain_entry({
                "kind": "pass",
                "conversation_id": conversation_id,
                "message_count": message_count,
            })
        except OSError as exc:
            logger.warning("Audit chain append failed: %s", exc)
