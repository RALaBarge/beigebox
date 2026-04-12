# Memory Integrity Implementation: Quick Reference

**Status:** Ready for implementation  
**Complexity:** Low-Medium (schema + 2 new methods)  
**Risk:** Minimal (backwards compatible, feature-gated)  
**Performance:** <5ms per conversation read  

---

## Core Files to Modify

### 1. `/beigebox/storage/sqlite_store.py`

**Changes:**
- Add to `MIGRATIONS` list (lines 227-245):
  ```python
  "ALTER TABLE messages ADD COLUMN message_hmac TEXT DEFAULT NULL",
  "ALTER TABLE messages ADD COLUMN integrity_version INTEGER DEFAULT 0",
  "ALTER TABLE messages ADD COLUMN tamper_detected INTEGER DEFAULT 0",
  ```

- Add method `_verify_message_integrity(self, row)` — verify HMAC signature
- Update `store_message()` — compute HMAC on write
- Update `get_conversation()` — verify HMAC on read, flag corrupted messages

**Key function signatures:**
```python
def _verify_message_integrity(self, row: sqlite3.Row) -> bool:
    """Return True if HMAC valid, False if mismatch."""

def store_message(self, msg: Message, ...):
    """Compute HMAC over (conversation_id|role|content|model|timestamp)."""

def get_conversation(self, conversation_id: str) -> list[dict]:
    """Return messages, adding '_corrupted': bool flag to corrupted rows."""
```

### 2. `/beigebox/config.py` (Optional)

Add to `Pydantic` models:
```python
class _IntegrityCfg(BaseModel):
    enabled: bool = True
    algorithm: str = "hmac-sha256"
    key_env_var: str = "BB_MESSAGE_INTEGRITY_KEY"
    fail_mode: str = "log_only"  # log_only | quarantine | strict
```

Add to `config.yaml`:
```yaml
integrity:
  enabled: true
  algorithm: "hmac-sha256"
  key_env_var: "BB_MESSAGE_INTEGRITY_KEY"
  fail_mode: "log_only"
```

### 3. `/beigebox/agents/operator.py` (Optional Guard)

Add before LLM context is built:
```python
async def _build_context_for_llm(self, messages: list[dict]) -> list[dict]:
    """Reject if any messages are corrupted."""
    corrupted = [m for m in messages if m.get("_corrupted")]
    if corrupted:
        cfg = get_config()
        fail_mode = cfg.get("integrity", {}).get("fail_mode", "log_only")
        
        if fail_mode == "strict":
            raise ValueError(f"{len(corrupted)} corrupted messages detected")
        elif fail_mode == "quarantine":
            logger.warning(f"Conversation contains {len(corrupted)} corrupted messages")
            # Continue but mark in logs
    
    return messages
```

---

## Key Material

### Generation
```bash
# Generate 32-byte key (256 bits)
python3 -c "import secrets; print(secrets.token_hex(32))"
# Output: 0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20
```

### Storage
```bash
# In .env or Docker secrets
BB_MESSAGE_INTEGRITY_KEY=0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20
```

### Access
```python
import os
key_hex = os.environ.get("BB_MESSAGE_INTEGRITY_KEY", "")
if key_hex:
    key = bytes.fromhex(key_hex)
else:
    logger.warning("BB_MESSAGE_INTEGRITY_KEY not set; integrity checks disabled")
```

---

## HMAC Computation

**Field order (canonical):**
```
payload = f"{conversation_id}|{role}|{content}|{model}|{timestamp}"
```

**Algorithm:**
```python
import hmac, hashlib

key = bytes.fromhex(BB_MESSAGE_INTEGRITY_KEY)
payload = f"{conv_id}|{role}|{content}|{model}|{ts}"
msg_hmac = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
```

**Storage:**
- Store in `messages.message_hmac` (64-char hex string)
- Set `messages.integrity_version = 1` (for future multi-version support)

**Verification:**
1. Read row from SQLite
2. Recompute HMAC using same payload order
3. Compare: `expected == row['message_hmac']`
4. If mismatch: log alert, set `row['_corrupted'] = True`, return to caller

---

## Integration Points

| Subsystem | Impact | Action |
|-----------|--------|--------|
| **Proxy** | None | Already calls `sqlite.get_conversation()`; will auto-get `_corrupted` flag |
| **Operator** | Medium | Add guard in `_build_context_for_llm()` to check `_corrupted` flag |
| **Harness** | Medium | Same guard as Operator |
| **Memory tool** | None | Reads from vector store, not SQLite; no change |
| **Replay** | None | Will receive `_corrupted` flag; can filter in display |

---

## Testing Checklist

- [ ] Unit: HMAC computed correctly on `store_message()`
- [ ] Unit: HMAC verified correctly on `get_conversation()`
- [ ] Unit: Corrupted messages flagged with `_corrupted=True`
- [ ] Unit: Old messages (NULL HMAC) readable but unverified
- [ ] Integration: Operator rejects conversation with corrupted messages (strict mode)
- [ ] Integration: Operator logs warning but continues (quarantine mode)
- [ ] Integration: Operator ignores corruption (log_only mode)
- [ ] Manual: Direct SQLite corruption detected within 100ms
- [ ] Performance: <5ms overhead per 50-message conversation

---

## Backwards Compatibility

**Old messages (before HMAC):**
- `message_hmac` column will be NULL
- `_verify_message_integrity()` returns True (unverified but allowed)
- Caller is responsible for handling `_corrupted=True` flag

**Migration path:**
- Phase 1 (Week 1): Deploy schema + compute HMAC on new messages, `fail_mode=log_only`
- Phase 2 (Week 2-3): Monitor; batch-sign existing messages if desired
- Phase 3 (Week 4): Switch to `fail_mode=quarantine` or `strict`

---

## Monitoring & Alerts

**Tap events:**
```python
self.wire.log(
    event_type="integrity_failure",
    source="storage",
    content=f"Message {msg_id} HMAC mismatch",
    conversation_id=conv_id,
    meta={
        "message_id": msg_id,
        "expected": expected[:16],
        "actual": actual[:16],
    }
)
```

**Metrics:**
- `beigebox_integrity_checks_total` — total verifications
- `beigebox_integrity_failures_total` — detected corruptions
- `beigebox_integrity_latency_ms` — verification overhead

**Logging:**
```python
logger.error("INTEGRITY FAILURE: Message %s HMAC mismatch. CORRUPTION DETECTED.", msg_id)
logger.warning("Conversation %s contains %d corrupted messages", conv_id, count)
logger.critical("REJECTING request: corrupted messages in context (strict mode)")
```

---

## Edge Cases

| Case | Handling |
|------|----------|
| **No integrity key set** | Checks disabled, warnings logged, messages readable |
| **Old message (NULL HMAC)** | Unverified, readable, flagged in logs |
| **New message (has HMAC)** | Verified on read |
| **Mixed old+new** | Both readable; old unverified, new verified |
| **Malformed key (bad hex)** | Exception caught, checks disabled, error logged |
| **Concurrent write + read** | WAL ensures isolation; no race condition |

---

## Future Extensions

1. **Conversation-level root hash** — tree hash of all messages, commit immutable
2. **Tap event signing** — HMAC on wire_events table
3. **Tool/skill registry signing** — prevent tool injection
4. **Key rotation** — versioned HMACs with key_version field
5. **Hardware key management** — HSM integration for `BB_MESSAGE_INTEGRITY_KEY`

---

## References

- **Full threat analysis:** `/d0cs/THREAT-MEMORY-POISONING.md`
- **RFC 2104:** HMAC specification
- **OWASP:** Data integrity verification patterns
