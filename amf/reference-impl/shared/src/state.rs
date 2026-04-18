use crate::data::{Agent, Message, AuditEntry, Decision, Heartbeat};
use crate::archive::Archive;
use crate::policy::PolicyStore;
use std::collections::VecDeque;
use std::time::{SystemTime, UNIX_EPOCH};

/// Ring agent state machine
pub struct RingAgentState {
    pub agent_id: Agent,
    pub tick_number: u64,
    pub nonce_counter: u64,
    pub session_key_present: bool,
    pub message_queue: VecDeque<Message>,
    pub policy: PolicyStore,
    pub archive: Archive,
    pub last_dmz_nonce_seen: u64,
    pub last_dmz_nonce_timestamp_ms: u64,
    pub policy_violations_count: u64,
    pub last_error: Option<String>,
    // Phase 1: Handshake state
    pub dmz_attestation_verified: bool,
    pub dmz_pubkey: Option<Vec<u8>>,  // DMZ Ed25519 public key (Phase 1)
}

impl RingAgentState {
    pub fn new(agent_id: Agent, archive: Archive) -> Self {
        RingAgentState {
            agent_id,
            tick_number: 0,
            nonce_counter: 0,
            session_key_present: false,
            message_queue: VecDeque::new(),
            policy: PolicyStore::new(),
            archive,
            last_dmz_nonce_seen: 0,
            last_dmz_nonce_timestamp_ms: 0,
            policy_violations_count: 0,
            last_error: None,
            dmz_attestation_verified: false,
            dmz_pubkey: None,
        }
    }

    /// Process one tick: dequeue message, evaluate policy, archive, log
    pub fn tick(&mut self) -> anyhow::Result<usize> {
        let mut processed = 0;

        // Process up to 1 message per tick (fairness)
        if let Some(msg) = self.message_queue.pop_front() {
            // Check nonce freshness
            if msg.nonce <= self.last_dmz_nonce_seen {
                // Replay or out-of-order, reject silently
                self.last_error = Some(format!("Replay detected: nonce {} <= {}", msg.nonce, self.last_dmz_nonce_seen));
                self.policy_violations_count += 1;

                let audit = AuditEntry::new(
                    now_ms(),
                    msg.agent,
                    msg.nonce,
                    msg.action.clone(),
                    Decision::DENIED,
                );
                self.archive.append_audit(audit)?;
                return Ok(0);
            }

            // Evaluate policy
            let decision = self.policy.evaluate(msg.agent, &msg.action, self.agent_id);

            // Log to audit
            let audit = AuditEntry::new(
                now_ms(),
                msg.agent,
                msg.nonce,
                msg.action.clone(),
                decision,
            );
            self.archive.append_audit(audit)?;

            // Update tracking
            if msg.agent == Agent::DMZ {
                self.last_dmz_nonce_seen = msg.nonce;
                self.last_dmz_nonce_timestamp_ms = now_ms();
            }

            if decision == Decision::DENIED {
                self.policy_violations_count += 1;
            }

            processed = 1;
        }

        self.tick_number += 1;
        Ok(processed)
    }

    /// Get current heartbeat snapshot
    pub fn heartbeat(&self) -> anyhow::Result<Heartbeat> {
        let mut hb = Heartbeat::new(now_ms(), self.agent_id);
        hb.tick_number = self.tick_number;
        hb.status = "active".to_string();
        hb.nonce_counter = self.nonce_counter;
        hb.session_key_present = self.session_key_present;
        hb.queue_depth = self.message_queue.len();
        hb.last_dmz_nonce_seen = self.last_dmz_nonce_seen;
        hb.last_dmz_nonce_timestamp_ms = self.last_dmz_nonce_timestamp_ms;
        hb.policy_violations_count = self.policy_violations_count;
        hb.last_error = self.last_error.clone();
        hb.archive_message_count = self.archive.message_count()?;
        hb.archive_size_mb = self.archive.archive_size_mb()?;

        Ok(hb)
    }

    /// Enqueue a message
    pub fn enqueue_message(&mut self, msg: Message) {
        self.message_queue.push_back(msg);
    }

    /// Get queue depth
    pub fn queue_depth(&self) -> usize {
        self.message_queue.len()
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
