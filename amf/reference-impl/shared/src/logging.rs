use crate::data::{Agent, Heartbeat};
use anyhow::Result;
use serde_json::json;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Diagnostic logger for per-agent logs
pub struct DiagnosticLogger {
    agent_id: Agent,
    file: Arc<Mutex<File>>,
}

impl DiagnosticLogger {
    pub fn new(agent_id: Agent, log_dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(log_dir)?;

        let filename = log_dir.join(format!("{}.log", agent_id));
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(filename)?;

        Ok(DiagnosticLogger {
            agent_id,
            file: Arc::new(Mutex::new(file)),
        })
    }

    pub fn info(&self, event: &str, context: serde_json::Value) -> Result<()> {
        let log_entry = json!({
            "timestamp_ms": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_millis(),
            "agent": self.agent_id.to_string(),
            "level": "INFO",
            "event": event,
            "context": context
        });

        self.write_log(&log_entry)?;
        Ok(())
    }

    pub fn warn(&self, event: &str, context: serde_json::Value) -> Result<()> {
        let log_entry = json!({
            "timestamp_ms": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_millis(),
            "agent": self.agent_id.to_string(),
            "level": "WARN",
            "event": event,
            "context": context
        });

        self.write_log(&log_entry)?;
        Ok(())
    }

    pub fn error(&self, event: &str, context: serde_json::Value) -> Result<()> {
        let log_entry = json!({
            "timestamp_ms": std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_millis(),
            "agent": self.agent_id.to_string(),
            "level": "ERROR",
            "event": event,
            "context": context
        });

        self.write_log(&log_entry)?;
        Ok(())
    }

    fn write_log(&self, entry: &serde_json::Value) -> Result<()> {
        let mut file = self.file.lock().unwrap();
        writeln!(file, "{}", entry.to_string())?;
        file.flush()?;
        Ok(())
    }
}

/// Heartbeat logger (shared by all agents)
pub struct HeartbeatLogger {
    heartbeat_dir: PathBuf,
}

impl HeartbeatLogger {
    pub fn new(heartbeat_dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(heartbeat_dir)?;
        Ok(HeartbeatLogger {
            heartbeat_dir: heartbeat_dir.to_path_buf(),
        })
    }

    pub fn write_heartbeat(&self, heartbeat: &Heartbeat) -> Result<()> {
        let filename = self.heartbeat_dir.join(format!(
            "{}.{}.json",
            heartbeat.agent, heartbeat.timestamp_ms
        ));

        let json = serde_json::to_string(heartbeat)?;
        std::fs::write(filename, json)?;
        Ok(())
    }

    pub fn read_heartbeats_at_timestamp(&self, timestamp_ms: u64) -> Result<Vec<Heartbeat>> {
        let mut heartbeats = Vec::new();

        for entry in std::fs::read_dir(&self.heartbeat_dir)? {
            let entry = entry?;
            let path = entry.path();

            if let Some(filename) = path.file_name() {
                if let Some(name_str) = filename.to_str() {
                    if name_str.ends_with(&format!(".{}.json", timestamp_ms)) {
                        let json_str = std::fs::read_to_string(&path)?;
                        let hb: Heartbeat = serde_json::from_str(&json_str)?;
                        heartbeats.push(hb);
                    }
                }
            }
        }

        Ok(heartbeats)
    }
}

/// Initialize logging directories
pub fn init_logging(home_dir: &Path) -> Result<(DiagnosticLogger, DiagnosticLogger, DiagnosticLogger, DiagnosticLogger, HeartbeatLogger)> {
    let log_dir = home_dir.join("logs");
    let heartbeat_dir = home_dir.join("heartbeat");

    let dmz_log = DiagnosticLogger::new(Agent::DMZ, &log_dir)?;
    let ring_a_log = DiagnosticLogger::new(Agent::RingA, &log_dir)?;
    let ring_b_log = DiagnosticLogger::new(Agent::RingB, &log_dir)?;
    let ring_c_log = DiagnosticLogger::new(Agent::RingC, &log_dir)?;
    let hb_log = HeartbeatLogger::new(&heartbeat_dir)?;

    Ok((dmz_log, ring_a_log, ring_b_log, ring_c_log, hb_log))
}
