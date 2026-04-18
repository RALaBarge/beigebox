# AMF Reference Implementation: Design Decisions & Justifications

## Overview

This document explains the 11 core architectural decisions in the AMF reference implementation, why each was made, and what trade-offs were accepted.

---

## 1. Sacrificial DMZ Pattern

### Decision
The DMZ is intentionally designed to be compromised and respawned. On each respawn, both the cryptographic state (new Ed25519 keypair) and LLM context (fresh session) are reset.

### Justification
- **Threat Model Reality**: In hostile networks with untrusted input, the gateway will eventually be compromised via prompt injection
- **Detection is Impossible**: No practical way to detect if an LLM has been corrupted by a prompt
- **Fail-Forward Design**: Instead of trying to detect compromise, design for automatic recovery
- **Isolation Guarantee**: Ring agents continue operating independently during DMZ downtime
- **Clean Slate**: Respawn resets both crypto keys AND mental state, making persistence of attacks impossible

### Trade-Offs
✓ **Cost**: Ring agents must vote to re-admit each new DMZ instance (quorum voting overhead ~50ms)
✗ **Benefit**: Attackers cannot maintain persistent control; injection attacks are self-contained

### Formal Basis
This is formalized in **TLA+ CryptoIdentity.tla**:
```tla
DMZRespawn: 
  /\ dmz_admitted = TRUE
  /\ dmz_admitted' = FALSE
  /\ agent_nonces[dmz_id]' = 0  (* nonce counter reset *)
  /\ seen_nonces[dmz_id]' = {}  (* old nonces invalidated *)
```

---

## 2. Ring Topology Isolation

### Decision
The DMZ is external to the ring. Ring agents form a closed internal topology (A↔B↔C↔A) that can operate independently if the DMZ is unavailable or compromised.

### Justification
- **Independent Liveness**: Ring agents never depend on DMZ for their own operation
- **Topology as Trust Boundary**: The structure itself enforces isolation (DMZ cannot read ring state, ring cannot be stalled by DMZ)
- **Simplicity**: No need for complex delegation or proxy patterns; ring is self-contained
- **Scalability**: If one ring agent fails, the other two can continue with reduced capacity

### Trade-Offs
✓ **Benefit**: Clean isolation boundary, simple threat model
✗ **Cost**: Requires separate listening socket per agent (minimal overhead)

### Architecture
```
  [DMZ connects here] ← → [Ring-A] ← → [Ring-B] ← → [Ring-C]
                            ↑___________|___________|↑
```

---

## 3. Ed25519 for Signing

### Decision
Use Ed25519 instead of ECDSA, RSA, or other signing schemes.

### Justification
- **Deterministic**: Every signature for the same (key, message) pair is identical
  - Eliminates per-signature randomness
  - Makes behavior easier to model in TLA+
  - Simpler Dafny proofs (no randomness invariants)
- **Constant-Time**: All correct implementations are immune to timing attacks
  - libsodium, x/crypto, ring all provide guaranteed constant-time
  - No special hardware needed (AES-NI, CLMUL, etc.)
- **Simplicity**: No RFC 6979 nonsense, no curve parameter debates
- **Proven**: Extensively audited in Tor, Signal, WireGuard, et al.

### Trade-Offs
✓ **Benefit**: Easier to reason about formally; timing side-channel free
✗ **Smaller**: 256-bit security (but still sufficient for agent auth)

### Cryptographic Basis
```
σ = SHA512(privkey || message) mod order
verify(pubkey, message, σ) = g^σ * pubkey^hash(message) == R
```
Deterministic because there's no random nonce k (unlike ECDSA).

---

## 4. ChaCha20-Poly1305 for AEAD

### Decision
Use ChaCha20-Poly1305 instead of AES-GCM for message encryption.

### Justification
- **Hardware-Independent**: Works identically on any CPU
  - No AES-NI accelerators (which can leak via timing)
  - No CLMUL for Ghash (another timing leak vector)
  - Avoids cryptographic coprocessor side-channels
- **Nonce Safety**: 12-byte random nonces, with 24-bit counter (larger space)
  - AES-GCM: 12-byte nonce, but sequential use is vulnerable to repeat attacks
  - ChaCha: Designed for random nonces (no counter-mode nonce brittleness)
- **Simpler**: No IV/counter management complexity
- **Proven Constant-Time**: libsodium and x/crypto both guarantee no timing leaks

### Threat Model
```
Attacker can:
- Choose plaintext (chosen-plaintext attack)
- See ciphertext
- Control nonces (adaptive attacks)

ChaCha20-Poly1305 is proven secure even when attacker chooses nonce.
AES-GCM breaks if same nonce is used twice (even with different messages).
```

### Trade-Offs
✓ **Benefit**: Side-channel resistant, nonce-flexible
✗ **Slightly slower** than AES-GCM on modern CPUs with AES-NI (but we reject hardware acceleration for security)

---

## 5. Destroyed Archive Keys (HMAC Integrity Only)

### Decision
Archive encryption keys are **destroyed immediately after sealing**. Later operations use HMAC-SHA256 for integrity verification, not encryption.

### Justification
- **No Key Exposure**: Archive cannot be decrypted even if storage is breached
  - Single attack surface: compromise storage ≠ compromise keys
  - Keys were destroyed; attacker cannot extract what doesn't exist
- **Immutability Guarantee**: HMAC proves archive wasn't modified post-sealing
  - Supports regulatory compliance (audit trail integrity)
  - Simplifies legal discovery (can show document wasn't tampered)
- **Reduces Blast Radius**: Fewer keys in memory = fewer extraction vectors
  - Fewer handles to accidentally log or pass to untrusted code
  - Simpler key management (no rotation, invalidation, or revocation)

### Formal Specification
From **BoundedHistory.tla**:
```tla
ArchiveSealing:
  /\ archived_key = "DESTROYED"  (* key is gone *)
  /\ archive_integrity = HMAC(archived_payload, salt)
```

### Trade-Offs
✓ **Benefit**: Archive is immutable and provably unforged
✗ **Archive is readable**: Plaintext is exposed if storage is compromised

This is acceptable because:
- Archive is **integrity-protected** (tampering detected)
- Archive is **immutable** (cannot be modified)
- Regulatory/legal value is in proving immutability, not confidentiality

---

## 6. Single-Threaded Event Loop with Fair Scheduling

### Decision
One message per tick, no parallelism, 1ms sleep between ticks. All processing is sequential and deterministic.

### Justification
- **Constant-Time Processing**: Tick duration is always 1ms, regardless of message complexity
  - No variable timing based on workload or queue depth
  - Attacker cannot infer message content from processing duration
- **Fairness**: No message starvation or priority inversion
  - Each agent processes one message per round
  - No head-of-line blocking
- **Determinism**: Behavior is predictable and reproducible
  - Easier to test, debug, and formally verify
  - No race conditions or thread-safety issues
- **No GC Pauses**: Rust with no dynamic allocation in hot path
  - No unpredictable latency spikes from garbage collection
  - Timing is truly constant
- **Simplicity**: State machine is trivial to understand and audit

### Performance Trade-Off
```
Single-threaded: 1 message per tick × 1000 ticks/sec = 1,000 msgs/sec max
Multi-threaded: N workers × 100 msgs/sec each = 100,000+ msgs/sec
```
We sacrifice throughput (100×) for timing side-channel safety.

---

## 7. Default-Deny Policy

### Decision
Every action must be explicitly allowed by the policy. No implicit permissions.

### Justification
- **Principle of Least Privilege**: Default is rejection
  - Safer default posture
  - Operator has to explicitly reason about each allowed action
- **Fail-Safe**: New actions are automatically denied until whitelisted
  - No possibility of accidentally enabling privileged operations
  - Prevents privilege escalation via misconfig
- **Audit Clarity**: Policy violations are obvious in logs
  - Denied action = evidence of attack or misconfiguration
  - Allowed action = operator intentionally permitted
- **Regulatory Compliance**: Many standards (SOC2, ISO27001) require default-deny

### Policy Format
```rust
// Only explicit rules are allowed
AllowRule::new(Agent::DMZ, "send", Agent::RingA),
AllowRule::new(Agent::RingA, "receive", Agent::DMZ),
AllowRule::new(Agent::RingA, "tick", Agent::RingB),
```

No implicit allow-all; no catch-all rules.

### Trade-Offs
✓ **Benefit**: Maximum security posture
✗ **Cost**: Must explicitly list every allowed action (slightly verbose)

---

## 8. Quorum Voting (3-of-3) for DMZ Admission

### Decision
All three ring agents must vote "yes" to admit the DMZ. No 2-of-3 majority or weighted voting.

### Justification
- **Byzantine Tolerance**: Even if one ring agent's logic is corrupted, the DMZ is still rejected
  - Threshold of 1 (all agents agree) is the safest
  - Prevents subtle voting exploits where 2-of-3 compromise silently admits attacker
- **Unanimous Consensus**: Simple decision rule with no edge cases
  - No weighing, no thresholds to debate
  - One rule: all must agree or DMZ is rejected
- **Fault Tolerance**: If one ring agent crashes, we can either:
  - Wait for it to recover (acceptable downtime)
  - Respawn it and vote again (automated recovery)

### Trade-Offs
✓ **Benefit**: Maximum security against Byzantine faults
✗ **All-or-nothing**: If one agent fails, DMZ cannot be admitted (downtime)

Acceptable because:
- DMZ respawn is fast (~50ms)
- Ring can operate without DMZ (graceful degradation)

---

## 9. Unix Domain Sockets for IPC

### Decision
Use Unix domain sockets (not TCP/TLS) for communication between DMZ and ring agents.

### Justification
- **Simplicity**: No TLS certificate management, hostname verification, or encryption overhead
- **Kernel-Mediated Isolation**: OS enforces process boundaries via socket permissions
  - Socket file has Unix permissions (mode 0600)
  - Only processes with correct UID can connect
- **Atomic Messages**: Kernel ensures message writes are atomic (no partial messages)
- **Predictable Latency**: No network stack variability
  - No TCP retransmits, jitter, or backoff
  - Direct kernel-to-kernel buffer transfer
- **No Network Exposure**: Cannot be intercepted across network

### Security Model
```
Socket file: ~/.amf/ring_a.sock (mode 0600)
  ↓
Only UID owner can connect
  ↓
Kernel enforces file permissions
  ↓
Attacker cannot connect unless they own the UID
```

### Trade-Offs
✓ **Benefit**: Simple, fast, kernel-protected
✗ **Local-only**: Cannot be extended to distributed deployment without TLS

Acceptable for reference implementation; production would add TLS for network deployment.

---

## 10. JSON Message Framing Over Sockets

### Decision
Messages are JSON, newline-delimited, sent as plaintext over sockets. Encryption happens at the application layer (ChaCha20-Poly1305).

### Justification
- **Human-Readable**: Easy to debug and log
  - Can inspect messages in transit (before encryption in Phase 2)
  - Researchers can easily understand the protocol
- **Self-Delimiting**: Newline marks message boundary
  - No length prefix (reduces framing complexity)
  - Newline is unambiguous terminator
- **Language-Agnostic**: JSON is universally parseable
  - Future implementations in Python, Go, etc. can interop
  - No endianness or byte-order issues
- **Stateless**: No protocol state machine
  - Connect → send message → read response
  - No handshake, sync, or state negotiation
- **Standard Format**: JSON is the de facto standard for IPC protocols

### Message Format
```json
{
  "msg_type": "encrypted_message",
  "payload": {
    "nonce": 1,
    "ciphertext": "a1b2c3d4..."
  }
}
[NEWLINE]
```

### Trade-Offs
✓ **Benefit**: Simple, debuggable, standard
✗ **Larger than binary**: ~500 bytes vs ~50 bytes for same message

Acceptable because local socket bandwidth is not a bottleneck.

---

## 11. SQLite for Persistent Archive

### Decision
Use SQLite for both the audit log and message archive.

### Justification
- **No External Dependencies**: Database is self-contained in a single file
  - No separate server process
  - No network connectivity required
  - Portable (can copy database file anywhere)
- **ACID Guarantees**: Transactions ensure audit trail consistency
  - All-or-nothing writes (no partial entries)
  - Serializable isolation (no race conditions)
  - Durability (survives process crash)
- **Queryable**: Can investigate patterns
  - "Show all messages from Agent X"
  - "Show all policy violations in last 10 minutes"
  - "Show nonce sequence for agent Y"
- **Thread-Safe**: Can wrap in Arc<Mutex<>> for concurrent access
  - Single writer (Mutex ensures serialization)
  - Multiple readers (SQLite handles concurrent reads)
- **Minimal**: Pure Rust, minimal dependencies, fast

### Schema
```sql
CREATE TABLE audit_log (
  timestamp_ms INTEGER,
  agent TEXT,
  nonce INTEGER,
  action TEXT,
  decision TEXT,
  UNIQUE(agent, nonce)  -- prevents duplicate nonces
);

CREATE INDEX idx_agent ON audit_log(agent);
CREATE INDEX idx_nonce ON audit_log(nonce);
```

### Trade-Offs
✓ **Benefit**: Simple, queryable, durable
✗ **Single-machine only**: Not distributed

Acceptable for reference implementation; production would use:
- Replicated database (PostgreSQL with replication)
- Immutable log system (S3 with versioning)
- Blockchain-style append-only ledger

---

## Summary Table

| Decision | Chosen | Rationale | Trade-Off |
|----------|--------|-----------|-----------|
| 1. DMZ Pattern | Sacrificial respawn | Impossible to detect compromise | ~50ms quorum voting overhead |
| 2. Topology | Ring (internal) + External DMZ | Isolation boundary | Separate socket per agent |
| 3. Signing | Ed25519 | Deterministic, constant-time | Smaller (but sufficient) |
| 4. AEAD | ChaCha20-Poly1305 | Hardware-independent, nonce-safe | Slower than AES-GCM (rejected HW) |
| 5. Archive Keys | Destroyed (HMAC only) | No key extraction | Archive plaintext readable |
| 6. Scheduler | Single-threaded, fair | Constant-time, deterministic | 1,000 msg/sec vs 100,000 possible |
| 7. Policy | Default-deny | Principle of least privilege | More verbose rules |
| 8. Quorum | 3-of-3 unanimous | Byzantine tolerance | Strict requirement (all must agree) |
| 9. IPC | Unix sockets | Kernel isolation, simple | Local-only (no network) |
| 10. Framing | JSON + newline | Human-readable, standard | Larger than binary |
| 11. Database | SQLite | Portable, queryable, ACID | Single-machine (not distributed) |

---

## Formal Verification

Each decision is paired with formal specifications:

| Decision | TLA+ Spec | Dafny Proof |
|----------|-----------|------------|
| Deterministic signing | CryptoIdentity.tla | Ed25519 is deterministic |
| Nonce monotonicity | CryptoIdentity.tla | `NoncesMonotonic` invariant |
| Replay protection | CryptoIdentity.tla | `ReplayImpossible` invariant |
| Archive immutability | BoundedHistory.tla | `ArchiveImmutableAfterSealing` |
| Policy enforcement | PolicyEnforcement.tla | `DefaultDeny` invariant |
| Isolation boundary | IsolationBoundary.tla | `DMZCannotSeeRingState` |
| Timing side-channels | SideChannel.tla | `TickIndependentOfPayload` |

---

## Conclusion

These 11 decisions form a coherent security architecture where:

1. **Simplicity** is the foundation (single-threaded, synchronous, local)
2. **Determinism** enables formal verification (no randomness, no concurrency)
3. **Isolation** is structural (ring vs DMZ, topology, permissions)
4. **Immutability** prevents tampering (destroyed keys, HMAC sealing)
5. **Transparency** aids auditing (human-readable JSON, queryable database)

The reference implementation demonstrates that a secure, formally verifiable system can be built without sacrificing clarity or auditability.
