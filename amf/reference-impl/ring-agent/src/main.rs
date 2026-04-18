use shared::{Agent, RingAgentState, Archive, IPCMessage, ChaChaKey, Message, kdf_derive_session_key, decrypt_chacha, EncryptedPayload};
use anyhow::Result;
use clap::{Parser, ValueEnum};
use std::path::PathBuf;
use std::time::Duration;
use std::os::unix::net::UnixListener;
use std::time::SystemTime;

// Hardcoded APPROVED_IMAGE_HASH for DMZ attestation verification
const APPROVED_IMAGE_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

fn get_now_ms() -> u64 {
    SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Parser)]
#[command(name = "amf-ring-agent")]
#[command(about = "AMF Ring Agent", long_about = None)]
struct Args {
    /// Which ring agent to run (a, b, or c)
    #[arg(value_enum)]
    agent: RingAgentId,

    #[arg(short, long)]
    config_dir: Option<PathBuf>,
}

#[derive(ValueEnum, Clone)]
enum RingAgentId {
    #[value(name = "a")]
    A,
    #[value(name = "b")]
    B,
    #[value(name = "c")]
    C,
}

impl RingAgentId {
    fn to_agent(&self) -> Agent {
        match self {
            RingAgentId::A => Agent::RingA,
            RingAgentId::B => Agent::RingB,
            RingAgentId::C => Agent::RingC,
        }
    }

    fn socket_name(&self) -> String {
        match self {
            RingAgentId::A => "ring_a.sock".to_string(),
            RingAgentId::B => "ring_b.sock".to_string(),
            RingAgentId::C => "ring_c.sock".to_string(),
        }
    }
}

fn main() -> Result<()> {
    let args = Args::parse();
    let agent = args.agent.to_agent();
    let agent_id = args.agent.clone();

    let config_dir = args.config_dir.unwrap_or_else(|| {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".amf")
    });

    std::fs::create_dir_all(&config_dir)?;

    println!("[{}] Starting ring agent with config dir: {:?}", agent, config_dir);

    // Phase 1: Set up listening socket for DMZ connections
    let socket_path = config_dir.join(agent_id.socket_name());
    let _ = std::fs::remove_file(&socket_path);
    let listener = UnixListener::bind(&socket_path)?;
    listener.set_nonblocking(true)?;
    println!("[{}] Listening on Unix socket: {:?}", agent, socket_path);

    // Initialize archive (SQLite)
    let archive_path = config_dir.join("archive.db");
    let archive = Archive::new_sqlite(&archive_path)?;

    // Initialize state machine
    let mut state = RingAgentState::new(agent, archive);
    let mut dmz_stream: Option<std::os::unix::net::UnixStream> = None;

    println!("[{}] Agent initialized, entering event loop", agent);

    // Single-threaded event loop
    loop {
        // Phase 1: Accept DMZ connection (non-blocking)
        if !state.dmz_attestation_verified && dmz_stream.is_none() {
            if let Ok((mut stream, _)) = listener.accept() {
                stream.set_read_timeout(Some(Duration::from_secs(30)))?;
                stream.set_write_timeout(Some(Duration::from_secs(30)))?;

                // Try to read admit_request
                let mut buffer = vec![0; 4096];
                match std::io::Read::read(&mut stream, &mut buffer) {
                    Ok(n) if n > 0 => {
                        let json_str = std::str::from_utf8(&buffer[..n])?;
                        if let Ok(ipc_msg) = serde_json::from_str::<IPCMessage>(json_str) {
                            if ipc_msg.msg_type == "admit_request" {
                                // Parse attestation from payload
                                if let Ok(attestation) = serde_json::from_value::<shared::AttestationReport>(ipc_msg.payload) {
                                    // Verify attestation
                                    let vote = attestation.image_hash == APPROVED_IMAGE_HASH;
                                    println!("[{}] Received admission request, image_hash={}, vote={}", agent, attestation.image_hash, vote);

                                    // Store DMZ pubkey
                                    if let Ok(pubkey_bytes) = hex::decode(&attestation.pubkey) {
                                        state.dmz_pubkey = Some(pubkey_bytes);
                                    }

                                    // Send admit_response
                                    let response = IPCMessage {
                                        msg_type: "admit_response".to_string(),
                                        payload: serde_json::json!({
                                            "agent": agent.to_string(),
                                            "vote": if vote { "yes" } else { "no" }
                                        }),
                                    };
                                    let response_json = serde_json::to_string(&response)?;
                                    std::io::Write::write_all(&mut stream, response_json.as_bytes())?;
                                    std::io::Write::write_all(&mut stream, b"\n")?;

                                    if vote {
                                        state.dmz_attestation_verified = true;
                                        state.session_key_present = true;
                                        dmz_stream = Some(stream);
                                        println!("[{}] DMZ admitted, ready for Phase 2 messages", agent);
                                    }
                                }
                            }
                        }
                    }
                    Ok(_) => println!("[{}] Empty read on socket", agent),
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        // No data yet, continue
                    }
                    Err(e) => println!("[{}] Socket read error: {}", agent, e),
                }
            }
        }

        // Phase 2: Receive messages from DMZ (if admitted)
        let mut should_clear_stream = false;
        if let Some(ref mut stream) = dmz_stream {
            stream.set_nonblocking(true)?;
            let mut buffer = vec![0; 4096];
            match std::io::Read::read(stream, &mut buffer) {
                Ok(n) if n > 0 => {
                    let json_str = std::str::from_utf8(&buffer[..n]).unwrap_or("");
                    if let Ok(ipc_msg) = serde_json::from_str::<IPCMessage>(json_str) {
                        // Derive session key (same as DMZ)
                        let dh_secret = b"hardcoded_dh_secret_for_phase2_testing";
                        let attestation = b"phase2_attestation";
                        if let Ok(session_key) = kdf_derive_session_key(dh_secret, attestation) {
                            match ipc_msg.msg_type.as_str() {
                                "encrypted_message" => {
                                    // Decrypt encrypted message
                                    if let Ok(encrypted) = serde_json::from_value::<EncryptedPayload>(ipc_msg.payload) {
                                        if let Ok(ciphertext_bytes) = hex::decode(&encrypted.ciphertext) {
                                            match decrypt_chacha(&session_key, encrypted.nonce, &ciphertext_bytes) {
                                                Ok(plaintext) => {
                                                    if let Ok(msg_str) = std::str::from_utf8(&plaintext) {
                                                        if let Ok(msg) = serde_json::from_str::<Message>(msg_str) {
                                                            println!("[{}] Received encrypted message: nonce={}, action={}", agent, msg.nonce, msg.action);
                                                            state.enqueue_message(msg);
                                                        }
                                                    }
                                                }
                                                Err(e) => eprintln!("[{}] Decryption failed: {}", agent, e),
                                            }
                                        }
                                    }
                                }
                                "message" => {
                                    // Fallback for unencrypted messages (for backward compatibility)
                                    if let Ok(msg) = serde_json::from_value::<Message>(ipc_msg.payload) {
                                        println!("[{}] Received message: nonce={}, action={}", agent, msg.nonce, msg.action);
                                        state.enqueue_message(msg);
                                    }
                                }
                                _ => {},
                            }
                        }
                    }
                }
                Ok(_) => {},
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {},
                Err(e) => {
                    eprintln!("[{}] Stream error: {}", agent, e);
                    should_clear_stream = true;
                }
            }
            stream.set_nonblocking(false)?;
        }
        if should_clear_stream {
            dmz_stream = None;
            state.dmz_attestation_verified = false;
        }

        // Tick: process one message from queue
        match state.tick() {
            Ok(_processed) => {
                // Write heartbeat every tick
                if let Ok(_hb) = state.heartbeat() {
                    if state.tick_number % 10 == 0 {
                        println!(
                            "[{}] Tick {}: queue_depth={}, violations={}, dmz_verified={}",
                            agent,
                            state.tick_number,
                            state.queue_depth(),
                            state.policy_violations_count,
                            state.dmz_attestation_verified
                        );
                    }
                }
            }
            Err(e) => {
                eprintln!("[{}] Tick error: {}", agent, e);
                state.last_error = Some(e.to_string());
            }
        }

        // Sleep briefly (1ms per tick = 1000 ticks/sec max)
        std::thread::sleep(Duration::from_millis(1));
    }
}
