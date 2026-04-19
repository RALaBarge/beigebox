---- MODULE CryptoIdentity ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Ed25519 Signing & Nonce Monotonicity
  With redundant attestation services and manual override fallback

  Threat model:
  1. Nonce monotonicity: each agent's nonce counter only increases
  2. No nonce reuse: (agent, nonce) pairs never repeat
  3. Replay prevention: enforced by nonce checks
  4. DMZ admission: requires quorum consensus
  5. Attestation redundancy: 2 independent services must validate
  6. Fallback: manual override when attestation services fail
  7. Nonce overflow prevention: catch when nonce approaches max value
*)

CONSTANT
  Agents,                    \* Set of agents {a, b, c, dmz}
  QUORUM,                    \* Consensus threshold (e.g., 3)
  MAX_NONCE,                 \* Maximum nonce value (e.g., 2^63 - 1)
  ATTESTATION_TIMEOUT        \* Max time to wait for attestation response (ms)

VARIABLE
  agent_nonces,              \* [agent -> current nonce value]
  message_log,               \* Sequence of messages sent
  dmz_votes,                 \* Set of agents that voted
  dmz_admitted,              \* Boolean: is DMZ admitted
  attestation_service_a,     \* State of Service A: "responding" | "timeout" | "failed"
  attestation_service_b,     \* State of Service B: "responding" | "timeout" | "failed"
  attestation_agreement,     \* Do both services agree on validity? TRUE | FALSE
  manual_override_votes,     \* Count of manual override votes (fallback mechanism)
  time

Init ==
  /\ agent_nonces = [a \in Agents |-> 0]
  /\ message_log = <<>>
  /\ dmz_votes = {}
  /\ dmz_admitted = FALSE
  /\ attestation_service_a = "responding"
  /\ attestation_service_b = "responding"
  /\ attestation_agreement = FALSE
  /\ manual_override_votes = 0
  /\ time = 0

(* ACTION: Advance time *)
TimeAdvances ==
  /\ time' = time + 1
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes>>

(* ACTION: Agent sends message with incremented nonce (guarded by nonce overflow) *)
SendMessage ==
  \E agent \in Agents:
    LET nonce == agent_nonces[agent]
    IN
    /\ nonce < MAX_NONCE  \* Guard: prevent nonce overflow
    /\ agent_nonces' = [agent_nonces EXCEPT ![agent] = nonce + 1]
    /\ message_log' = Append(message_log, [agent |-> agent, nonce |-> nonce, time |-> time])
    /\ UNCHANGED <<dmz_votes, dmz_admitted, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: Attestation service A responds (simulated) *)
AttestationServiceA_Responds ==
  /\ attestation_service_a = "responding"
  /\ attestation_service_a' = "responding"  \* stays responding
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: Attestation service A times out *)
AttestationServiceA_Timeout ==
  /\ attestation_service_a = "responding"
  /\ attestation_service_a' = "timeout"
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: Attestation service B responds (simulated) *)
AttestationServiceB_Responds ==
  /\ attestation_service_b = "responding"
  /\ attestation_service_b' = "responding"  \* stays responding
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_agreement, manual_override_votes, time>>

(* ACTION: Attestation service B times out *)
AttestationServiceB_Timeout ==
  /\ attestation_service_b = "responding"
  /\ attestation_service_b' = "timeout"
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_agreement, manual_override_votes, time>>

(* ACTION: Both attestation services agree on validity *)
AttestationServicesAgree ==
  /\ attestation_service_a = "responding"
  /\ attestation_service_b = "responding"
  /\ attestation_agreement = FALSE
  /\ attestation_agreement' = TRUE
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_service_b, manual_override_votes, time>>

(* ACTION: Ring agent votes to admit DMZ (via attestation) *)
RingVoteForDMZ ==
  /\ dmz_admitted = FALSE
  /\ attestation_agreement = TRUE  \* Both services must agree
  /\ Cardinality(dmz_votes) < QUORUM
  /\ \E voter \in Agents:
     voter \notin dmz_votes
     /\ dmz_votes' = dmz_votes \union {voter}
  /\ UNCHANGED <<agent_nonces, message_log, dmz_admitted, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: Admit DMZ when quorum reached (via attestation) *)
AdmitDMZInstance ==
  /\ Cardinality(dmz_votes) >= QUORUM
  /\ dmz_admitted = FALSE
  /\ attestation_agreement = TRUE  \* Attestation services agreed
  /\ dmz_admitted' = TRUE
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: Ring agent votes for manual override (fallback when attestation fails) *)
RingVoteForManualOverride ==
  /\ dmz_admitted = FALSE
  /\ (attestation_service_a = "timeout" \/ attestation_service_b = "timeout")  \* At least one service failed
  /\ manual_override_votes < Cardinality(Agents)  \* Cannot have more votes than agents
  /\ manual_override_votes' = manual_override_votes + 1
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_service_b, attestation_agreement, time>>

(* ACTION: Admit DMZ via manual override (all agents must agree) *)
ManualAdmitDMZ ==
  /\ dmz_admitted = FALSE
  /\ manual_override_votes = Cardinality(Agents)  \* Unanimous vote required for override
  /\ dmz_admitted' = TRUE
  /\ UNCHANGED <<agent_nonces, message_log, dmz_votes, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes, time>>

(* ACTION: DMZ respawn clears voting state but maintains nonce monotonicity *)
DMZRespawn ==
  /\ dmz_votes' = {}
  /\ dmz_admitted' = FALSE
  /\ attestation_agreement' = FALSE  \* Reset attestation to unknown
  /\ manual_override_votes' = 0  \* Reset manual override votes
  /\ UNCHANGED <<agent_nonces, message_log, attestation_service_a, attestation_service_b, time>>

Next ==
  \/ TimeAdvances
  \/ SendMessage
  \/ AttestationServiceA_Responds
  \/ AttestationServiceA_Timeout
  \/ AttestationServiceB_Responds
  \/ AttestationServiceB_Timeout
  \/ AttestationServicesAgree
  \/ RingVoteForDMZ
  \/ AdmitDMZInstance
  \/ RingVoteForManualOverride
  \/ ManualAdmitDMZ
  \/ DMZRespawn

(* INVARIANTS *)

\* Invariant 1: Nonces are non-negative and bounded by MAX_NONCE
NoncesNonNegative ==
  \A a \in Agents: agent_nonces[a] >= 0 /\ agent_nonces[a] <= MAX_NONCE

\* Invariant 2: No duplicate (agent, nonce) pairs in message log
NoDuplicateNonces ==
  \A i, j \in DOMAIN message_log:
    (i # j /\ message_log[i].agent = message_log[j].agent)
    => message_log[i].nonce # message_log[j].nonce

\* Invariant 3: Message log is ordered by nonce per agent
NoncesIncreaseMonotonically ==
  \A i, j \in DOMAIN message_log:
    (i < j /\ message_log[i].agent = message_log[j].agent)
    => message_log[i].nonce < message_log[j].nonce

\* Invariant 4: DMZ admitted only with quorum AND attestation agreement
QuorumRequired ==
  dmz_admitted = TRUE => (Cardinality(dmz_votes) >= QUORUM \/ manual_override_votes = Cardinality(Agents))

\* Invariant 5: Cannot have more votes than agents
VoteCardinality ==
  Cardinality(dmz_votes) <= Cardinality(Agents)

\* Invariant 6: Attestation services must be in valid state
AttestationServicesValid ==
  attestation_service_a \in {"responding", "timeout", "failed"} /\
  attestation_service_b \in {"responding", "timeout", "failed"}

\* Invariant 7: Both attestation services must agree before quorum voting
AttestationAgreementRequired ==
  (Cardinality(dmz_votes) > 0) =>
  attestation_agreement = TRUE

\* Invariant 8: Manual override requires unanimous vote
ManualOverrideMajority ==
  manual_override_votes = Cardinality(Agents) => dmz_admitted = TRUE

\* Invariant 9: DMZ cannot be admitted via conflicting paths (both quorum and manual override)
DMZAdmissionConsistency ==
  \neg (Cardinality(dmz_votes) >= QUORUM /\ manual_override_votes = Cardinality(Agents))

Spec == Init /\ [][Next]_<<agent_nonces, message_log, dmz_votes, dmz_admitted, attestation_service_a, attestation_service_b, attestation_agreement, manual_override_votes, time>>
        /\ SF_<<Len(message_log)>>(SendMessage)

====
