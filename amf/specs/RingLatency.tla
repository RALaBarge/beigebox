---- MODULE RingLatency ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for AMF Ring Queue with Latency Tracking & Fault Tolerance

  Ring properties:
  - N agents in a ring (A→B→C→...→A)
  - "at_bat" agent: current turn (runs tick action)
  - "on_deck" agent: next turn (monitors latency, marks late)
  - Tick arrives on time OR late (still accepted, but marked)
  - Exponential backoff: agent gets 3 chances before circuit break
  - Watchdog timer: if agent doesn't tick within deadline, force advance

  Threat model:
  - Slowness DoS: attacker deliberately delays agent (controlled network)
  - Silent failure: agent stops ticking but doesn't crash
  - Churn explosion: tight latency threshold causes cascading failures
*)

CONSTANT
  Agents,           \* Set of all agent IDs {1, 2, 3, ...}
  MAX_FAILURES,     \* Consecutive failures before circuit break (e.g., 3)
  TICK_TIMEOUT_MS,  \* Latency threshold in milliseconds (e.g., 100ms)
  WATCHDOG_TIMEOUT  \* Force advance if no tick in this many ticks (e.g., 10)

VARIABLE
  ring,                 \* Ring ordering [A, B, C, D, ...]
  at_bat,               \* Index in ring of current agent
  on_deck,              \* Index in ring of next agent
  tick_counter,         \* Monotonic tick number
  time,                 \* Wall-clock time (for timeout calculations)
  message_buffer,       \* {<<tick, agent_id, payload, arrived_at>>}
  late_count,           \* <<agent_id>> -> count of consecutive late ticks
  agent_status,         \* <<agent_id>> -> "active" | "degraded" | "circuit_broken"
  tick_start_time,      \* When current tick started (for latency calculation)
  watchdog_timeout,     \* Ticks remaining before force-advance (watchdog fires)
  exponential_backoff   \* <<agent_id>> -> backoff delay (0 = normal, 1+ = exponential)

Init ==
  /\ ring = <<1, 2, 3, 4>>
  /\ at_bat = 1
  /\ on_deck = 2
  /\ tick_counter = 0
  /\ time = 0
  /\ message_buffer = {}
  /\ late_count = [a \in Agents |-> 0]
  /\ agent_status = [a \in Agents |-> "active"]
  /\ tick_start_time = 0
  /\ watchdog_timeout = WATCHDOG_TIMEOUT
  /\ exponential_backoff = [a \in Agents |-> 0]

(* ACTION: Advance time (watchdog decrements) *)
TimeAdvances ==
  /\ time' = time + 1
  /\ watchdog_timeout' = IF watchdog_timeout > 0 THEN watchdog_timeout - 1 ELSE 0
  /\ UNCHANGED <<ring, at_bat, on_deck, tick_counter, message_buffer, late_count, agent_status, tick_start_time, exponential_backoff>>

(* ACTION: Tick starts - at_bat agent has a turn *)
TickStart ==
  /\ agent_status[ring[at_bat]] = "active"
  /\ tick_start_time' = time
  /\ on_deck' = IF Len(ring) > 1 THEN (at_bat % Len(ring)) + 1 ELSE at_bat
  /\ UNCHANGED <<ring, at_bat, tick_counter, time, message_buffer, late_count, agent_status, exponential_backoff, watchdog_timeout>>

(* ACTION: Agent submits message (may be late or on-time) *)
AgentSubmitsMessage ==
  LET
    current_agent == ring[at_bat]
    arrival_time == time
    expected_time == tick_start_time + 1
    is_late == arrival_time > expected_time
  IN
  /\ message_buffer' = message_buffer \union {<<tick_counter, current_agent, "payload", is_late>>}
  /\ IF is_late
     THEN late_count' = [late_count EXCEPT ![current_agent] = late_count[current_agent] + 1]
     ELSE late_count' = [late_count EXCEPT ![current_agent] = 0]
  /\ UNCHANGED <<ring, at_bat, on_deck, tick_counter, time, agent_status, tick_start_time, exponential_backoff, watchdog_timeout>>

(* ACTION: On-deck agent marks at_bat as late (latency detection with exponential backoff) *)
OnDeckDetectsLate ==
  LET
    at_bat_agent == ring[at_bat]
    on_deck_agent == ring[on_deck]
    current_time == time
    deadline == tick_start_time + TICK_TIMEOUT_MS
    new_late_count == late_count[at_bat_agent] + 1
    new_backoff == IF new_late_count >= MAX_FAILURES THEN 2 ^ (new_late_count - MAX_FAILURES) ELSE exponential_backoff[at_bat_agent]
  IN
  /\ agent_status[on_deck_agent] = "active"
  /\ current_time >= deadline
  /\ late_count' = [late_count EXCEPT ![at_bat_agent] = new_late_count]
  /\ exponential_backoff' = [exponential_backoff EXCEPT ![at_bat_agent] = new_backoff]
  /\ IF new_late_count >= MAX_FAILURES + 2  \* 2 extra chances with backoff before circuit break
     THEN agent_status' = [agent_status EXCEPT ![at_bat_agent] = "circuit_broken"]
     ELSE agent_status' = [agent_status EXCEPT ![at_bat_agent] = IF new_late_count >= MAX_FAILURES THEN "degraded" ELSE "active"]
  /\ UNCHANGED <<ring, at_bat, on_deck, tick_counter, time, message_buffer, tick_start_time, watchdog_timeout>>

(* ACTION: Advance to next agent (ring continues even if current is late) *)
AdvanceTick ==
  LET
    next_at_bat == IF Len(ring) > 1 THEN (at_bat % Len(ring)) + 1 ELSE 1
    next_on_deck == IF Len(ring) > 1 THEN (next_at_bat % Len(ring)) + 1 ELSE 1
  IN
  /\ agent_status[ring[next_at_bat]] # "circuit_broken"  \* Allow active or degraded
  /\ tick_counter' = tick_counter + 1
  /\ at_bat' = next_at_bat
  /\ on_deck' = next_on_deck
  /\ watchdog_timeout' = WATCHDOG_TIMEOUT  \* Reset watchdog on successful advance
  /\ UNCHANGED <<ring, time, message_buffer, late_count, agent_status, tick_start_time, exponential_backoff>>

(* ACTION: Watchdog timeout - force advance if agent stuck *)
WatchdogForceAdvance ==
  /\ watchdog_timeout = 0
  /\ LET
       next_at_bat == IF Len(ring) > 1 THEN (at_bat % Len(ring)) + 1 ELSE 1
       next_on_deck == IF Len(ring) > 1 THEN (next_at_bat % Len(ring)) + 1 ELSE 1
     IN
     /\ agent_status[ring[next_at_bat]] # "circuit_broken"
     /\ tick_counter' = tick_counter + 1
     /\ at_bat' = next_at_bat
     /\ on_deck' = next_on_deck
     /\ watchdog_timeout' = WATCHDOG_TIMEOUT  \* Reset watchdog
  /\ UNCHANGED <<ring, time, message_buffer, late_count, agent_status, tick_start_time, exponential_backoff>>

(* ACTION: Circuit breaker - remove failed agent from ring *)
EscapeRingBuffer ==
  LET
    failed_agent == ring[at_bat]
    new_ring == SelectSeq(ring, LAMBDA a: a # failed_agent)
  IN
  /\ agent_status[failed_agent] = "circuit_broken"
  /\ Len(new_ring) >= 1
  /\ ring' = new_ring
  /\ at_bat' = IF at_bat > Len(new_ring) THEN 1 ELSE at_bat
  /\ on_deck' = IF Len(new_ring) > 1 THEN (at_bat' % Len(new_ring)) + 1 ELSE at_bat'
  /\ UNCHANGED <<tick_counter, time, message_buffer, late_count, agent_status, tick_start_time>>

Next ==
  \/ TimeAdvances
  \/ TickStart
  \/ AgentSubmitsMessage
  \/ OnDeckDetectsLate
  \/ AdvanceTick
  \/ WatchdogForceAdvance
  \/ EscapeRingBuffer

(* INVARIANTS *)

\* Invariant 1: Ring is never empty
RingNonEmpty ==
  Len(ring) >= 1

\* Invariant 2: At_bat and on_deck are valid ring positions
RingPositionsValid ==
  /\ 1 <= at_bat /\ at_bat <= Len(ring)
  /\ 1 <= on_deck /\ on_deck <= Len(ring)
  /\ (Len(ring) > 1) => (at_bat # on_deck)

\* Invariant 3: Circuit-broken agents are not in ring
CircuitBreakerEnforced ==
  \A i \in DOMAIN ring: agent_status[ring[i]] = "active"

\* Invariant 4: Late count is non-negative
LateCountNonNegative ==
  \A a \in Agents:
    late_count[a] >= 0

Spec == Init /\ [][Next]_<<ring, at_bat, on_deck, tick_counter, time, message_buffer, late_count, agent_status, tick_start_time, watchdog_timeout, exponential_backoff>>
        /\ SF_<<tick_counter>>(AdvanceTick)
        /\ SF_<<watchdog_timeout>>(WatchdogForceAdvance)

====
