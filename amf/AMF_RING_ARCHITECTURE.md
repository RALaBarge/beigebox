# AMF Ring Queue Architecture

## System Overview

The Agent Mesh Framework uses a **secure ring queue** for agent-to-agent communication in adversarial environments. Three TLA+ specs formalize the three critical layers:

```
┌─────────────────────────────────────────────────────────┐
│ APPLICATION LAYER (agents, policies, logic)             │
├─────────────────────────────────────────────────────────┤
│ MESSAGE PADDING LAYER (RingLatency.tla)                 │
│ - Constant 512-byte messages (attacker can't tell size) │
│ - Pre-generated pads (no runtime entropy)               │
│ - In-place updates (no allocation side-channels)        │
├─────────────────────────────────────────────────────────┤
│ BOUNDED HISTORY LAYER (BoundedHistory.tla)              │
│ - Last 20 messages in hot active queue                  │
│ - Older messages in cold archive (separate keys)        │
│ - Compromise of active doesn't leak archive             │
├─────────────────────────────────────────────────────────┤
│ RING QUEUE LAYER (RingLatency.tla)                      │
│ - A→B→C→D→A ring topology                              │
│ - At-bat / on-deck latency detection                    │
│ - Circuit breaker removes failed agents                 │
│ - Continues even if agent is slow (accepted late)       │
├─────────────────────────────────────────────────────────┤
│ TRANSPORT (encryption, signing, network)               │
└─────────────────────────────────────────────────────────┘
```

## The Three Specs & How They Interact

### 1. RingLatency: Fault Tolerance

**Problem**: If one agent is slow, compromised, or crashed, how do we detect it without stopping the whole system?

**Solution**: Ring topology with latency monitoring
- Agent A has "at-bat" (current turn)
- Agent B has "on-deck" (next turn)
- On-deck starts timer when at-bat begins
- If at-bat doesn't respond by TICK_TIMEOUT_MS (100ms), mark late
- After MAX_FAILURES (3) consecutive lates, circuit break the agent
- Ring shrinks: A→B→[C is broken]→D→A becomes A→B→D→A

**State machine**:
```
[tick_start] → [message arrives or timeout] → [advance to next agent]
   ↓                      ↓
at_bat=A              on_deck marks late
on_deck=B             late_count[A] += 1
                      if late_count[A] >= 3:
                         remove A from ring
```

**Formal properties**:
- ✓ Ring is never empty (at least 1 agent always alive)
- ✓ Latency detection is non-blocking (doesn't halt other agents)
- ✓ Circuit breaker is deterministic (same failure threshold for all)
- ✓ Messages are never lost (even late messages accepted and processed)

**Example trace** (4 agents, MAX_FAILURES=3):
```
Tick 0: at_bat=A, on_deck=B
Tick 1: A responds on-time → late_count[A]=0, advance to at_bat=B
Tick 2: B responds late → late_count[B]=1, advance to at_bat=C
Tick 3: C responds late → late_count[C]=1, advance to at_bat=D
Tick 4: D responds late → late_count[D]=1, advance to at_bat=A
Tick 5: A responds late → late_count[A]=1, advance to at_bat=B
Tick 6: B responds late → late_count[B]=2, advance to at_bat=C
Tick 7: C responds late → late_count[C]=2, advance to at_bat=D
Tick 8: D responds late → late_count[D]=2, advance to at_bat=A
Tick 9: A responds late → late_count[A]=2, advance to at_bat=B
Tick 10: B responds late → late_count[B]=3 → CIRCUIT BREAK B
         Ring now: A→C→D→A
```

---

### 2. BoundedHistory: Adversary Isolation

**Problem**: If a DMZ agent gets compromised, attacker can read all messages it has ever processed. How do we limit the blast radius?

**Solution**: Sliding window of recent messages + archive with separate keys
- Active queue: Last 20 messages (hot path, current keys)
- Archive queue: Messages 21+ (cold path, sealed with old keys)
- When message #21 arrives, message #1 rotates to archive
- Archive keys are SEALED at rotation time (never match current keys)
- Compromise of active queue ≠ compromise of archive

**State machine**:
```
New message arrives
  ↓
Active queue full?
  ├─ NO: Add to active queue, continue
  └─ YES: 
      ├─ Move head(active) to archive[sealed_with_key_at_rotation_time]
      ├─ Add new message to active
      └─ Rotate encryption key for future messages
```

**Example trace** (ACTIVE_WINDOW=3, messages abbreviated as M1, M2, ...):
```
M1 arrives: active=[M1], archive=[]
M2 arrives: active=[M1,M2], archive=[]
M3 arrives: active=[M1,M2,M3], archive=[]
M4 arrives: active=[M2,M3,M4], archive=[M1+seal{key_v1}]
M5 arrives: active=[M3,M4,M5], archive=[M1+seal{key_v1}, M2+seal{key_v2}]
...
Agent gets pwned at M10
  - Attacker can read: M8, M9, M10 (in active queue)
  - Attacker CANNOT read: M1..M7 (in archive, need key_v1..key_v7)
  - Those keys are inaccessible (sealed, only accessible via secure archive API)
```

**Formal properties**:
- ✓ Active queue never exceeds window size
- ✓ Archived messages are immutable (timestamp + nonce + sealed key)
- ✓ Key isolation: old keys never match new keys
- ✓ Messages older than MAX_ARCHIVE_AGE are purged (GDPR compliance)
- ✓ Archive is non-repudiable (attacker can't claim false timestamp)

---

### 3. MessagePadding: Traffic Analysis Prevention

**Problem**: All messages are different sizes. Attacker observes packet sizes → infers content (large message = policy doc, small = heartbeat). How do we hide message size?

**Solution**: Constant-message-size with pre-generated padding
- All messages exactly 512 bytes (32 header + 468 max payload + padding)
- Padding is pre-generated at startup, not at message time
- Four padding strategies (ZEROS, RANDOM, DETERMINISTIC, CHACHA)
- In-place updates: same-size payload can update with fresh nonce

**Message structure**:
```
┌──────────────────────────────────────┐
│ HEADER (32 bytes)                    │
├──────────────────────────────────────┤
│ PAYLOAD (size: 0 to 468)             │
├──────────────────────────────────────┤
│ PADDING (fills to 512 total)         │
└──────────────────────────────────────┘
```

**Example**:
```
Message A: payload_size=100, padding_size=380 → total=512
Message B: payload_size=468, padding_size=12  → total=512
Message C: payload_size=50,  padding_size=430 → total=512

Ciphertext A: 512 bytes
Ciphertext B: 512 bytes
Ciphertext C: 512 bytes

Attacker sees: ✗ can't tell A, B, C apart (all same size!)
```

**Pre-generated pad pool**:
```go
// At startup (once per agent lifetime)
master_key := Hash(entropy || agent_id)
pads := []
for i in range(PAD_POOL_SIZE):
  pads[i] := HMAC_SHA256(master_key, counter=i)[:PAD_SIZE]

// At message time (O(1) lookup)
message.padding = pads[pad_index++]
```

**Formal properties**:
- ✓ All messages exactly 512 bytes
- ✓ Nonces never repeat (per-agent monotonic counter)
- ✓ Pad pool index bounded (can't overflow)
- ✓ Payload + padding always fills to 512
- ✓ In-place updates maintain size (same-size updates reuse padding)

---

## How The Three Layers Work Together

### Scenario: Secure Message Flow A→B→C→D

**Time 0: System starts**
1. RingLatency initializes: ring=[A,B,C,D], at_bat=A, on_deck=B
2. BoundedHistory initializes: active=[], archive=[], agent_keys={A→k_a, B→k_b, C→k_c, D→k_d}
3. MessagePadding initializes: pads={A→[1000 pads], B→[1000 pads], ...}

**Time 1: A sends "encrypt this policy doc (200 bytes)"**
1. **MessagePadding layer**: 
   - msg.payload = 200 bytes
   - msg.padding = pads[A][100] (496 bytes)
   - msg.total_size = 512 bytes
   - A's nonce increments: nonce_A = 1

2. **BoundedHistory layer**:
   - active.append(msg)
   - active = [msg_1]

3. **RingLatency layer**:
   - at_bat=A processes message
   - on_deck=B starts 100ms timer
   - Tick advances: at_bat=B, on_deck=C

**Time 2: B receives ciphertext (512 bytes)**
1. **RingLatency layer**:
   - B responds on-time → late_count[B]=0
   - Tick advances: at_bat=C, on_deck=D

2. **BoundedHistory layer**:
   - active.append(msg_2)
   - active = [msg_1, msg_2]

3. **MessagePadding layer**:
   - Attacker observing network sees: 512 bytes, 512 bytes
   - Can't tell message 1 was 200 bytes or what it contains!

**Time 3: C slow to respond (120ms > 100ms timeout)**
1. **RingLatency layer**:
   - on_deck=D detects timeout
   - late_count[C] = 1
   - C still sends message (late, but accepted)
   - Tick advances: at_bat=D, on_deck=A

2. **BoundedHistory layer**:
   - Message still accepted and added to active
   - Latency doesn't block message processing

**Time 10: A is circuit broken (3 consecutive lates)**
1. **RingLatency layer**:
   - late_count[A] >= 3
   - agent_status[A] = "circuit_broken"
   - ring = [B, C, D] (A removed)
   - at_bat = B (adjusted)

2. **Other layers unaffected**:
   - Messages continue flowing
   - Active/archive histories intact
   - Padding continues as normal

**Time 21: First message rotates to archive**
1. **BoundedHistory layer**:
   - active = [msg_2...msg_20]
   - archive = [msg_1 + sealed{key_v0}]
   - encryption_key rotated to k_v1

2. **RingLatency/MessagePadding**:
   - Unaffected, continue normally

---

## Threat Model & What Each Layer Defends Against

| Threat | RingLatency | BoundedHistory | MessagePadding |
|--------|-------------|----------------|----------------|
| **Slow/crashed agent** | ✓ Detects via latency, circuits out | — | — |
| **Agent compromise** | — | ✓ Limits to recent messages | ✓ Hides message size |
| **Complete ring compromise** | — | ✓ Archive safe with old keys | — |
| **Traffic analysis** | — | — | ✓ All messages same size |
| **Timing attacks** | ✓ Constant ticks | ✓ Constant rotation | ✓ Constant padding |
| **Nonce reuse** | — | — | ✓ Monotonic, pre-generated |
| **Message reordering** | ✓ Ring topology enforces order | ✓ Timestamp proof | — |

---

## Implementation Roadmap

### Week 1-2: Verify & Formalize
- ✓ RingLatency TLA+ spec (done)
- ✓ BoundedHistory TLA+ spec (done)
- ✓ MessagePadding TLA+ spec (done)
- Run TLC on all three, verify no deadlocks/invariant violations

### Week 3-5: Reference Implementation
- Implement ring queue in Go
- Implement latency detection (on_deck timer, late tracking)
- Implement circuit breaker (remove agent after X failures)
- Write Dafny proofs:
  - `lemma RingNeverEmpty()`
  - `lemma LatencyDetectionNonBlocking()`
  - `lemma CircuitBreakerCorrect()`

### Week 6-8: Bounded History Implementation
- Implement active/archive queues
- Implement key rotation at rotation time
- Implement archive access control
- Dafny proofs:
  - `lemma ArchiveImmutable()`
  - `lemma KeyIsolation()`
  - `lemma CompromiseDoesNotLeakArchive()`

### Week 9-10: Message Padding Implementation
- Implement 512-byte constant messages
- Pre-generate pad pools at startup
- Implement in-place updates
- Dafny proofs:
  - `lemma NonceMonotonic()`
  - `lemma NonceNeverRepeats()`
  - `lemma ConstantSizeEnforcedByType()`

### Week 11-12: Integration & Testing
- Integrate all three layers
- Write integration tests
- Adversarial tests (inject latency, compromise agents, analyze traffic)
- Security review

---

## Critical Design Decisions

### Why Ring Topology?
- ✓ Total message ordering (no parallel broadcast chaos)
- ✓ Latency detection is per-agent-pair (A→B latency measured by B)
- ✗ One slow agent affects everyone (mitigated by circuit breaker)

### Why Sliding Window + Archive?
- ✓ Old messages isolated from new keys
- ✓ Compromise window limited to recent messages
- ✗ Requires separate secure storage for archive

### Why Pre-generated Pads?
- ✓ O(1) padding at message time (no entropy call)
- ✓ Deterministic & reproducible (testable)
- ✗ Fixed pad set per agent lifetime (respawn to refresh)

---

## Next: Crypto Proofs

Once ring/bounded-history/padding are proven and implemented, move to:
1. **Signing**: Prove that signatures on each message are unforgeable
2. **Nonce deduplication**: Prove that nonce is never reused
3. **Replay prevention**: Prove that message replay is impossible
4. **Side-channel resistance**: Prove constant-time operations

All of these will extend the TLA+ specs with cryptographic invariants proven in Dafny.
