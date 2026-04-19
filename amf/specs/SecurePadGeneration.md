# Secure Pad Generation for Constant-Message Padding

## The Problem

In `MessagePadding.tla`, every message must be exactly 512 bytes. The 468-byte max payload leaves 0-44 bytes for padding depending on actual payload size.

**Naive approach**: Generate pads at message time from `/dev/urandom`
- ✗ Blocks on entropy (slow at high frequency)
- ✗ Non-deterministic (hard to test)
- ✗ Timing side-channels (entropy calls vary in latency)

**Better approach**: Pre-generate all pads at startup, use them sequentially
- ✓ One-time entropy cost at startup
- ✓ Deterministic (same seed → same pads, testable)
- ✓ O(1) pad lookup at message time

---

## Three Options for Pre-Generation

### Option A: Derive from Master Key (Deterministic, No Entropy)

```go
// At agent startup
masterKey := DeriveKey(agentID, constants)  // Deterministic hash
padPool := make([][]byte, PAD_POOL_SIZE)

for i := 0; i < PAD_POOL_SIZE; i++ {
  nonce := uint64(i)
  // ChaCha20(key, nonce) = deterministic keystream
  padPool[i] = ChaCha20(masterKey, nonce)[0:PAD_SIZE]
}

// At message time
msg.padding = padPool[padIndex++]
```

**Pros:**
- ✓ Deterministic (seed → same pads always)
- ✓ No entropy call at startup
- ✓ Infinite pad stream (can derive more if needed)
- ✓ Testable (unit tests with known seed)

**Cons:**
- ✗ All pads derived from one key
- ✗ If masterKey compromised, attacker can predict all pads (past and future)
- ✗ No randomness (pads are deterministic function of counter)

**Security:** ✓ Fine. If masterKey leaks, agent respawns anyway. Pre-generated pads serve to hide message size, not to be cryptographically random.

**Use case:** Testing, low-threat environments, devices without entropy.

---

### Option B: Pure Entropy (Maximum Randomness)

```go
// At agent startup
padPool := make([][]byte, PAD_POOL_SIZE)

for i := 0; i < PAD_POOL_SIZE; i++ {
  // Read directly from /dev/urandom
  // BLOCKS if entropy pool is low
  n, err := rand.Read(padPool[i])
  if err != nil {
    log.Fatal("entropy exhausted, cannot start agent")
  }
}

// At message time
msg.padding = padPool[padIndex++]
```

**Pros:**
- ✓ Maximum entropy (fully random pads)
- ✓ No correlation between pads (each independent)
- ✓ Highest security (unpredictable)

**Cons:**
- ✗ BLOCKS at startup if entropy pool depleted
- ✗ Non-deterministic (hard to test, can't reproduce)
- ✗ May fail at startup (entropy starvation)
- ✗ Slow on systems with weak entropy source

**Security:** ✓ Excellent. Pads are random and independent.

**Use case:** High-security environments with good entropy (server with /dev/urandom backed by hardware RNG).

---

### Option C: Hybrid (RECOMMENDED)

```go
// At agent startup (once per agent lifetime)
// Step 1: Get SINGLE entropy value from OS
masterKey := make([]byte, 32)
_, err := rand.Read(masterKey)  // Blocks once, for 32 bytes
if err != nil {
  log.Fatal("cannot read entropy")
}

// Step 2: Derive all pads from that one entropy value
padPool := make([][]byte, PAD_POOL_SIZE)
for i := 0; i < PAD_POOL_SIZE; i++ {
  // Use counter-mode derivation: HMAC(key, counter)
  // Different counter = different pad, all independent
  h := hmac.New(sha256.New, masterKey)
  h.Write([]byte{byte(i >> 56), byte(i >> 48), ..., byte(i)})  // Write counter as bytes
  padPool[i] = h.Sum(nil)[0:PAD_SIZE]
}

// At message time
msg.padding = padPool[padIndex++]
```

**Pros:**
- ✓ ONE entropy call (fast, doesn't block)
- ✓ Pads are independent (different counters → different HMACs)
- ✓ Deterministic given the master key (reproducible for testing)
- ✓ Testable (inject known masterKey → known pads)
- ✓ Resistant to key compromise (agent respawns, gets new masterKey)

**Cons:**
- ✗ All pads derived from one key (if key leaked, all pads derivable)
- ✗ Less random than Option B (but still perfectly adequate for padding)

**Security:** ✓ Excellent. HMAC(key, counter) gives independent pads with single entropy source.

**Use case:** Production systems. Best balance of security, performance, and testability.

---

## Comparison Table

| Aspect | Option A | Option B | Option C |
|--------|----------|----------|----------|
| Entropy calls | 0 | 1000 | 1 |
| Startup latency | Minimal (1ms) | **Slow (100-500ms)** | Fast (1-10ms) |
| Deterministic | ✓ Yes | ✗ No | ✓ Yes (with seed) |
| Testable | ✓ High | ✗ Low | ✓ High |
| Randomness | Pseudo-random | True random | Crypto random |
| Pad independence | Medium (counter) | High | High (HMAC) |
| If masterKey leaks | All pads leaked | N/A | All pads leaked |
| Agent respawn | Gets new key | N/A | Gets new key |

---

## Recommended: Option C Implementation

### Go Implementation

```go
package padding

import (
  "crypto/hmac"
  "crypto/rand"
  "crypto/sha256"
  "encoding/binary"
)

const (
  PAD_POOL_SIZE = 1000
  PAD_SIZE      = 44  // max padding needed for 512-byte messages
)

type PadGenerator struct {
  masterKey []byte
  padPool   [][]byte
  index     int
}

// NewPadGenerator creates a pad pool from system entropy
func NewPadGenerator() (*PadGenerator, error) {
  // Step 1: Read ONE 32-byte entropy value
  masterKey := make([]byte, 32)
  _, err := rand.Read(masterKey)
  if err != nil {
    return nil, err
  }

  // Step 2: Derive all pads from masterKey
  pg := &PadGenerator{
    masterKey: masterKey,
    padPool:   make([][]byte, PAD_POOL_SIZE),
    index:     0,
  }

  for i := 0; i < PAD_POOL_SIZE; i++ {
    pg.padPool[i] = pg.derivePad(uint64(i))
  }

  return pg, nil
}

// derivePad computes HMAC(masterKey, counter) to get pad
func (pg *PadGenerator) derivePad(counter uint64) []byte {
  h := hmac.New(sha256.New, pg.masterKey)
  
  // Write counter as 8 bytes big-endian
  var buf [8]byte
  binary.BigEndian.PutUint64(buf[:], counter)
  h.Write(buf[:])
  
  // Return first PAD_SIZE bytes of HMAC output
  digest := h.Sum(nil)
  pad := make([]byte, PAD_SIZE)
  copy(pad, digest[:PAD_SIZE])
  return pad
}

// NextPad returns the next pad from pool
func (pg *PadGenerator) NextPad() []byte {
  if pg.index >= PAD_POOL_SIZE {
    panic("pad pool exhausted—agent should respawn")
  }
  pad := pg.padPool[pg.index]
  pg.index++
  return pad
}

// RemainingPads returns count of pads before exhaustion
func (pg *PadGenerator) RemainingPads() int {
  return PAD_POOL_SIZE - pg.index
}
```

### Usage in Message Construction

```go
type Message struct {
  Header  [32]byte
  Payload []byte
  Padding []byte
}

// ConstructMessage fills in padding from generator
func ConstructMessage(payload []byte, padGen *PadGenerator) *Message {
  payloadLen := len(payload)
  if payloadLen > 468 {
    panic("payload too large")
  }

  paddingSize := 512 - 32 - payloadLen  // 512 = total, 32 = header
  padding := make([]byte, paddingSize)

  // Strategy 1: Use pre-generated pads
  if padGen != nil {
    copy(padding, padGen.NextPad())
  } else {
    // Fallback: deterministic padding (ZEROS)
    // Don't do this in production—reduces security
  }

  msg := &Message{
    Payload: payload,
    Padding: padding,
  }
  
  // Encrypt entire message (header + payload + padding)
  // Ciphertext is always 512 bytes
  
  return msg
}
```

### Testing with Option C

```go
func TestPadGenerationDeterministic(t *testing.T) {
  // Deterministic test: same seed → same pads
  
  // Fake entropy: inject known key
  masterKey := []byte("test-master-key-32-bytes-------")  // 32 bytes
  
  pg1 := &PadGenerator{masterKey: masterKey, padPool: make([][]byte, PAD_POOL_SIZE)}
  pg2 := &PadGenerator{masterKey: masterKey, padPool: make([][]byte, PAD_POOL_SIZE)}
  
  // Derive pads for both
  for i := 0; i < PAD_POOL_SIZE; i++ {
    pg1.padPool[i] = pg1.derivePad(uint64(i))
    pg2.padPool[i] = pg2.derivePad(uint64(i))
  }
  
  // Both should produce identical pads
  for i := 0; i < PAD_POOL_SIZE; i++ {
    if !bytes.Equal(pg1.padPool[i], pg2.padPool[i]) {
      t.Fatalf("pad %d differs: pg1=%x, pg2=%x", i, pg1.padPool[i], pg2.padPool[i])
    }
  }
}

func TestMessageConstantSize(t *testing.T) {
  // All messages should be 512 bytes, regardless of payload
  
  padGen, _ := NewPadGenerator()
  
  for _, payloadSize := range []int{0, 100, 200, 468} {
    payload := make([]byte, payloadSize)
    msg := ConstructMessage(payload, padGen)
    
    totalSize := 32 + len(msg.Payload) + len(msg.Padding)
    if totalSize != 512 {
      t.Fatalf("payload_size=%d: total_size=%d, want 512", payloadSize, totalSize)
    }
  }
}
```

---

## Agent Respawn Strategy

When does a DMZ agent respawn?

```
Respawn condition:
  - Time elapsed > 1 hour, OR
  - Remaining pads < 10 (about to exhaust)
```

```go
func (pg *PadGenerator) ShouldRespawn() bool {
  return pg.RemainingPads() < 10  // Warn at 990 pads used
}

// In agent main loop:
for {
  if padGen.ShouldRespawn() {
    log.Warn("agent exhausting pad pool, respawning...")
    return  // Kill agent, supervisor spawns new one
  }
  
  // Process next tick
  ...
}
```

**Why respawn?**
- ✓ Fresh entropy (new masterKey)
- ✓ Fresh pad pool (1000 more pads)
- ✓ Clean state (any compromise is bounded)
- ✓ No key rotation headache

**Respawn frequency:**
- Worst case: high-frequency agent (1000 messages/sec) exhausts 1000 pads in 1 second → respawn every second
- Normal case: ~100 msg/sec → respawn every 10 seconds
- DMZ agent: ~10 msg/sec → respawn every 100 seconds (add 1-hour safety limit)

---

## Summary

**Use Option C (Hybrid) for AMF:**

```go
// At agent startup
padGen, err := NewPadGenerator()  // Calls rand.Read(32) once
if err != nil {
  log.Fatal(err)  // Entropy failure → fail fast
}

// At message time
msg.padding = padGen.NextPad()  // O(1) lookup

// At scheduled respawn or exhaustion
if padGen.ShouldRespawn() {
  os.Exit(0)  // Supervisor spawns new agent
}
```

**Benefits:**
- ✓ Fast startup (1 entropy call, 1ms)
- ✓ Fast message time (O(1) pad lookup)
- ✓ Deterministic & testable
- ✓ Cryptographically sound
- ✓ Bounded compromise (respawn to refresh)
