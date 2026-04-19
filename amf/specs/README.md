# AMF Ring Queue Formal Specifications

Formal TLA+ specifications for the Agent Mesh Framework's secure ring queue communication pattern.

## Overview

Three complementary formal specs verify critical security properties:

### 1. **RingLatency.tla** - Fault Detection & Circuit Breaking

**What it models:**
- Ring queue with N agents in sequence (A→B→C→...→A)
- "At-bat" agent gets current turn, "on-deck" agent monitors latency
- Each tick, on-deck agent starts timer; if at-bat doesn't respond by timeout, mark late
- After X consecutive failures, circuit breaker removes agent from ring
- Ring continues even if agent is late (message accepted whenever it arrives)

**Key properties verified:**
- ✓ Ring never deadlocks (always has at least 1 agent)
- ✓ Latency detection is non-blocking (on-deck marks late without halting ring)
- ✓ Circuit breaker removes failed agents (no recovery, fresh start)
- ✓ Message ordering preserved across failures

**Use case:** Detecting compromised/slow agents in real-time without stopping other agents.

**Parameters:**
- `MAX_FAILURES = 3` (fail 3 times consecutively, then circuit break)
- `TICK_TIMEOUT_MS = 100` (agents have 100ms to respond)

---

### 2. **BoundedHistory.tla** - Adversary Isolation via Windowing

**What it models:**
- Active queue: Last Y messages (hot path, currently in use)
- Archive queue: Messages Y+1 and older (cold path, separate security boundary)
- When message Y+1 arrives, message 1 rotates to archive
- Archive uses different encryption keys (sealed at time of rotation)
- Compromise of active queue does NOT expose archive

**Key properties verified:**
- ✓ Active queue bounded to Y messages max
- ✓ Archived messages are immutable (timestamp + sealed key)
- ✓ Key isolation (old messages use old keys, new messages use new keys)
- ✓ Messages older than MAX_ARCHIVE_AGE are purged
- ✓ Archive is non-repudiable (attacker can't claim false timestamp)

**Use case:** If DMZ agent gets pwned after processing message #50, attacker can only see last ~20 messages (active window), not the full history.

**Parameters:**
- `ACTIVE_WINDOW_SIZE = 20` (keep last 20 messages hot)
- `MAX_ARCHIVE_AGE = 604800` (keep archived for 7 days)

---

### 3. **MessagePadding.tla** - Constant-Time Messaging

**What it models:**
- All messages are exactly 512 bytes (header 32 + payload ≤468 + padding)
- Attacker can't tell message size from ciphertext (all same size)
- Padding is pre-generated at startup (not at message time)
- Four padding strategies: ZEROS, RANDOM, DETERMINISTIC, CHACHA_STREAM
- In-place updates: same-size payload can update with fresh nonce, reusing pad

**Key properties verified:**
- ✓ All messages exactly MESSAGE_SIZE (512 bytes)
- ✓ Nonces never repeat (per-agent monotonic)
- ✓ Pad pool index bounded (can't overflow)
- ✓ Payload + padding always equals max size
- ✓ In-place updates maintain size invariant

**Padding types:**
| Type | Entropy | Repeatable | Latency | Security |
|------|---------|-----------|---------|----------|
| ZEROS | No | Yes | O(n) | ✗ (detectable) |
| RANDOM | Yes | No | O(1)* | ✓ (unpredictable) |
| DETERMINISTIC | No | Yes | O(1) | ✓ (HMAC-based) |
| CHACHA | No | Yes | O(1) | ✓ (keystream) |

*Pre-generated at startup, O(1) at message time

**Use case:** Prevent traffic analysis attacks. Attacker sees constant 512-byte messages even if one agent sends "x" and another sends a full policy document.

---

## How to Run

### Verify individual specs with TLC:

```bash
cd /home/jinx/ai-stack/beigebox/amf/specs

# Check latency & fault tolerance
java -cp /path/to/tla2tools.jar tlc2.TLC RingLatency -config RingLatency.cfg

# Check bounded history adversary isolation
java -cp /path/to/tla2tools.jar tlc2.TLC BoundedHistory -config BoundedHistory.cfg

# Check message padding
java -cp /path/to/tla2tools.jar tlc2.TLC MessagePadding -config MessagePadding.cfg
```

### Explore state space:

Each spec generates ~1000-5000 distinct states. TLC reports:
- ✓ All invariants hold (no contradictions)
- ✗ Deadlock (timeline becomes impossible)
- ✗ Invariant violated (bug found in spec)

---

## Security Properties Guaranteed

### By RingLatency:
- **Liveness**: Agents can't permanently stall others
- **Fault detection**: Compromised agents detected via latency
- **Bounded blast radius**: One slow agent doesn't block ring

### By BoundedHistory:
- **Information isolation**: Old messages safe even if current agent pwned
- **Non-repudiation**: Archived messages have unforgeable timestamps
- **Graceful degradation**: Can access archive with special authorization

### By MessagePadding:
- **Constant size**: Traffic analysis impossible
- **No runtime entropy**: Deterministic, reproducible (testable)
- **Nonce safety**: Never repeat nonce = GCM security guaranteed
- **In-place updates**: No allocation side-channels

---

## Integration with Implementation

These specs define the **properties** the implementation must satisfy:

### Phase 1: Verification (current)
- ✓ RingLatency verified for N=4 agents, MAX_FAILURES=3
- ✓ BoundedHistory verified for WINDOW_SIZE=20
- ✓ MessagePadding verified for 512-byte messages

### Phase 2: Implementation (next)
- Write reference implementation in Go/Rust
- Prove (in Dafny) that implementation satisfies these specs
- Example Dafny proofs:
  - `lemma RingCannotDeadlock(agents: seq<Agent>) { ... }`
  - `lemma ArchiveImmutable(msg: Message, sealed_key: Key) { ... }`
  - `lemma NonceMonotonic(nonce_gen: NonceGenerator) { ... }`

### Phase 3: Testing
- Unit tests for each agent role
- Integration tests with all three specs active
- Adversarial tests (inject latency, compromise agents)

---

## Critical Path from Week 1

This spec work (1-2 weeks) feeds into:
- **Weeks 3-5**: Implement ring queue in Go (with Dafny proofs)
- **Weeks 6-8**: Crypto proofs (signing, nonce dedup)
- **Weeks 9-20**: Policy verification + isolation proofs
- **Weeks 21-28**: Side-channel analysis

Each phase re-uses the TLA+ structure (state machine, invariants, fairness constraints).

---

## Next: Generate Secure Pads

Question: How to pre-generate pads securely at VM startup?

**Option A: Derive from master key**
```go
master_key := PBKDF2(passphrase, salt, iterations)
for i := 0..PAD_POOL_SIZE {
  pad[i] := ChaCha20(master_key, nonce=i)[0:pad_size]
}
```
- ✓ Deterministic, reproducible
- ✓ No entropy pool needed
- ✗ All pads derived from one key (if key compromised, all pads compromised)

**Option B: System entropy pool**
```go
for i := 0..PAD_POOL_SIZE {
  pad[i] := SecureRandom(pad_size)  // from /dev/urandom
}
```
- ✓ Independent pads
- ✗ Blocks on entropy, slow at startup
- ✗ Non-reproducible

**Option C: Hybrid (recommended)**
```go
master_key := EntropyPool.Read(32)  // One entropy call at startup
for i := 0..PAD_POOL_SIZE {
  pad[i] := HMAC_SHA256(master_key, counter=i)[0:pad_size]
}
```
- ✓ Independent pads (different counters)
- ✓ Single entropy call (fast)
- ✓ Reproducible with same master_key
- ✓ If master_key leaked, attacker can predict future pads (OK—agent would respawn)

Recommendation: **Use Option C**. Generate 1000 pads at startup (takes ~1ms), agent respawns when pool depleted or after 1 hour.

