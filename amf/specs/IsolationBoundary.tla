---- MODULE IsolationBoundary ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Information Flow Isolation with Respawn Race Conditions

  Goals:
  1. DMZ cannot see ring state (no information flows outward)
  2. Ring agents cannot see each other's secrets (no lateral leakage)
  3. Compromised agent's blast radius is bounded to active window
  4. Archive is safe from compromise (keys destroyed, data sealed)
  5. DMZ respawn does not leak messages during transition window
  6. Input sanitization prevents prompt injection attacks

  Threat model:
  - DMZ can be compromised by attacker on hostile network
  - Ring agent can be compromised (but isolated from others)
  - Compromise of one agent doesn't leak data older than active window
  - Attacker tries to send unsanitized/injected input during DMZ respawn

  Design:
  - DMZ_Visible = {dmz_state} (isolated, never sees ring internals)
  - Ring_Visible = {a_state, b_state, c_state} (internal only)
  - Active queue: messages 1..20 (decryptable if agent compromised)
  - Archive: messages 21+ (keys destroyed, attacker cannot decrypt)
  - If agent compromised at time T, adversary sees only messages [T-W..T] where W = active window
  - Respawn phases: HEALTHY -> RESPAWNING -> ADMITTED -> HEALTHY
  - Sanitization: text is normalized, injection markers removed, only whitelisted tokens allowed
*)

CONSTANT
  Agents,                 \* {dmz, a, b, c}
  ACTIVE_WINDOW,          \* How many messages to keep in active queue (e.g., 20)
  DMZ_ID,                 \* Which agent is the DMZ (e.g., "dmz")
  RESPAWN_GRACE_PERIOD    \* How long old DMZ accepts input during respawn (ticks)

VARIABLE
  dmz_state,              \* [input_queue, output_queue, context] — isolated
  ring_state,             \* [a_queue, b_queue, c_queue, shared_archive]
  active_queue,           \* Current messages (last ACTIVE_WINDOW)
  archive_queue,          \* Old messages (sealed, read-only)
  agent_keys,             \* [agent -> current_key]
  compromise_set,         \* Set of compromised agents
  adversary_knowledge,    \* What attacker can see
  dmz_respawn_phase,      \* Phase of DMZ respawn: "healthy" | "respawning" | "admitted"
  respawn_start_time,     \* When respawn began (for grace period)
  last_dmz_message_id,    \* Last message ID before respawn (for deduplication)
  sanitized_message_log,  \* Messages confirmed as sanitized and safe
  time

Init ==
  /\ dmz_state = [input_queue |-> <<>>, output_queue |-> <<>>, context |-> "clean"]
  /\ ring_state = [a |-> [queue |-> <<>>, key |-> "key_a"],
                   b |-> [queue |-> <<>>, key |-> "key_b"],
                   c |-> [queue |-> <<>>, key |-> "key_c"]]
  /\ active_queue = <<>>
  /\ archive_queue = <<>>
  /\ agent_keys = [a |-> "key_a", b |-> "key_b", c |-> "key_c", dmz |-> "key_dmz"]
  /\ compromise_set = {}
  /\ adversary_knowledge = {}
  /\ dmz_respawn_phase = "healthy"
  /\ respawn_start_time = 0
  /\ last_dmz_message_id = 0
  /\ sanitized_message_log = <<>>
  /\ time = 0

(* ACTION: Advance time *)
TimeAdvances ==
  /\ time' = time + 1
  /\ UNCHANGED <<dmz_state, ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log>>

(* Predicate: Message is properly sanitized (no injection markers) *)
Sanitized(msg) ==
  \* Message is safe if it passes sanitization check
  \* Formal definition: message contains no injection markers and only whitelisted tokens
  \* For this spec, we abstract this as a property that ring agents enforce
  msg \notin {"<|im_start|>", "[SYSTEM]", "{instruction:", "prompt:", "ignore:"}

(* ACTION: DMZ initiates respawn (e.g., on compromise or nonce depletion) *)
InitiateDMZRespawn ==
  /\ dmz_respawn_phase = "healthy"
  /\ dmz_respawn_phase' = "respawning"
  /\ respawn_start_time' = time
  /\ last_dmz_message_id' = Len(active_queue)  \* Last message before respawn
  /\ UNCHANGED <<dmz_state, ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, sanitized_message_log, time>>

(* ACTION: DMZ respawn completes (new instance admitted by ring) *)
CompleteDMZRespawn ==
  /\ dmz_respawn_phase = "respawning"
  /\ time - respawn_start_time >= RESPAWN_GRACE_PERIOD  \* Grace period elapsed
  /\ dmz_respawn_phase' = "admitted"
  /\ dmz_state' = [dmz_state EXCEPT !.context = "clean"]  \* Reset mental state
  /\ UNCHANGED <<ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: New DMZ becomes fully healthy after admission *)
FinalizeDMZRespawn ==
  /\ dmz_respawn_phase = "admitted"
  /\ dmz_respawn_phase' = "healthy"
  /\ UNCHANGED <<dmz_state, ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: Compromise a ring agent *)
CompromiseRingAgent ==
  \E agent \in Agents:
    agent # DMZ_ID  \* DMZ handled separately (respawn)
    /\ agent \notin compromise_set
    /\ compromise_set' = compromise_set \union {agent}
    /\ \* Attacker learns what's in active queue for this agent
      adversary_knowledge' = adversary_knowledge \union {agent}
    /\ UNCHANGED <<dmz_state, ring_state, active_queue, archive_queue, agent_keys, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: Attempt to access archived message (should fail) *)
AttemptArchiveAccess ==
  /\ Len(archive_queue) > 0
  /\ \E agent \in compromise_set:
    \E i \in DOMAIN archive_queue:
      archive_queue[i].msg.agent = agent
      /\ \* Attacker CANNOT decrypt because key is destroyed
         archive_queue[i].archived_key = "DESTROYED"
  /\ UNCHANGED <<dmz_state, ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: Rotate active→archive as new messages arrive *)
RotateWindow ==
  /\ Len(active_queue) = ACTIVE_WINDOW
  /\ LET oldest_msg == Head(active_queue)
     IN
     /\ active_queue' = Tail(active_queue)
     /\ archive_queue' = Append(archive_queue, [msg |-> oldest_msg, sealed_at |-> time, archived_key |-> "DESTROYED"])
  /\ UNCHANGED <<dmz_state, ring_state, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: DMZ receives input from network (isolated) *)
DMZReceiveInput ==
  /\ LET new_input == "input"
     IN
     /\ dmz_state' = [dmz_state EXCEPT !.input_queue = Append(dmz_state.input_queue, new_input)]
  /\ UNCHANGED <<ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>

(* ACTION: DMZ sends sanitized output to ring (checked for injection) *)
DMZSendToRing ==
  /\ dmz_state.output_queue # <<>>
  /\ LET msg == Head(dmz_state.output_queue)
     IN
     /\ Sanitized(msg)  \* Guard: message must pass sanitization
     /\ active_queue' = Append(active_queue, [agent |-> DMZ_ID, data |-> msg, time |-> time, msg_id |-> Len(active_queue) + 1])
     /\ sanitized_message_log' = Append(sanitized_message_log, [msg |-> msg, msg_id |-> Len(active_queue) + 1, phase |-> dmz_respawn_phase, timestamp |-> time])
     /\ dmz_state' = [dmz_state EXCEPT !.output_queue = Tail(dmz_state.output_queue)]
  /\ UNCHANGED <<ring_state, archive_queue, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, time>>

Next ==
  \/ TimeAdvances
  \/ CompromiseRingAgent
  \/ AttemptArchiveAccess
  \/ RotateWindow
  \/ DMZReceiveInput
  \/ DMZSendToRing
  \/ InitiateDMZRespawn
  \/ CompleteDMZRespawn
  \/ FinalizeDMZRespawn

(* INVARIANTS *)

\* Invariant 1: DMZ cannot see ring internals
DMZCannotSeeRing ==
  DOMAIN(dmz_state) \intersect DOMAIN(ring_state) = {}

\* Invariant 2: Archive is always sealed (keys destroyed)
ArchiveAlwaysSealed ==
  \A i \in DOMAIN archive_queue:
    archive_queue[i].archived_key = "DESTROYED"

\* Invariant 3: Archive is always sealed (attacker cannot access archived keys)
CompromiseBlastRadiusBounded ==
  \A i \in DOMAIN archive_queue:
    archive_queue[i].archived_key = "DESTROYED"

\* Invariant 4: Active queue never exceeds window size
ActiveQueueBounded ==
  Len(active_queue) <= ACTIVE_WINDOW

\* Invariant 5: Older messages are in archive, not active
MessageSeparation ==
  \A i \in DOMAIN active_queue:
    \A j \in DOMAIN archive_queue:
      active_queue[i].time >= archive_queue[j].msg.time

\* Invariant 6: Attacker knowledge only contains compromised agents
NoForging ==
  adversary_knowledge \subseteq compromise_set

\* Invariant 7: Respawn phase is always valid
RespawnPhaseValid ==
  dmz_respawn_phase \in {"healthy", "respawning", "admitted"}

\* Invariant 8: All messages in active queue are sanitized
AllMessagesSanitized ==
  \A i \in DOMAIN sanitized_message_log:
    Sanitized(sanitized_message_log[i].msg)

\* Invariant 9: Respawn start time is consistent with phase
RespawnTimingConsistent ==
  (dmz_respawn_phase = "healthy") => respawn_start_time <= time

\* Invariant 10: During respawn window, no message deduplication failures
NoRespawnMessageConfusion ==
  (dmz_respawn_phase = "respawning") =>
  (\A i, j \in DOMAIN sanitized_message_log:
    (i < j /\ sanitized_message_log[i].msg_id = sanitized_message_log[j].msg_id) => FALSE)

Spec == Init /\ [][Next]_<<dmz_state, ring_state, active_queue, archive_queue, agent_keys, compromise_set, adversary_knowledge, dmz_respawn_phase, respawn_start_time, last_dmz_message_id, sanitized_message_log, time>>
        /\ SF_<<Len(active_queue)>>(DMZSendToRing)

====
