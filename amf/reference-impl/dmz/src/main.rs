use shared::{Ed25519Key, IPCMessage, AttestationReport, Message, Agent, ChaChaKey, kdf_derive_session_key, encrypt_chacha, EncryptedPayload};
use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::os::unix::net::UnixStream;
use std::io::{Read, Write};
use std::time::Duration;
use std::collections::HashMap;

const IMAGE_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Parser)]
#[command(name = "amf-dmz")]
#[command(about = "AMF DMZ Gateway Agent", long_about = None)]
struct Args {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the DMZ agent
    Start {
        #[arg(short, long)]
        config_dir: Option<PathBuf>,
    },
    /// Trigger a respawn
    Respawn {
        #[arg(short, long)]
        config_dir: Option<PathBuf>,
    },
    /// Show status
    Status {
        #[arg(short, long)]
        config_dir: Option<PathBuf>,
    },
}

fn get_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn main() -> Result<()> {
    let args = Args::parse();

    match args.command {
        Commands::Start { config_dir } => {
            let config_dir = config_dir.unwrap_or_else(|| {
                dirs::home_dir()
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join(".amf")
            });
            std::fs::create_dir_all(&config_dir)?;
            start_dmz(&config_dir)?;
            Ok(())
        }
        Commands::Respawn { config_dir } => {
            let config_dir = config_dir.unwrap_or_else(|| {
                dirs::home_dir()
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join(".amf")
            });
            respawn_dmz(&config_dir)?;
            Ok(())
        }
        Commands::Status { config_dir: _ } => {
            println!("[DMZ] Status check not yet implemented");
            Ok(())
        }
    }
}

fn start_dmz(config_dir: &PathBuf) -> Result<()> {
    println!("[DMZ] Starting agent with config dir: {:?}", config_dir);

    // Phase 1: Generate or load Ed25519 keypair
    let key_path = config_dir.join("dmz_keypair.bin");
    let key = if key_path.exists() {
        let key_bytes = std::fs::read(&key_path)?;
        let key_array: [u8; 32] = key_bytes.try_into()
            .map_err(|_| anyhow::anyhow!("Invalid key size"))?;
        Ed25519Key::from_bytes(&key_array)?
    } else {
        let key = Ed25519Key::generate();
        std::fs::write(&key_path, key.to_bytes())?;
        key
    };

    let pubkey_hex = hex::encode(key.public_key());
    println!("[DMZ] Using public key: {}", pubkey_hex);

    // Create attestation report
    let attestation = AttestationReport {
        timestamp_ms: get_now_ms(),
        image_hash: IMAGE_HASH.to_string(),
        pubkey: pubkey_hex.clone(),
    };

    // Connect to each ring agent and perform Phase 1 handshake
    let ring_agents = vec![
        ("a", "ring_a.sock"),
        ("b", "ring_b.sock"),
        ("c", "ring_c.sock"),
    ];

    let mut streams: HashMap<String, UnixStream> = HashMap::new();
    let mut votes: HashMap<String, bool> = HashMap::new();
    let mut connected_agents = 0;

    for (name, socket_name) in ring_agents {
        let socket_path = config_dir.join(socket_name);

        // Try to connect with retries
        let mut stream = None;
        for attempt in 0..10 {
            match UnixStream::connect(&socket_path) {
                Ok(s) => {
                    stream = Some(s);
                    println!("[DMZ] Connected to ring-{} on attempt {}", name, attempt + 1);
                    break;
                }
                Err(_) if attempt < 9 => {
                    if attempt == 0 {
                        eprint!("[DMZ] Waiting for ring-{} to be ready...", name);
                    }
                    std::thread::sleep(Duration::from_millis(200));
                }
                Err(e) => {
                    eprintln!("\n[DMZ] Failed to connect to ring-{}: {}", name, e);
                    break;
                }
            }
        }

        if let Some(mut stream) = stream {
            stream.set_read_timeout(Some(Duration::from_secs(30)))?;
            stream.set_write_timeout(Some(Duration::from_secs(30)))?;

            // Send admit_request with attestation
            let admit_msg = IPCMessage {
                msg_type: "admit_request".to_string(),
                payload: serde_json::to_value(&attestation)?,
            };

            let json = serde_json::to_string(&admit_msg)?;
            stream.write_all(json.as_bytes())?;
            stream.write_all(b"\n")?;
            println!("[DMZ] Sent admission request to ring-{}", name);

            // Read admit_response
            let mut buffer = vec![0; 4096];
            match stream.read(&mut buffer) {
                Ok(n) if n > 0 => {
                    let json_str = std::str::from_utf8(&buffer[..n])?;
                    if let Ok(ipc_msg) = serde_json::from_str::<IPCMessage>(json_str) {
                        if ipc_msg.msg_type == "admit_response" {
                            let vote = ipc_msg
                                .payload
                                .get("vote")
                                .and_then(|v| v.as_str())
                                .unwrap_or("no")
                                == "yes";
                            votes.insert(name.to_string(), vote);
                            connected_agents += 1;
                            println!("[DMZ] Received admission response from ring-{}: vote={}", name, vote);

                            if vote {
                                streams.insert(name.to_string(), stream);
                            }
                        }
                    }
                }
                Ok(_) => println!("[DMZ] Empty read from ring-{}", name),
                Err(e) => eprintln!("[DMZ] Failed to read from ring-{}: {}", name, e),
            }
        }
    }

    // Check if we have quorum (3 votes) and all positive
    let yes_votes = votes.values().filter(|&&v| v).count();
    println!("[DMZ] Admission votes: {} yes out of {} connected agents", yes_votes, connected_agents);

    if yes_votes == 3 {
        println!("[DMZ] Phase 1 handshake complete! DMZ is admitted.");
        println!("[DMZ] Ready for Phase 2 (message encryption)");

        // Phase 2: Send test messages through established connections
        phase2_send_messages(&streams)?;
    } else {
        println!("[DMZ] Failed to get quorum. Exiting.");
    }

    Ok(())
}

fn phase2_send_messages(streams: &HashMap<String, UnixStream>) -> Result<()> {
    println!("[DMZ] Phase 2: Sending encrypted messages");

    // Derive session key from a hardcoded DH exchange result
    let dh_secret = b"hardcoded_dh_secret_for_phase2_testing";
    let attestation = b"phase2_attestation";
    let session_key = kdf_derive_session_key(dh_secret, attestation)?;

    for (name, stream) in streams {
        // Create a test message
        let msg = Message::new(
            get_now_ms(),
            Agent::DMZ,
            1,
            "test_action".to_string(),
            "secret payload - encrypted with chacha20poly1305".to_string(),
            vec![0; 64], // dummy signature
        );

        // Serialize message and encrypt with ChaCha20-Poly1305
        let msg_json = serde_json::to_string(&msg)?;
        let ciphertext = encrypt_chacha(&session_key, 1, msg_json.as_bytes())?;
        let ciphertext_hex = hex::encode(&ciphertext);

        // Create encrypted payload message
        let encrypted_payload = EncryptedPayload {
            nonce: 1,
            ciphertext: ciphertext_hex,
        };

        let msg_ipc = IPCMessage {
            msg_type: "encrypted_message".to_string(),
            payload: serde_json::to_value(&encrypted_payload)?,
        };

        let json = serde_json::to_string(&msg_ipc)?;
        let _ = std::io::Write::write_all(&mut stream.try_clone()?, json.as_bytes());
        let _ = std::io::Write::write_all(&mut stream.try_clone()?, b"\n");

        println!("[DMZ] Sent encrypted message to ring-{}", name);
    }

    println!("[DMZ] Phase 2 complete - encrypted messages sent");
    Ok(())
}

fn respawn_dmz(config_dir: &PathBuf) -> Result<()> {
    println!("[DMZ] Respawning (reset crypto state)");
    let key_path = config_dir.join("dmz_keypair.bin");
    if key_path.exists() {
        std::fs::remove_file(&key_path)?;
        println!("[DMZ] Removed old keypair, will generate new one on next start");
    }
    Ok(())
}
