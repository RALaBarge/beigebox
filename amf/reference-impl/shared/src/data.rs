use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Agent {
    DMZ,
    RingA,
    RingB,
    RingC,
}

impl fmt::Display for Agent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Agent::DMZ => write!(f, "dmz"),
            Agent::RingA => write!(f, "ring-a"),
            Agent::RingB => write!(f, "ring-b"),
            Agent::RingC => write!(f, "ring-c"),
        }
    }
}

impl Agent {
    pub fn all() -> Vec<Agent> {
        vec![Agent::DMZ, Agent::RingA, Agent::RingB, Agent::RingC]
    }

    pub fn ring_agents() -> Vec<Agent> {
        vec![Agent::RingA, Agent::RingB, Agent::RingC]
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Decision {
    ALLOWED,
    DENIED,
}

impl fmt::Display for Decision {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Decision::ALLOWED => write!(f, "ALLOWED"),
            Decision::DENIED => write!(f, "DENIED"),
        }
    }
}

/// Message sent between agents
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub timestamp_ms: u64,
    pub agent: Agent,
    pub nonce: u64,
    pub action: String,
    pub data: String,
    pub signature: Vec<u8>,
}

impl Message {
    pub fn new(
        timestamp_ms: u64,
        agent: Agent,
        nonce: u64,
        action: String,
        data: String,
        signature: Vec<u8>,
    ) -> Self {
        Message {
            timestamp_ms,
            agent,
            nonce,
            action,
            data,
            signature,
        }
    }
}

/// Message that has been archived and key destroyed
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArchivedMessage {
    pub timestamp_ms: u64,
    pub agent: Agent,
    pub nonce: u64,
    pub action: String,
    pub data_hmac: Vec<u8>,  // HMAC of original data (for integrity, not confidentiality)
    pub sealed_at_ms: u64,
    pub archived_key: String,  // "DESTROYED" (key is not stored)
}

impl ArchivedMessage {
    pub fn from_message(msg: Message, sealed_at_ms: u64) -> Self {
        ArchivedMessage {
            timestamp_ms: msg.timestamp_ms,
            agent: msg.agent,
            nonce: msg.nonce,
            action: msg.action,
            data_hmac: vec![],  // Would be computed from original data
            sealed_at_ms,
            archived_key: "DESTROYED".to_string(),
        }
    }
}

/// Audit log entry (immutable, security/compliance)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub timestamp_ms: u64,
    pub agent: Agent,
    pub nonce: u64,
    pub action: String,
    pub decision: Decision,
}

impl AuditEntry {
    pub fn new(timestamp_ms: u64, agent: Agent, nonce: u64, action: String, decision: Decision) -> Self {
        AuditEntry {
            timestamp_ms,
            agent,
            nonce,
            action,
            decision,
        }
    }
}

/// Policy rule: allow agent to perform action on object
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct AllowRule {
    pub subject: Agent,
    pub action: String,
    pub object: Agent,
}

impl AllowRule {
    pub fn new(subject: Agent, action: String, object: Agent) -> Self {
        AllowRule {
            subject,
            action,
            object,
        }
    }
}

/// Heartbeat snapshot (system state, per agent, per second)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Heartbeat {
    pub timestamp_ms: u64,
    pub agent: Agent,
    pub tick_number: u64,
    pub status: String,
    pub nonce_counter: u64,
    pub session_key_present: bool,
    pub queue_depth: usize,
    pub messages_processed_this_tick: usize,
    pub mem_mb: u64,
    pub cpu_percent: u8,
    pub disk_available_mb: u64,
    pub archive_size_mb: u64,
    pub archive_message_count: u64,
    pub last_dmz_nonce_seen: u64,
    pub last_dmz_nonce_timestamp_ms: u64,
    pub policy_violations_count: u64,
    pub last_error: Option<String>,
}

impl Heartbeat {
    pub fn new(timestamp_ms: u64, agent: Agent) -> Self {
        Heartbeat {
            timestamp_ms,
            agent,
            tick_number: 0,
            status: "initializing".to_string(),
            nonce_counter: 0,
            session_key_present: false,
            queue_depth: 0,
            messages_processed_this_tick: 0,
            mem_mb: 0,
            cpu_percent: 0,
            disk_available_mb: 0,
            archive_size_mb: 0,
            archive_message_count: 0,
            last_dmz_nonce_seen: 0,
            last_dmz_nonce_timestamp_ms: 0,
            policy_violations_count: 0,
            last_error: None,
        }
    }
}

/// Attestation report from TEE (mock: no TEE in reference impl)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttestationReport {
    pub timestamp_ms: u64,
    pub image_hash: String,  // expected APPROVED_IMAGE_HASH
    pub pubkey: String,       // hex-encoded Ed25519 public key
}

/// DH exchange message for session key derivation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DHExchange {
    pub pubkey: String,  // hex-encoded Ed25519 public key (used as DH identifier)
}

/// Encrypted message payload (Phase 2)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptedPayload {
    pub nonce: u64,           // Nonce for nonce-based encryption
    pub ciphertext: String,   // hex-encoded ChaCha20-Poly1305 ciphertext
}
