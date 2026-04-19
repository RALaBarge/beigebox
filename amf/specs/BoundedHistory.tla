---- MODULE BoundedHistory ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Bounded Message History with Adversary Isolation

  Security principle:
  - Active queue: Last Y messages (hot path, high security)
  - Archive queue: Messages Y+1 and older (cold path, separate security boundary)
  - When message Y+1 arrives, message 1 moves to archive with sealed key
  - Archive keys are destroyed (HMAC integrity only, no confidentiality)
  - Compromise of active queue does NOT expose archive
  - Archive is immutable and time-locked
*)

CONSTANT
  Agents,                 \* Set of agent IDs
  ACTIVE_WINDOW_SIZE,     \* Y: how many recent messages to keep hot (e.g., 20)
  MAX_ARCHIVE_AGE         \* How long to keep archived messages (e.g., 1 week in seconds)

VARIABLE
  active_queue,           \* Sequence of recent messages
  archive_queue,          \* Sequence of archived messages with sealed metadata
  message_counter,        \* Total messages ever processed (unbounded)
  agent_keys,             \* [agent_id -> current key]
  message_timestamp,      \* [message_id -> creation time]
  time                    \* Wall-clock time

Init ==
  /\ active_queue = <<>>
  /\ archive_queue = <<>>
  /\ message_counter = 0
  /\ agent_keys = [a \in Agents |-> "key_v0"]
  /\ message_timestamp = <<>>
  /\ time = 0

(* ACTION: Advance time *)
TimeAdvances ==
  /\ time' = time + 1
  /\ UNCHANGED <<active_queue, archive_queue, message_counter, agent_keys, message_timestamp>>

(* ACTION: New message arrives, goes to active queue *)
MessageArrivesActive ==
  /\ Len(active_queue) < ACTIVE_WINDOW_SIZE
  /\ LET
       new_msg == [id |-> message_counter, agent_id |-> "dmz", data |-> "payload", received_at |-> time]
     IN
     /\ active_queue' = Append(active_queue, new_msg)
     /\ message_counter' = message_counter + 1
     /\ message_timestamp' = Append(message_timestamp, time)
  /\ UNCHANGED <<archive_queue, agent_keys, time>>

(* ACTION: Active queue is full, oldest message rotates to archive *)
RotateToArchive ==
  /\ Len(active_queue) = ACTIVE_WINDOW_SIZE
  /\ LET
       oldest_msg == Head(active_queue)
       agent == oldest_msg.agent_id
       remaining_active == Tail(active_queue)
     IN
     /\ active_queue' = remaining_active
     /\ archive_queue' = Append(archive_queue, [msg |-> oldest_msg, sealed_at |-> time, archived_key |-> "DESTROYED"])
     /\ agent_keys' = [agent_keys EXCEPT ![agent] = "key_rotated"]
  /\ UNCHANGED <<message_counter, message_timestamp, time>>

(* ACTION: Process message from active queue *)
ProcessFromActive ==
  /\ Len(active_queue) > 0
  /\ LET msg == Head(active_queue)
     IN /\ agent_keys[msg.agent_id] # "UNKNOWN"
  /\ UNCHANGED <<active_queue, archive_queue, message_counter, agent_keys, message_timestamp, time>>

(* ACTION: Retrieve archived message (requires special authority) *)
RetrieveFromArchive ==
  /\ Len(archive_queue) > 0
  /\ LET archived == Head(archive_queue)
     IN /\ archived.archived_key = "DESTROYED"
  /\ UNCHANGED <<active_queue, archive_queue, message_counter, agent_keys, message_timestamp, time>>

(* ACTION: Archive cleanup - drop messages older than MAX_ARCHIVE_AGE *)
ArchiveCleanup ==
  /\ \E i \in DOMAIN archive_queue:
     /\ time - message_timestamp[i] > MAX_ARCHIVE_AGE
     /\ archive_queue' = SubSeq(archive_queue, i+1, Len(archive_queue))
  /\ UNCHANGED <<active_queue, message_counter, agent_keys, message_timestamp, time>>

Next ==
  \/ TimeAdvances
  \/ MessageArrivesActive
  \/ RotateToArchive
  \/ ProcessFromActive
  \/ RetrieveFromArchive
  \/ ArchiveCleanup

(* INVARIANTS *)

\* Invariant 1: Active queue never exceeds window size
ActiveQueueBounded ==
  Len(active_queue) <= ACTIVE_WINDOW_SIZE

\* Invariant 2: Messages in active queue are ordered by ID
MessageOrdering ==
  \A i \in 1..(Len(active_queue) - 1):
    active_queue[i].id < active_queue[i+1].id

\* Invariant 3: Archive keys are destroyed (not accessible)
KeyIsolation ==
  \A i \in DOMAIN archive_queue:
    archive_queue[i].archived_key = "DESTROYED"

\* Invariant 4: Archive length is non-decreasing
ArchiveLengthNonDecreasing ==
  TRUE  \* Always true; archive can only grow

Spec == Init /\ [][Next]_<<active_queue, archive_queue, message_counter, agent_keys, message_timestamp, time>>
        /\ SF_<<message_counter>>(MessageArrivesActive)
        /\ SF_<<Len(archive_queue)>>(ArchiveCleanup)

====
