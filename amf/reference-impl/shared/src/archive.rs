use crate::data::{Agent, AuditEntry, Decision, ArchivedMessage};
use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::Path;
use std::sync::{Arc, Mutex};

/// Archive message (what gets stored)
#[derive(Debug, Clone)]
pub struct ArchiveMessage {
    pub timestamp_ms: u64,
    pub agent: Agent,
    pub nonce: u64,
    pub action: String,
    pub data_hmac: Vec<u8>,
    pub sealed_at_ms: u64,
    pub archived_key: String,
}

/// Trait for archive backends
pub trait ArchiveBackend: Send + Sync {
    fn append_audit(&mut self, entry: AuditEntry) -> Result<()>;
    fn append_archived_message(&mut self, msg: ArchiveMessage) -> Result<()>;
    fn get_messages_for_agent(&self, agent: Agent) -> Result<Vec<ArchiveMessage>>;
    fn get_audit_for_agent(&self, agent: Agent) -> Result<Vec<AuditEntry>>;
    fn message_count(&self) -> Result<u64>;
    fn archive_size_mb(&self) -> Result<u64>;
    fn flush(&mut self) -> Result<()>;
}

/// SQLite archive backend (durable, queryable)
pub struct SqliteArchive {
    conn: Arc<Mutex<Connection>>,
}

impl SqliteArchive {
    /// Create or open a SQLite archive
    pub fn new(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;

        // Create tables if they don't exist
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp_ms INTEGER,
                agent TEXT,
                nonce INTEGER,
                action TEXT,
                decision TEXT,
                UNIQUE(agent, nonce)
            );

            CREATE TABLE IF NOT EXISTS archive (
                id INTEGER PRIMARY KEY,
                timestamp_ms INTEGER,
                agent TEXT,
                nonce INTEGER,
                action TEXT,
                data_hmac BLOB,
                sealed_at_ms INTEGER,
                archived_key TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent);
            CREATE INDEX IF NOT EXISTS idx_archive_agent ON archive(agent);
            CREATE INDEX IF NOT EXISTS idx_archive_nonce ON archive(nonce);
        ",
        )?;

        Ok(SqliteArchive {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Get the size of the database file in MB
    fn db_size_mb(&self) -> Result<u64> {
        let conn = self.conn.lock().unwrap();
        let page_count: i64 = conn.query_row("PRAGMA page_count;", [], |row| row.get(0))?;
        let page_size: i64 = conn.query_row("PRAGMA page_size;", [], |row| row.get(0))?;

        let bytes = page_count * page_size;
        Ok((bytes / 1024 / 1024) as u64)
    }
}

impl ArchiveBackend for SqliteArchive {
    fn append_audit(&mut self, entry: AuditEntry) -> Result<()> {
        let decision_str = match entry.decision {
            Decision::ALLOWED => "ALLOWED",
            Decision::DENIED => "DENIED",
        };

        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR IGNORE INTO audit_log (timestamp_ms, agent, nonce, action, decision)
             VALUES (?, ?, ?, ?, ?)",
            params![
                entry.timestamp_ms,
                entry.agent.to_string(),
                entry.nonce,
                entry.action,
                decision_str
            ],
        )?;

        Ok(())
    }

    fn append_archived_message(&mut self, msg: ArchiveMessage) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO archive (timestamp_ms, agent, nonce, action, data_hmac, sealed_at_ms, archived_key)
             VALUES (?, ?, ?, ?, ?, ?, ?)",
            params![
                msg.timestamp_ms,
                msg.agent.to_string(),
                msg.nonce,
                msg.action,
                msg.data_hmac,
                msg.sealed_at_ms,
                msg.archived_key
            ],
        )?;

        Ok(())
    }

    fn get_messages_for_agent(&self, agent: Agent) -> Result<Vec<ArchiveMessage>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT timestamp_ms, agent, nonce, action, data_hmac, sealed_at_ms, archived_key
             FROM archive WHERE agent = ? ORDER BY nonce ASC",
        )?;

        let messages = stmt
            .query_map(params![agent.to_string()], |row| {
                Ok(ArchiveMessage {
                    timestamp_ms: row.get(0)?,
                    agent: match row.get::<_, String>(1)?.as_str() {
                        "ring-a" => Agent::RingA,
                        "ring-b" => Agent::RingB,
                        "ring-c" => Agent::RingC,
                        _ => Agent::DMZ,
                    },
                    nonce: row.get(2)?,
                    action: row.get(3)?,
                    data_hmac: row.get(4)?,
                    sealed_at_ms: row.get(5)?,
                    archived_key: row.get(6)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;

        Ok(messages)
    }

    fn get_audit_for_agent(&self, agent: Agent) -> Result<Vec<AuditEntry>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT timestamp_ms, agent, nonce, action, decision
             FROM audit_log WHERE agent = ? ORDER BY timestamp_ms ASC",
        )?;

        let entries = stmt
            .query_map(params![agent.to_string()], |row| {
                Ok(AuditEntry {
                    timestamp_ms: row.get(0)?,
                    agent: match row.get::<_, String>(1)?.as_str() {
                        "ring-a" => Agent::RingA,
                        "ring-b" => Agent::RingB,
                        "ring-c" => Agent::RingC,
                        _ => Agent::DMZ,
                    },
                    nonce: row.get(2)?,
                    action: row.get(3)?,
                    decision: match row.get::<_, String>(4)?.as_str() {
                        "DENIED" => Decision::DENIED,
                        _ => Decision::ALLOWED,
                    },
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;

        Ok(entries)
    }

    fn message_count(&self) -> Result<u64> {
        let conn = self.conn.lock().unwrap();
        let count: u64 = conn.query_row("SELECT COUNT(*) FROM archive", [], |row| row.get(0))?;
        Ok(count)
    }

    fn archive_size_mb(&self) -> Result<u64> {
        self.db_size_mb()
    }

    fn flush(&mut self) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("PRAGMA optimize;", [])?;
        Ok(())
    }
}

/// In-memory archive (for testing)
pub struct InMemoryArchive {
    audit: Vec<AuditEntry>,
    archived: Vec<ArchiveMessage>,
}

impl InMemoryArchive {
    pub fn new() -> Self {
        InMemoryArchive {
            audit: Vec::new(),
            archived: Vec::new(),
        }
    }
}

impl Default for InMemoryArchive {
    fn default() -> Self {
        Self::new()
    }
}

impl ArchiveBackend for InMemoryArchive {
    fn append_audit(&mut self, entry: AuditEntry) -> Result<()> {
        self.audit.push(entry);
        Ok(())
    }

    fn append_archived_message(&mut self, msg: ArchiveMessage) -> Result<()> {
        self.archived.push(msg);
        Ok(())
    }

    fn get_messages_for_agent(&self, agent: Agent) -> Result<Vec<ArchiveMessage>> {
        Ok(self
            .archived
            .iter()
            .filter(|m| m.agent == agent)
            .cloned()
            .collect())
    }

    fn get_audit_for_agent(&self, agent: Agent) -> Result<Vec<AuditEntry>> {
        Ok(self
            .audit
            .iter()
            .filter(|e| e.agent == agent)
            .cloned()
            .collect())
    }

    fn message_count(&self) -> Result<u64> {
        Ok(self.archived.len() as u64)
    }

    fn archive_size_mb(&self) -> Result<u64> {
        Ok(0) // In-memory, doesn't use disk
    }

    fn flush(&mut self) -> Result<()> {
        Ok(())
    }
}

/// Thread-safe archive wrapper
pub struct Archive {
    backend: Arc<Mutex<Box<dyn ArchiveBackend>>>,
}

impl Archive {
    pub fn new_sqlite(path: &Path) -> Result<Self> {
        let backend = SqliteArchive::new(path)?;
        Ok(Archive {
            backend: Arc::new(Mutex::new(Box::new(backend))),
        })
    }

    pub fn new_inmemory() -> Self {
        Archive {
            backend: Arc::new(Mutex::new(Box::new(InMemoryArchive::new()))),
        }
    }

    pub fn append_audit(&self, entry: AuditEntry) -> Result<()> {
        self.backend.lock().unwrap().append_audit(entry)
    }

    pub fn append_archived_message(&self, msg: ArchiveMessage) -> Result<()> {
        self.backend.lock().unwrap().append_archived_message(msg)
    }

    pub fn get_messages_for_agent(&self, agent: Agent) -> Result<Vec<ArchiveMessage>> {
        self.backend.lock().unwrap().get_messages_for_agent(agent)
    }

    pub fn message_count(&self) -> Result<u64> {
        self.backend.lock().unwrap().message_count()
    }

    pub fn archive_size_mb(&self) -> Result<u64> {
        self.backend.lock().unwrap().archive_size_mb()
    }

    pub fn flush(&self) -> Result<()> {
        self.backend.lock().unwrap().flush()
    }
}
