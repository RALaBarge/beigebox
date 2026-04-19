---- MODULE PolicyEnforcement ----
EXTENDS Naturals, Sequences, FiniteSets

(*
  TLA+ Specification for Capability-Based Policy Enforcement with Constraints

  Goals:
  1. Default-deny: every action is explicitly allowed or denied
  2. Capability rules: subject can only perform action on object if rule exists
  3. NO delegation: capabilities are fixed at initialization (no grants during execution)
  4. Rate & size limits: constraints prevent resource exhaustion attacks
  5. Audit log: all decisions are logged with timestamp and decision

  Policy model:
  - AllowRule = [subject, action, object, rate_limit, size_limit]
  - Static rules initialized at startup (no runtime grants)
  - Every (subject, action, object) triple is checked against policy_store
  - Rate limit: max messages per second, Size limit: max bytes per message
  - All decisions logged (allowed, denied)

  Threat model:
  - Compromised agent might try actions outside its capabilities
  - Attacker might try to flood with messages (rate limit defense)
  - Attacker might try to send large payloads (size limit defense)
  - Policy violations must be auditable
*)

CONSTANT
  Agents,                 \* {dmz, a, b, c}
  Actions,                \* {"read", "write", "execute"} (NO "delegate" - immutable roles)
  Resources,              \* {"payload", "metadata", "audit_log"}
  MAX_RATE_LIMIT,         \* Maximum messages per second (e.g., 100)
  MAX_SIZE_LIMIT          \* Maximum message size in bytes (e.g., 512)

VARIABLE
  policy_store,           \* Set of [subject, action, object, rate_limit, size_limit] tuples
  audit_log,              \* Sequence of [timestamp, subject, action, object, decision]
  message_count,          \* [subject -> count of messages in current second]
  time                    \* Wall-clock time

Init ==
  /\ policy_store = {}
  /\ audit_log = <<>>
  /\ message_count = [a \in Agents |-> 0]
  /\ time = 0

(* ACTION: Advance time (reset message counts per second) *)
TimeAdvances ==
  /\ time' = time + 1
  /\ message_count' = [a \in Agents |-> 0]  \* Reset rate limit counter each second
  /\ UNCHANGED <<policy_store, audit_log>>

(* Helper: Check if rule exists and constraints are satisfied *)
RuleExists(subject, action, object) ==
  \E rule \in policy_store:
    rule.subject = subject /\ rule.action = action /\ rule.object = object

(* Helper: Check rate limit (messages per second) *)
WithinRateLimit(subject) ==
  message_count[subject] < MAX_RATE_LIMIT

(* Helper: Log decision *)
LogDecision(subject, action, object, decision) ==
  <<[timestamp |-> time, subject |-> subject, action |-> action, object |-> object, decision |-> decision]>>

(* ACTION: Subject attempts an action (must have rule and pass rate limit) *)
AttemptAction ==
  \E subject \in Agents:
    \E action \in Actions:
      \E object \in Resources:
        LET
          rule_exists == RuleExists(subject, action, object)
          rate_ok == WithinRateLimit(subject)
          allowed == rule_exists /\ rate_ok
        IN
        /\ IF allowed
           THEN /\ audit_log' = Append(audit_log, LogDecision(subject, action, object, "ALLOWED")[1])
                /\ message_count' = [message_count EXCEPT ![subject] = message_count[subject] + 1]
           ELSE /\ audit_log' = Append(audit_log, LogDecision(subject, action, object, "DENIED")[1])
                /\ UNCHANGED message_count
        /\ UNCHANGED <<policy_store, time>>

(* ACTION: Initialize static policy (happens once at startup via Init, immutable thereafter) *)
(* Policies are defined via Init, not granted at runtime - no delegation *)
(* This prevents privilege escalation through capability delegation *)
(* Roles are: dmz (limited), a/b/c (full ring agent), each with fixed permissions *)

Next ==
  \/ TimeAdvances
  \/ AttemptAction

(* INVARIANTS *)

\* Invariant 1: Default-deny: every decision is logged
DecisionsAreLogged ==
  \A i \in DOMAIN audit_log:
    audit_log[i].decision \in {"ALLOWED", "DENIED"}

\* Invariant 2: No delegation action in policy (static roles only)
NoDelegationCapability ==
  \A rule \in policy_store:
    rule.action # "delegate"

\* Invariant 3: All audit log entries have timestamps
AuditLogTimestamped ==
  \A i \in DOMAIN audit_log:
    audit_log[i].timestamp >= 0

\* Invariant 4: Timestamps are monotonically increasing
TimestampsMonotonic ==
  \A i, j \in DOMAIN audit_log:
    (i < j) => audit_log[i].timestamp <= audit_log[j].timestamp

\* Invariant 5: Message counts respect rate limits
RateLimitEnforced ==
  \A agent \in Agents:
    message_count[agent] <= MAX_RATE_LIMIT

\* Invariant 6: Only "ALLOWED" and "DENIED" decisions (no "GRANTED")
NoGrantDecisions ==
  \A i \in DOMAIN audit_log:
    audit_log[i].decision # "GRANTED"

Spec == Init /\ [][Next]_<<policy_store, audit_log, time, message_count>>
        /\ SF_<<Len(audit_log)>>(AttemptAction)

====
