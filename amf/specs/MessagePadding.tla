---- MODULE MessagePadding ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Message Padding & Constant-Time Messaging

  Goals:
  1. Constant message size (header + payload + padding = fixed total)
     - Attacker can't tell message content size from ciphertext
  2. Pre-generated secure pads (at startup, for lifetime)
     - No entropy needed at runtime (faster, deterministic)
     - Each agent gets pre-allocated pad sequences
  3. In-place encrypted updates
     - Same-sized payload can be encrypted in-place with fresh nonce
     - No allocation/deallocation (reduces timing side-channels)

  Message structure:
  ┌─────────────────────────────────────────────────┐
  │ HEADER (fixed 32 bytes)                         │
  │ - agent_id (variable length)                    │
  │ - message_id (8 bytes)                          │
  │ - tick (8 bytes)                                │
  │ - nonce (12 bytes, IV for AES-GCM)              │
  │ - flags (3 bytes: padding_type, compression)    │
  ├─────────────────────────────────────────────────┤
  │ PAYLOAD (variable, 0 to MAX_PAYLOAD)            │
  ├─────────────────────────────────────────────────┤
  │ PADDING (fills to TOTAL_SIZE)                   │
  │ - padding_type determines padding content       │
  └─────────────────────────────────────────────────┘

  Total size = HEADER_SIZE + variable payload + padding = Always 512 bytes

  Padding types:
  - ZEROS: All 0x00 bytes
  - RANDOM: From pre-generated secure pool
  - DETERMINISTIC: HMAC(key, nonce)
  - CHACHA: ChaCha20(key, nonce)
*)

CONSTANT
  Agents,                \* Set of agent IDs
  HEADER_SIZE,           \* 32 bytes
  MAX_PAYLOAD_SIZE,      \* 468 bytes
  MESSAGE_SIZE,          \* 512 bytes total
  PADDING_TYPES,         \* {"ZEROS", "RANDOM", "DETERMINISTIC", "CHACHA"}
  PAD_POOL_SIZE          \* Pre-generate X pads per agent (e.g., 1000)

VARIABLE
  agent_pad_pools,       \* [agent_id -> [index -> pad_bytes]]
  message_log,           \* Sequence of messages sent
  pad_index_per_agent,   \* [agent_id -> current_index]
  encryption_key,        \* [agent_id -> key]
  nonce_generator,       \* [agent_id -> next_nonce_value]
  time                   \* Wall-clock time

Init ==
  /\ agent_pad_pools = [a \in Agents |-> [i \in 1..PAD_POOL_SIZE |-> "pad_" \o a]]
  /\ message_log = <<>>
  /\ pad_index_per_agent = [a \in Agents |-> 1]
  /\ encryption_key = [a \in Agents |-> "key_" \o a]
  /\ nonce_generator = [a \in Agents |-> 0]
  /\ time = 0

(* ACTION: Advance time *)
TimeAdvances ==
  /\ time' = time + 1
  /\ UNCHANGED <<agent_pad_pools, message_log, pad_index_per_agent, encryption_key, nonce_generator>>

(* Helper: Construct message with padding *)
ConstructMessage(agent, payload_size, padding_type) ==
  LET
    msg_id == IF message_log = <<>> THEN 0 ELSE message_log[Len(message_log)].header.message_id + 1
    pad_size == MESSAGE_SIZE - HEADER_SIZE - payload_size
    header == [
      agent_id |-> agent,
      message_id |-> msg_id,
      tick |-> time,
      nonce |-> nonce_generator[agent],
      flags |-> padding_type
    ]
  IN
  [
    header |-> header,
    payload_size |-> payload_size,
    padding_size |-> pad_size,
    total_size |-> MESSAGE_SIZE
  ]

(* ACTION: Send message with padding *)
SendMessage ==
  \E agent \in Agents:
    \E payload_size \in 0..MAX_PAYLOAD_SIZE:
      \E padding_type \in PADDING_TYPES:
        LET
          msg == ConstructMessage(agent, payload_size, padding_type)
        IN
        /\ message_log' = Append(message_log, msg)
        /\ IF padding_type = "RANDOM"
           THEN pad_index_per_agent' = [pad_index_per_agent EXCEPT ![agent] = (pad_index_per_agent[agent] % PAD_POOL_SIZE) + 1]
           ELSE pad_index_per_agent' = pad_index_per_agent
        /\ nonce_generator' = [nonce_generator EXCEPT ![agent] = nonce_generator[agent] + 1]
        /\ UNCHANGED <<agent_pad_pools, encryption_key, time>>

(* ACTION: In-place update of payload (same size, new nonce) *)
UpdatePayloadInPlace ==
  \E msg_idx \in DOMAIN message_log:
    LET
      old_msg == message_log[msg_idx]
      agent == old_msg.header.agent_id
      new_msg == [
        header |-> [old_msg.header EXCEPT !.nonce = nonce_generator[agent]],
        payload_size |-> old_msg.payload_size,
        padding_size |-> old_msg.padding_size,
        total_size |-> old_msg.total_size
      ]
    IN
    /\ message_log' = [message_log EXCEPT ![msg_idx] = new_msg]
    /\ nonce_generator' = [nonce_generator EXCEPT ![agent] = nonce_generator[agent] + 1]
    /\ UNCHANGED <<agent_pad_pools, pad_index_per_agent, encryption_key, time>>

(* ACTION: Pad pool exhaustion warning *)
PadPoolWarning ==
  \E agent \in Agents:
    /\ pad_index_per_agent[agent] >= (PAD_POOL_SIZE - 10)
    /\ UNCHANGED <<agent_pad_pools, message_log, pad_index_per_agent, encryption_key, nonce_generator, time>>

(* ACTION: Regenerate pad pool at agent respawn *)
RegeneratePadPool ==
  \E agent \in Agents:
    /\ agent_pad_pools' = [agent_pad_pools EXCEPT ![agent] = [i \in 1..PAD_POOL_SIZE |-> "pad_" \o agent]]
    /\ pad_index_per_agent' = [pad_index_per_agent EXCEPT ![agent] = 1]
    /\ encryption_key' = [encryption_key EXCEPT ![agent] = "key_" \o agent]
    /\ nonce_generator' = [nonce_generator EXCEPT ![agent] = 0]
    /\ UNCHANGED <<message_log, time>>

Next ==
  \/ TimeAdvances
  \/ SendMessage
  \/ UpdatePayloadInPlace
  \/ PadPoolWarning
  \/ RegeneratePadPool

(* INVARIANTS *)

\* Invariant 1: All messages are exactly MESSAGE_SIZE bytes
ConstantMessageSize ==
  \A i \in DOMAIN message_log: message_log[i].total_size = MESSAGE_SIZE

\* Invariant 2: Nonces never repeat for same agent
NonceUniqueness ==
  \A i, j \in DOMAIN message_log:
    (i # j /\ message_log[i].header.agent_id = message_log[j].header.agent_id)
    => message_log[i].header.nonce # message_log[j].header.nonce

\* Invariant 3: Pad pool index never exceeds pool size
PadIndexBounded ==
  \A agent \in Agents: pad_index_per_agent[agent] <= PAD_POOL_SIZE

\* Invariant 4: Message structure is always valid
MessageStructureValid ==
  \A i \in DOMAIN message_log:
    /\ message_log[i].payload_size <= MAX_PAYLOAD_SIZE
    /\ message_log[i].padding_size = MESSAGE_SIZE - HEADER_SIZE - message_log[i].payload_size
    /\ message_log[i].total_size = MESSAGE_SIZE

\* Invariant 5: Payload + padding always equals constant
PayloadPaddingSumConstant ==
  \A i \in DOMAIN message_log:
    message_log[i].payload_size + message_log[i].padding_size = MESSAGE_SIZE - HEADER_SIZE

Spec == Init /\ [][Next]_<<agent_pad_pools, message_log, pad_index_per_agent, encryption_key, nonce_generator, time>>
        /\ SF_<<Len(message_log)>>(SendMessage)

====
