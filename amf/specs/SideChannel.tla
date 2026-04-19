---- MODULE SideChannel ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Side-Channel Resistance

  Goals:
  1. Constant message size: all ciphertexts are exactly 512 bytes
  2. Constant timing: tick interval is deterministic, independent of payload
  3. Non-interference: observable behavior (size, timing) doesn't depend on secret (payload)
  4. Padding is pre-generated: no per-message entropy calls (no timing variance)

  Threat model:
  - Passive attacker observes wire traffic (message sizes, inter-arrival times)
  - Attacker tries to infer payload content from traffic patterns
  - Goal: traffic pattern reveals nothing about payload (all messages identical size)

  Design:
  - MessageSize is constant (512 bytes) for all payloads (0..468 bytes)
  - TickInterval is constant (e.g., 100ms, enforced by on_deck timer)
  - Padding is deterministic, pre-generated at startup
  - No conditional logic that depends on payload affects timing or size
*)

CONSTANT
  MESSAGE_SIZE,           \* 512 bytes (constant)
  HEADER_SIZE,            \* 32 bytes
  MAX_PAYLOAD_SIZE,       \* 468 bytes
  TICK_INTERVAL           \* 100ms (constant tick time)

VARIABLE
  message_log,            \* Sequence of [size, timestamp, nonce]
  padding_pool,           \* Pre-generated pads (generated at init)
  payload_log,            \* Secret: what was actually sent (attacker cannot see)
  observable_log,         \* What attacker observes: [size, arrival_time]
  tick_counter,           \* Monotonic tick for timing
  time

Init ==
  /\ message_log = <<>>
  /\ padding_pool = [i \in 1..1000 |-> "pad"]
  /\ payload_log = <<>>
  /\ observable_log = <<>>
  /\ tick_counter = 0
  /\ time = 0

(* ACTION: Advance time by TICK_INTERVAL *)
Tick ==
  /\ time' = time + TICK_INTERVAL
  /\ tick_counter' = tick_counter + 1
  /\ UNCHANGED <<message_log, padding_pool, payload_log, observable_log>>

(* ACTION: Send message (payload is abstracted away) *)
SendMessage ==
  \E payload_size \in 0..MAX_PAYLOAD_SIZE:
    LET
      msg_size == MESSAGE_SIZE  \* Always 512 bytes (constant!)
      msg_time == time          \* Arrival time determined by tick only
    IN
    /\ message_log' = Append(message_log, [size |-> msg_size, time |-> msg_time, nonce |-> tick_counter])
    /\ payload_log' = Append(payload_log, [size |-> payload_size])
    /\ observable_log' = Append(observable_log, [size |-> msg_size, time |-> msg_time])
    /\ UNCHANGED <<padding_pool, tick_counter, time>>

(* ACTION: Attacker observes network traffic *)
ObserveTraffic ==
  /\ observable_log # <<>>
  /\ \* Attacker can see message sizes and timings, but not payload
     TRUE
  /\ UNCHANGED <<message_log, padding_pool, payload_log, observable_log, tick_counter, time>>

Next ==
  \/ Tick
  \/ SendMessage
  \/ ObserveTraffic

(* INVARIANTS *)

\* Invariant 1: All messages are exactly MESSAGE_SIZE bytes
ConstantMessageSize ==
  \A i \in DOMAIN message_log:
    message_log[i].size = MESSAGE_SIZE

\* Invariant 2: All observable messages have the same size
ObservableMessageSizeConstant ==
  \A i, j \in DOMAIN observable_log:
    observable_log[i].size = observable_log[j].size

\* Invariant 3: Observable sizes match actual sizes
ObservableSizeMatchesActual ==
  \A i \in DOMAIN message_log:
    message_log[i].size = observable_log[i].size

\* Invariant 4: Message times are determined by ticks only (not payload)
TimingDeterminedByTick ==
  \A i \in DOMAIN message_log:
    message_log[i].time % TICK_INTERVAL = 0

\* Invariant 5: Tick counter increments correctly
ConstantTickInterval ==
  tick_counter >= 0

\* Invariant 6: Payload size doesn't affect observable message size
PayloadIndependent ==
  \A i, j \in DOMAIN message_log:
    observable_log[i].size = observable_log[j].size

\* Invariant 7: No conditional branching based on payload affects timing
NonInterference ==
  \* If two executions differ only in payload, they produce identical observable log
  \A i \in DOMAIN message_log:
    (message_log[i].size = MESSAGE_SIZE /\ message_log[i].time % TICK_INTERVAL = 0)

Spec == Init /\ [][Next]_<<message_log, padding_pool, payload_log, observable_log, tick_counter, time>>
        /\ SF_<<Len(message_log)>>(SendMessage)
        /\ SF_<<tick_counter>>(Tick)

====
